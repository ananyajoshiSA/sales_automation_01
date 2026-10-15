"""Fetch full activity history for a list of lead IDs into a JSONL file (resumable).

Each lead is fetched once (repeated IDs are dropped). If the shared LeadSquared gate defers (a request budget
is used up), the run stops with exit code 75 and says when to try again; rerunning the same command resumes.
Failed leads are kept as errors and named in a closing warning (exit code 1 from the command line); rerunning
retries them. ``main`` returns how many failed, so callers can say so too.

    python scripts/fetch_lead_histories.py ids.json out.jsonl [threads]
"""
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor

from integrations.leadsquared import AdmissionDeferred, LeadSquaredClient, LeadSquaredError
from integrations.timeutil import IST

DEFERRED_EXIT = 75  # EX_TEMPFAIL: try again later


def main(ids_path, out_path, threads=5):
    ids = list(dict.fromkeys(json.load(open(ids_path))))
    done = set()
    if os.path.exists(out_path):
        for line in open(out_path):
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if d.get("activities") is not None and not d.get("error"):  # errored leads are retried
                done.add(d["lead_id"])
    todo = [i for i in ids if i not in done]
    print(f"{len(ids)} leads, {len(done)} already fetched, {len(todo)} to go", flush=True)
    c = LeadSquaredClient()

    def get(lid):
        out, off = [], 0
        while True:
            try:
                r = c.request("POST", "ProspectActivity.svc/Retrieve", params={"leadId": lid},
                              json={"Paging": {"Offset": off, "RowCount": 100}})
            except AdmissionDeferred:
                raise                      # stop the run: nothing was sent, the lead stays to do
            except LeadSquaredError as e:
                return lid, None, str(e)
            acts = r.get("ProspectActivities") or []
            out += acts
            if len(acts) < 100:
                return lid, out, None
            off += 100

    t0 = time.time()
    written = failed = 0
    deferred = None
    with open(out_path, "a") as fh, ThreadPoolExecutor(int(threads)) as ex:
        try:
            for n, (lid, acts, err) in enumerate(ex.map(get, todo), 1):
                fh.write(json.dumps({"lead_id": lid, "activities": acts, "error": err}) + "\n")
                written, failed = n, failed + (err is not None)
                if n % 250 == 0:
                    fh.flush()
                    rate = n / (time.time() - t0)
                    print(f"{n}/{len(todo)} ({rate:.1f}/s, ~{(len(todo) - n) / rate / 60:.0f} min left)", flush=True)
        except AdmissionDeferred as e:
            deferred = e
            ex.shutdown(wait=True, cancel_futures=True)
    if deferred is not None:
        when = deferred.next_eligible.astimezone(IST).strftime("%d %b %H:%M IST") if deferred.next_eligible else "later"
        print(f"Stopped, not finished: {deferred.reason}. {written} of {len(todo)} leads written this run; "
              f"rerun the same command after {when} to resume.", file=sys.stderr, flush=True)
        raise SystemExit(DEFERRED_EXIT)
    if failed:
        print(f"Not complete: {failed} of {written} leads failed (kept as errors); rerun the same command to retry them.",
              file=sys.stderr, flush=True)
    else:
        print(f"done: {written} leads", flush=True)
    return failed


if __name__ == "__main__":
    sys.exit(1 if main(*sys.argv[1:]) else 0)
