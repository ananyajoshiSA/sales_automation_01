"""Tomorrow's call plan for every team, found from LeadSquared's user groups (plan step 8, G2).

Lists the account's users, takes each caller's team with ``team_of`` (calling-software groups are
skipped), and runs ``analytics.nightly_plan.run_team`` once per team. One team failing never stops the
others; the run ends non-zero if any failed. Teams with no users are never listed. Reads only.

    python -m analytics.nightly_all [--out exports/plans] [--date 2026-10-09] [--days 15]
        [--only "Team Elite Calling"] [--list]
"""

from __future__ import annotations

import argparse
import sys

from analytics.definitions import team_of
from analytics.nightly_plan import run_team


def teams_from_users(users: list[dict]) -> list[str]:
    """Distinct teams with at least one user, largest first."""
    counts: dict[str, int] = {}
    for u in users:
        t = team_of(u.get("MemberOfGroups"))
        if t != "Unassigned":
            counts[t] = counts.get(t, 0) + 1
    return sorted(counts, key=lambda t: (-counts[t], t))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="exports/plans")
    ap.add_argument("--date", help="plan day, YYYY-MM-DD (default: tomorrow in IST)")
    ap.add_argument("--days", type=int, default=15)
    ap.add_argument("--chances", default="exports/tier_chances.json")
    ap.add_argument("--only", action="append", default=[], help="plan just these teams")
    ap.add_argument("--list", action="store_true", help="print the teams and stop")
    a = ap.parse_args()
    from integrations.leadsquared import LeadSquaredClient

    teams = teams_from_users(LeadSquaredClient().get_users())
    if a.only:
        teams = [t for t in teams if t in a.only]
    if a.list:
        print("\n".join(teams))
        return
    failed = []
    for t in teams:
        try:
            run_team(t, a.out, a.date, a.days, None, a.chances)
        except Exception as e:  # keep going: a plan for 9 teams beats none for 10
            failed.append(t)
            print(f"{t}: FAILED ({type(e).__name__}: {e})", file=sys.stderr)
    print(f"{len(teams) - len(failed)} of {len(teams)} team plans built" + (f"; failed: {', '.join(failed)}" if failed else ""))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
