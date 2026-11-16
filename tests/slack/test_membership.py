from deployground.slack.membership import SlackMembershipResolver


class FakeClient:
    def __init__(self):
        self.calls = []

    def usergroups_list(self):
        self.calls.append("usergroups_list")
        return {"usergroups": [{"id": "S1", "handle": "devs"}, {"id": "S2", "handle": "ops"}]}

    def usergroups_users_list(self, usergroup):
        self.calls.append(f"users:{usergroup}")
        return {"users": ["U1"] if usergroup == "S1" else ["U2"]}

    def users_conversations(self, **kwargs):
        self.calls.append(("conv", kwargs.get("cursor")))
        if kwargs.get("cursor"):
            return {"channels": [{"id": "C2"}], "response_metadata": {"next_cursor": ""}}
        return {"channels": [{"id": "C1"}], "response_metadata": {"next_cursor": "next"}}


def test_groups_by_id_and_handle_cached_until_ttl():
    now = [0.0]
    client = FakeClient()
    resolver = SlackMembershipResolver(client, ttl=300, clock=lambda: now[0])

    assert resolver.usergroups_of("U1") == {"S1", "devs"}
    assert resolver.usergroups_of("U2") == {"S2", "ops"}
    assert client.calls.count("usergroups_list") == 1

    now[0] = 301.0
    resolver.usergroups_of("U1")
    assert client.calls.count("usergroups_list") == 2


def test_channels_paginate_and_cache():
    client = FakeClient()
    resolver = SlackMembershipResolver(client, clock=lambda: 0.0)

    assert resolver.channels_of("U1") == {"C1", "C2"}
    resolver.channels_of("U1")
    assert len(client.calls) == 2
