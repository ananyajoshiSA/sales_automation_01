"""Fetch full activity history for a list of lead IDs into a JSONL file (resumable).

    python scripts/fetch_lead_histories.py ids.json out.jsonl [threads]
"""
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor

from integrations.leadsquared import LeadSquaredClient, LeadSquaredError


def main(ids_path, out_path, threads=5):
    ids = json.load(open(ids_path))
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
            except LeadSquaredError as e:
                return lid, None, str(e)
            acts = r.get("ProspectActivities") or []
            out += acts
            if len(acts) < 100:
                return lid, out, None
            off += 100

    t0 = time.time()
    with open(out_path, "a") as fh, ThreadPoolExecutor(int(threads)) as ex:
        for n, (lid, acts, err) in enumerate(ex.map(get, todo), 1):
            fh.write(json.dumps({"lead_id": lid, "activities": acts, "error": err}) + "\n")
            if n % 250 == 0:
                fh.flush()
                rate = n / (time.time() - t0)
                print(f"{n}/{len(todo)} ({rate:.1f}/s, ~{(len(todo) - n) / rate / 60:.0f} min left)", flush=True)
    print("done", flush=True)


if __name__ == "__main__":
    main(*sys.argv[1:])
