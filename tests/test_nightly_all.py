from analytics.nightly_all import teams_from_users


def u(*groups):
    return {"MemberOfGroups": list(groups)}


def test_teams_skip_calling_software_and_order_by_size():
    users = [u("Acefone", "Team B"), u("Team A"), u("Team B"), u("Mcube"), u(), {"MemberOfGroups": None}]
    assert teams_from_users(users) == ["Team B", "Team A"]
