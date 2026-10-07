"""Quick read-only checks against LeadSquared.

    python -m integrations.leadsquared check
    python -m integrations.leadsquared fields
    python -m integrations.leadsquared activity-types
    python -m integrations.leadsquared users
    python -m integrations.leadsquared lead-by-email someone@example.com
"""

import json
import sys

from .client import LeadSquaredClient, LeadSquaredError


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
