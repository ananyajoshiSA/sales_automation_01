import { beforeEach, describe, expect, it } from "vitest";
import { NOT_PROTECTED, resetAccessCache, verifyAccess } from "../src/access";

const TEAM = "acme.cloudflareaccess.com";
const AUD = "aud-tag-123";
const env = { ACCESS_TEAM_DOMAIN: TEAM, ACCESS_AUD: AUD };
const now = Date.parse("2026-10-09T07:40:00Z");

const b64 = (b: Uint8Array) => btoa(String.fromCharCode(...b)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
const enc = (o: unknown) => b64(new TextEncoder().encode(JSON.stringify(o)));

async function keyPair() {
  return crypto.subtle.generateKey({ name: "RSASSA-PKCS1-v1_5", modulusLength: 2048, publicExponent: new Uint8Array([1, 0, 1]),
    hash: "SHA-256" }, true, ["sign", "verify"]) as Promise<CryptoKeyPair>;
}

async function jwt(key: CryptoKey, claims: Record<string, unknown>, kid = "k1") {
  const head = `${enc({ alg: "RS256", kid, typ: "JWT" })}.${enc(claims)}`;
  const sig = new Uint8Array(await crypto.subtle.sign("RSASSA-PKCS1-v1_5", key, new TextEncoder().encode(head)));
  return `${head}.${b64(sig)}`;
}

const good = { aud: [AUD], email: "lead@example.com", iss: `https://${TEAM}`, exp: now / 1000 + 600, nbf: now / 1000 - 60 };
const req = (token?: string) => new Request("https://x.workers.dev/api/summary", token ? { headers: { "Cf-Access-Jwt-Assertion": token } } : {});

let pair: CryptoKeyPair;
let fetches: string[];
let fetcher: (url: string) => Promise<Response>;

beforeEach(async () => {
  resetAccessCache();
  pair ??= await keyPair();
  const jwk = { ...(await crypto.subtle.exportKey("jwk", pair.publicKey)), kid: "k1" };
  fetches = [];
  fetcher = async (url) => { fetches.push(url); return new Response(JSON.stringify({ keys: [jwk] })); };
});

describe("verifyAccess", () => {
  it("accepts a valid token and fetches the keys once", async () => {
    const t = await jwt(pair.privateKey, { ...good, exp: now / 1000 + 3 * 3600 });
    expect(await verifyAccess(req(t), env, now, fetcher)).toEqual({ ok: true, email: "lead@example.com" });
    expect(await verifyAccess(req(t), env, now + 30 * 60_000, fetcher)).toMatchObject({ ok: true });
    expect(fetches).toEqual([`https://${TEAM}/cdn-cgi/access/certs`]);
    await verifyAccess(req(t), env, now + 61 * 60_000, fetcher);
    expect(fetches).toHaveLength(2);   // refreshed after an hour
  });

  it("rejects wrong audience, expired, wrong issuer and bad signature", async () => {
    const other = await keyPair();
    const cases = [
      [await jwt(pair.privateKey, { ...good, aud: ["someone-else"] }), "wrong audience"],
      [await jwt(pair.privateKey, { ...good, exp: now / 1000 - 3600 }), "expired"],
      [await jwt(pair.privateKey, { ...good, nbf: now / 1000 + 3600 }), "not yet valid"],
      [await jwt(pair.privateKey, { ...good, iss: "https://evil.cloudflareaccess.com" }), "wrong issuer"],
      [await jwt(other.privateKey, good), "bad signature"],
      ["not.a.jwt", "malformed token"],
    ] as const;
    for (const [token, why] of cases) {
      const r = await verifyAccess(req(token), env, now, fetcher);
      expect(r).toMatchObject({ ok: false, status: 403 });
      expect(r.ok ? "" : r.error).toContain(why);
    }
  });

  it("rejects a request with no token", async () => {
    expect(await verifyAccess(req(), env, now, fetcher)).toMatchObject({ ok: false, status: 403 });
    expect(fetches).toEqual([]);
  });

  it("refuses everything until Access is configured", async () => {
    const t = await jwt(pair.privateKey, good);
    for (const e of [{}, { ACCESS_TEAM_DOMAIN: TEAM, ACCESS_AUD: "" }, { ACCESS_TEAM_DOMAIN: " ", ACCESS_AUD: AUD }]) {
      expect(await verifyAccess(req(t), e, now, fetcher)).toEqual({ ok: false, status: 403, error: NOT_PROTECTED });
    }
    expect(fetches).toEqual([]);
  });

  it("reports unreachable keys as 503 and does not cache the failure", async () => {
    const t = await jwt(pair.privateKey, good);
    expect(await verifyAccess(req(t), env, now, async () => new Response("", { status: 500 }))).toMatchObject({ ok: false, status: 503 });
    expect(await verifyAccess(req(t), env, now, fetcher)).toMatchObject({ ok: true });
  });
});
