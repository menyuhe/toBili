"""Check a closed draw with the installed enrollment code and an isolated event log."""

import json
import tempfile
from pathlib import Path

from .. import __version__
from ..adapters.bili.gateway import BiliGateway
from ..adapters.bili.http import Wire
from ..adapters.sqlite import Database, Repositories
from ..adapters.sqlite.eventsink import SqliteEventSink
from ..ports.models import AccountProfile
from ..runtime import SystemClock
from ..runtime.events import EventBus
from ..usecases.enroll import Enroll


class ReadOnlyJournal:
    preflight = None

    def attempt(self, **kwargs):
        raise RuntimeError("活动仍可参与，只读检查已停止")


def verify(dyn_id: str, output: Path) -> int:
    if not dyn_id.isascii() or not dyn_id.isdigit():
        raise ValueError("需要动态数字 ID")
    with tempfile.TemporaryDirectory() as folder:
        db = Database(Path(folder) / "check.sqlite")
        wire = Wire()
        try:
            db.migrate()
            repos = Repositories(db)
            owner = repos.accounts.upsert(AccountProfile(uid="1", nickname="只读核验"), "")
            # Expose only GET transport. The process has no account cookies or scheduler.
            from types import SimpleNamespace

            gateway = BiliGateway(SimpleNamespace(fetch=wire.fetch))
            enroll = Enroll(
                gateway=gateway,
                campaigns=repos.campaigns,
                enrollments=repos.enrollments,
                journal=ReadOnlyJournal(),
                clock=SystemClock(),
                standard_actions=True,
                events=EventBus(sink=SqliteEventSink(repos.events)),
            )
            result = enroll.run(owner, dyn_id)
            messages = [
                json.loads(row[0])["message"]
                for row in db.connect().execute(
                    "SELECT payload FROM run_event WHERE topic='participation.check' ORDER BY seq"
                )
            ]
            count = db.connect().execute("SELECT count(*) FROM intent").fetchone()[0]
            proof = dict(
                version=__version__,
                original=result.dyn_id,
                skipped=result.skipped,
                intents=count,
                messages=messages,
            )
            output.write_text(json.dumps(proof, ensure_ascii=False, indent=2), encoding="utf8")
            return 0 if result.skipped and not count and messages else 1
        finally:
            wire.close()
            db.close()
