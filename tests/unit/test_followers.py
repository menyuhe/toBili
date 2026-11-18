from types import SimpleNamespace

from tobili.runtime.followers import refresh, sample


def test_hourly_sample_and_standby_sync(repos, account_id, clock):
    calls = []
    gateway = SimpleNamespace(follower_count=lambda uid: calls.append(uid) or 0)
    box = SimpleNamespace(repos=repos, clock=clock, gateway_for=lambda _: gateway)
    refresh(box, account_id)
    refresh(box, account_id)
    assert calls == ["1001"]
    assert sample(box, account_id, "1001")["count"] == 0
    assert sample(box, account_id, "other") == {}
    clock.advance(hours=1)
    refresh(box, account_id)
    assert calls == ["1001", "1001"]
    box.resource_policy = SimpleNamespace(allowed=lambda _: False)
    current = sample(box, account_id, "1001")
    box.sync_followers = lambda uid, value: {"count": 47, "at": current["at"] + 1}
    refresh(box, account_id)
    assert sample(box, account_id, "1001")["count"] == 47
    box.sync_followers = lambda uid, value: current
    refresh(box, account_id)
    assert sample(box, account_id, "1001")["count"] == 47
    assert calls == ["1001", "1001"]
