// Cloudflare Access check inside the Worker: defense in depth behind the Access policy on the
// hostname, and fail-closed, so the API never answers with data before Access is set up.
// Verifies the Cf-Access-Jwt-Assertion JWT (RS256) against the team's public keys, which are
// fetched at most once an hour per isolate (one subrequest).

export interface AccessEnv { ACCESS_TEAM_DOMAIN?: string; ACCESS_AUD?: string }

export type AccessResult = { ok: true; email: string } | { ok: false; status: 403 | 503; error: string };

export const NOT_PROTECTED = "Dashboard not yet protected: finish Cloudflare Access setup";
const CERTS_TTL_MS = 60 * 60_000;
const CLOCK_SKEW_S = 60;

type Fetcher = (url: string) => Promise<Response>;
interface Jwk extends JsonWebKey { kid?: string }
let certs: { url: string; at: number; keys: Map<string, CryptoKey> } | null = null;

/** Test hook: forget the cached keys. */
export function resetAccessCache(): void { certs = null; }

export function teamOrigin(domain: string): string {
  return "https://" + domain.trim().replace(/^https?:\/\//, "").replace(/\/+$/, "");
}

function b64url(s: string): Uint8Array<ArrayBuffer> {
  const bin = atob(s.replace(/-/g, "+").replace(/_/g, "/") + "===".slice((s.length + 3) % 4));
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

const decodeJson = (s: string): Record<string, any> => JSON.parse(new TextDecoder().decode(b64url(s)));

async function teamKeys(origin: string, nowMs: number, fetcher: Fetcher): Promise<Map<string, CryptoKey>> {
  const url = `${origin}/cdn-cgi/access/certs`;
  if (certs && certs.url === url && nowMs - certs.at < CERTS_TTL_MS) return certs.keys;
  const resp = await fetcher(url);
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  const body = (await resp.json()) as { keys?: Jwk[] };
  const keys = new Map<string, CryptoKey>();
  for (const k of body.keys ?? []) {
    if (k.kty !== "RSA") continue;
    keys.set(k.kid ?? "", await crypto.subtle.importKey("jwk", k, { name: "RSASSA-PKCS1-v1_5", hash: "SHA-256" }, false, ["verify"]));
  }
  certs = { url, at: nowMs, keys };
  return keys;
}

export async function verifyAccess(req: Request, env: AccessEnv, nowMs = Date.now(),
                                   fetcher: Fetcher = (u) => fetch(u)): Promise<AccessResult> {
  const domain = (env.ACCESS_TEAM_DOMAIN ?? "").trim(), aud = (env.ACCESS_AUD ?? "").trim();
  if (!domain || !aud) return { ok: false, status: 403, error: NOT_PROTECTED };
  const deny = (why: string): AccessResult => ({ ok: false, status: 403, error: `Access login required (${why})` });
  const token = req.headers.get("Cf-Access-Jwt-Assertion");
  if (!token) return deny("no Access token");
  const parts = token.split(".");
  if (parts.length !== 3) return deny("malformed token");
  let header: Record<string, any>, claims: Record<string, any>;
  try { header = decodeJson(parts[0]); claims = decodeJson(parts[1]); } catch { return deny("malformed token"); }
  if (header.alg !== "RS256") return deny("unexpected algorithm");

  const origin = teamOrigin(domain);
  let keys: Map<string, CryptoKey>;
  try { keys = await teamKeys(origin, nowMs, fetcher); } catch (err) {
    return { ok: false, status: 503, error: `Could not load Access keys: ${err instanceof Error ? err.message : err}` };
  }
  const candidates = header.kid !== undefined && keys.has(header.kid) ? [keys.get(header.kid)!] : [...keys.values()];
  let sig: Uint8Array<ArrayBuffer>;
  try { sig = b64url(parts[2]); } catch { return deny("malformed token"); }
  const signed = new TextEncoder().encode(`${parts[0]}.${parts[1]}`);
  let valid = false;
  for (const k of candidates) {
    if (await crypto.subtle.verify("RSASSA-PKCS1-v1_5", k, sig, signed)) { valid = true; break; }
  }
  if (!valid) return deny("bad signature");

  const now = nowMs / 1000;
  if (typeof claims.exp !== "number" || claims.exp + CLOCK_SKEW_S < now) return deny("expired");
  if (typeof claims.nbf === "number" && claims.nbf - CLOCK_SKEW_S > now) return deny("not yet valid");
  if (claims.iss !== origin) return deny("wrong issuer");
  const auds = Array.isArray(claims.aud) ? claims.aud : [claims.aud];
  if (!auds.includes(aud)) return deny("wrong audience");
  return { ok: true, email: String(claims.email ?? "") };
}
