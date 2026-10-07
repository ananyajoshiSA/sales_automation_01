"""Quick read-only checks against LeadSquared.

    python -m integrations.leadsquared check
    python -m integrations.leadsquared fields
    python -m integrations.leadsquared activity-types
    python -m integrations.leadsquared users
    python -m integrations.leadsquared lead-by-email someone@example.com
    python -m integrations.leadsquared leads-by-group "Team Elite Calling" exports/elite.csv
"""

import csv
import json
import os
import sys

from .client import LeadSquaredClient, LeadSquaredError


EXPORT_COLUMNS = [
    "ProspectID", "FirstName", "LastName", "EmailAddress", "Phone", "OwnerIdName",
    "ProspectStage", "Source", "mx_Enquired_Course", "ProspectActivityName_Max",
    "ProspectActivityDate_Max", "CreatedOn", "ModifiedOn",
]


def export_group_leads(client: LeadSquaredClient, group: str, out_path: str) -> None:
    users = client.get_users_in_group(group)
    if not users:
        raise LeadSquaredError(f"No users found in group {group!r}")
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    total = 0
    with open(out_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=EXPORT_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for user in users:
            n = 0
            for lead in client.iter_leads("OwnerId", user["ID"], page_size=1000, columns=EXPORT_COLUMNS):
                writer.writerow(lead)
                n += 1
            total += n
            print(f"{user.get('FirstName', '')} {user.get('LastName', '')}: {n}", file=sys.stderr)
    print(f"{total} leads from {len(users)} users in {group!r} -> {out_path}")


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 1
    cmd, args = argv[0], argv[1:]
    try:
        client = LeadSquaredClient()
        if cmd == "check":
            fields = client.get_lead_metadata()
            print(f"OK: connected to {client.host} ({len(fields)} lead fields)")
        elif cmd == "fields":
            for f in client.get_lead_metadata():
                print(f"{f.get('SchemaName'):45} {f.get('DataType', ''):15} {f.get('DisplayName', '')}")
        elif cmd == "activity-types":
            for a in client.get_activity_types():
                print(f"{a.get('ActivityEvent')!s:8} {a.get('DisplayName')}")
        elif cmd == "users":
            for u in client.get_users():
                print(f"{u.get('ID')}  {u.get('FirstName', '')} {u.get('LastName', '')}  {u.get('EmailAddress', '')}")
        elif cmd == "leads-by-group" and len(args) == 2:
            export_group_leads(client, *args)
        elif cmd == "lead-by-email" and args:
            print(json.dumps(client.get_lead_by_email(args[0]), indent=2))
        else:
            print(__doc__)
            return 1
    except LeadSquaredError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
