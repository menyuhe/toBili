from fastapi.testclient import TestClient

from tobili.api.app import create_app
from tobili.domain.campaign import Campaign
from tobili.ports.models import AccountProfile


def test_local_tidy_edit_without_credentials_is_scoped_and_serialized(tmp_path):
    with TestClient(create_app(home=str(tmp_path))) as client:
        box = client.app.state.container
        owner = box.repos.accounts.upsert(AccountProfile(uid="11", nickname="A"), "missing")
        cid = box.repos.campaigns.upsert(Campaign(dyn_id="test", up_uid="1", title="活动"))
        plan = box.repos.tidy.create_plan(owner, now=box.clock.now())
        item = box.repos.tidy.add_item(plan, campaign_id=cid, action="drop_share", snapshot="{}")
        box.repos.tidy.mark_previewed(plan, now=box.clock.now())
        url = f"/api/tidy/plans/{plan}"
        with box.lanes.exclusive(owner):
            assert client.post(url + "/items/select", json={"item_ids": []}).status_code == 409
        assert client.post(url + "/items/select", json={"item_ids": []}).json()["dropped"] == 1
        assert client.post(url + "/items/select", json={"item_ids": [item]}).json()["revived"] == 1
        other = box.repos.accounts.upsert(AccountProfile(uid="22", nickname="B"), "missing2")
        box.repos.accounts.mark_current(other)
        assert client.post(url + "/items/select", json={"item_ids": []}).status_code == 404
        box.repos.accounts.mark_current(owner)
        assert client.post(url + "/confirm").json()["state"] == "confirmed"
        assert client.post(url + "/items/select", json={"item_ids": []}).status_code == 409
        assert client.post(url + "/run", json={"apply": True}).status_code == 401
        assert not box.db.connect().execute("SELECT 1 FROM intent").fetchone()
