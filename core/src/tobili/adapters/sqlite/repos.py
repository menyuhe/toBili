"""仓储实现。

所有写操作都走 `Database.write()`，也就是 `BEGIN IMMEDIATE`。所有时间都过一遍
`stamp()` 转成 UTC —— 混用本地时间和 UTC 是这类工具最经典的隐性 bug：
宽限期差 8 小时，就是"该删的没删"或者"不该删的删了"。
"""

from __future__ import annotations

import json
import time
from collections.abc import Sequence
from contextlib import contextmanager, suppress
from datetime import UTC, datetime

from ...domain.action import ActionKind
from ...domain.campaign import Campaign, CampaignKind
from ...domain.enrollment import Enrollment, Stage
from ...domain.errors import Fault
from ...domain.pool import PoolItem, PoolState
from ...domain.tidy import Outcome
from ...ports.models import AccountProfile
from .db import Database, parse_stamp, stamp

# A day-only finding has no exact draw minute. Its next Beijing midnight is
# used only as a conservative expiry boundary, never as the displayed time.
_DATE_HINT = "(SELECT candidate_at FROM deadline_check d WHERE d.campaign_id=c.id AND d.state='date_only' AND d.body_text=c.body_text AND d.published_at IS c.published_at)"
_EXPIRES = f"COALESCE(c.deadline_at, strftime('%Y-%m-%dT%H:%M:%S+00:00', {_DATE_HINT}, '+8 hours','start of day','+1 day','-8 hours'))"


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _to_profile(row) -> AccountProfile:  # type: ignore[no-untyped-def]
    return AccountProfile(
        uid=row["bili_uid"],
        nickname=row["nickname"],
        avatar=row["avatar"],
        level=int(row["level"] or 0),
    )


class AccountRepo:
    # "当前用哪个账号"存在全局设置里（account_id=0），而不是 account 表上加一列。
    # 加列意味着"每一行都得知道自己是不是当前"，改一次要 UPDATE 全表 —— 一旦
    # 中途失败就有两个当前账号。单行设置天然只有一份。
    K_CURRENT = "account.current"

    def __init__(self, db: Database) -> None:
        self._db = db

    def upsert(self, profile: AccountProfile, credential_ref: str) -> int:
        with self._db.write() as conn:
            conn.execute(
                """
                INSERT INTO account (bili_uid, nickname, avatar, level, credential_ref, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(bili_uid) DO UPDATE SET
                    nickname = excluded.nickname,
                    avatar = excluded.avatar,
                    level = excluded.level,
                    credential_ref = excluded.credential_ref,
                    disabled = 0
                """,
                (
                    profile.uid,
                    profile.nickname,
                    profile.avatar,
                    profile.level,
                    credential_ref,
                    stamp(_utcnow()),
                ),
            )
            row = conn.execute(
                "SELECT id FROM account WHERE bili_uid = ?", (profile.uid,)
            ).fetchone()
        return int(row["id"])

    def current(self) -> tuple[int, AccountProfile] | None:
        """当前账号。

        先认设置里记的那个；它指向不存在/已停用的账号时（删过账号、老库没记过），
        退回最早的一个并把标记补上 —— 读路径顺手自愈，好过让人看到"未登录"
        却不知道为什么。
        """
        conn = self._db.connect()
        marked = conn.execute(
            "SELECT value FROM setting WHERE account_id = 0 AND key = ?", (self.K_CURRENT,)
        ).fetchone()
        if marked is not None and str(marked["value"]).isdigit():
            row = conn.execute(
                "SELECT id, bili_uid, nickname, avatar, level FROM account "
                "WHERE id = ? AND disabled = 0",
                (int(marked["value"]),),
            ).fetchone()
            if row is not None:
                return int(row["id"]), _to_profile(row)

        row = conn.execute(
            "SELECT id, bili_uid, nickname, avatar, level FROM account "
            "WHERE disabled = 0 ORDER BY id LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        self.mark_current(int(row["id"]))
        return int(row["id"]), _to_profile(row)

    def mark_current(self, account_id: int) -> None:
        with self._db.write() as conn:
            conn.execute(
                "INSERT INTO setting (account_id, key, value) VALUES (0, ?, ?) "
                "ON CONFLICT(account_id, key) DO UPDATE SET value = excluded.value",
                (self.K_CURRENT, str(account_id)),
            )

    def credential_ref(self, account_id: int) -> str:
        """这个账号的凭据存在保险库的哪个键下。空串表示没存过。"""
        row = (
            self._db.connect()
            .execute("SELECT credential_ref FROM account WHERE id = ?", (account_id,))
            .fetchone()
        )
        return str(row["credential_ref"]) if row is not None else ""

    def set_credential_ref(self, account_id: int, ref: str) -> None:
        with self._db.write() as conn:
            conn.execute("UPDATE account SET credential_ref = ? WHERE id = ?", (ref, account_id))

    def exists(self, account_id: int) -> bool:
        row = (
            self._db.connect()
            .execute("SELECT id FROM account WHERE id = ?", (account_id,))
            .fetchone()
        )
        return row is not None

    def by_id(self, account_id: int) -> AccountProfile | None:
        row = (
            self._db.connect()
            .execute(
                "SELECT bili_uid, nickname, avatar, level FROM account WHERE id = ?",
                (account_id,),
            )
            .fetchone()
        )
        if row is None:
            return None
        return _to_profile(row)

    def all(self) -> Sequence[AccountProfile]:
        rows = self._db.connect().execute(
            "SELECT bili_uid, nickname, avatar, level FROM account WHERE disabled = 0 ORDER BY id"
        )
        return [_to_profile(r) for r in rows]

    def ids(self) -> Sequence[int]:
        rows = self._db.connect().execute("SELECT id FROM account WHERE disabled = 0 ORDER BY id")
        return [int(row["id"]) for row in rows]

    def disable(self, account_id: int) -> None:
        with self._db.write() as conn:
            conn.execute("UPDATE account SET disabled = 1 WHERE id = ?", (account_id,))


class FeedRepo:
    def __init__(self, db: Database) -> None:
        self._db = db

    def add(self, account_id: int, kind: str, target: str, label: str = "") -> int:
        with self._db.write() as conn:
            if kind in ("topic", "collection"):
                existing = conn.execute(
                    "SELECT id FROM feed WHERE kind=? AND target=?", (kind, target)
                ).fetchone()
                if existing:
                    conn.execute(
                        "UPDATE feed SET label=?,enabled=1 WHERE id=?", (label, existing[0])
                    )
                    return int(existing[0])
            conn.execute(
                """
                INSERT INTO feed (account_id, kind, target, label)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(account_id, kind, target) DO UPDATE SET
                    label = excluded.label, enabled = 1
                """,
                (account_id, kind, target, label),
            )
            row = conn.execute(
                "SELECT id FROM feed WHERE account_id = ? AND kind = ? AND target = ?",
                (account_id, kind, target),
            ).fetchone()
        return int(row["id"])

    def list_for(self, account_id: int) -> Sequence[dict[str, object]]:
        rows = self._db.connect().execute(
            "SELECT f.id, kind, target, label, cursor, last_scan_at, enabled, h.error, h.attempted_at, "
            "coalesce(r.next_at,0) AS next_scan_at "
            "FROM feed f LEFT JOIN feed_health h ON h.feed_id=f.id LEFT JOIN feed_refresh r ON r.feed_id=f.id "
            "WHERE account_id = ? OR kind IN ('topic','collection') ORDER BY f.id",
            (account_id,),
        )
        return [dict(r) for r in rows]

    @contextmanager
    def scan_guard(self, source, *, automatic=False, history=False):
        if source["kind"] not in ("topic", "collection"):
            yield source
            return
        from ...runtime.lockfile import FileLock

        fid = int(source["id"])
        lock = FileLock(self._db.path.parent / "lanes" / "shared-feeds" / f"{fid}.lock")
        if not lock.acquire():
            yield None
            return
        try:
            now = time.time()
            with self._db.write() as db:
                fresh = db.execute("SELECT * FROM feed WHERE id=?", (fid,)).fetchone()
                status = db.execute(
                    "SELECT next_at,last_finished FROM feed_refresh WHERE feed_id=?", (fid,)
                ).fetchone()
                slot = db.execute(
                    "SELECT value FROM setting WHERE account_id=0 AND key='sources.shared.next_slot'"
                ).fetchone()
                blocked = not fresh or (
                    automatic and (not fresh["enabled"] or (slot and float(slot[0]) > now))
                )
                unfinished = False
                if fresh and str(fresh["cursor"]).startswith("v2:"):
                    with suppress(ValueError, TypeError, AttributeError):
                        unfinished = bool(json.loads(fresh["cursor"][3:]).get("latest"))
                if status and not history:
                    blocked = blocked or (
                        status[0] > now if automatic else status[1] > now - 60 and not unfinished
                    )
                if not blocked:
                    db.execute(
                        "INSERT INTO feed_refresh(feed_id,next_at) VALUES(?,?) ON CONFLICT(feed_id) DO UPDATE SET next_at=max(next_at,excluded.next_at)",
                        (fid, now + 300),
                    )
                    if automatic:
                        db.execute(
                            "INSERT INTO setting VALUES(0,'sources.shared.next_slot',?) ON CONFLICT(account_id,key) DO UPDATE SET value=excluded.value",
                            (str(now + 300),),
                        )
            if blocked:
                yield None
            else:
                yield dict(fresh)
        finally:
            lock.release()

    def finish_scan(self, feed_id, *, problem=False):
        from .shared_feeds import interval

        with self._db.write() as db:
            row = db.execute("SELECT kind FROM feed WHERE id=?", (feed_id,)).fetchone()
            if row and row[0] in ("topic", "collection"):
                delay = max(600, interval(db, row[0])) if problem else interval(db, row[0])
                now = time.time()
                db.execute(
                    "UPDATE feed_refresh SET next_at=?,last_finished=? WHERE feed_id=?",
                    (now + delay, now, feed_id),
                )

    def record_status(self, feed_id, *, now, error):
        with self._db.write() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO feed_health VALUES(?,?,?)",
                (feed_id, stamp(now), error[:500]),
            )

    @contextmanager
    def edit_guard(self, feed_id):
        from ...runtime.lockfile import FileLock

        row = self._db.connect().execute("SELECT kind FROM feed WHERE id=?", (feed_id,)).fetchone()
        if not row or row[0] not in ("topic", "collection"):
            yield
            return
        lock = FileLock(self._db.path.parent / "lanes" / "shared-feeds" / f"{feed_id}.lock")
        if not lock.acquire():
            raise ValueError("共享订阅正在更新，请稍后修改")
        try:
            yield
        finally:
            lock.release()

    def save_cursor(self, feed_id: int, cursor: str, *, now: datetime) -> None:
        with self._db.write() as conn:
            conn.execute(
                "UPDATE feed SET cursor = ?, last_scan_at = ? WHERE id = ?",
                (cursor, stamp(now), feed_id),
            )

    def disable(self, feed_id: int) -> None:
        with self._db.write() as conn:
            conn.execute("UPDATE feed SET enabled = 0 WHERE id = ?", (feed_id,))

    def enable(self, feed_id: int) -> None:
        with self._db.write() as conn:
            conn.execute("UPDATE feed SET enabled = 1 WHERE id = ?", (feed_id,))

    def rename(self, feed_id: int, label: str) -> None:
        with self._db.write() as conn:
            conn.execute("UPDATE feed SET label = ? WHERE id = ?", (label, feed_id))

    def delete(self, feed_id: int) -> None:
        """真的删掉一行。

        `campaign_source` 会跟着 CASCADE 删除，但 `campaign` 本身留着 ——
        活动是公共的，发现它的那个源没了，不等于这条活动不存在。
        """
        with self._db.write() as conn:
            conn.execute("DELETE FROM feed WHERE id = ?", (feed_id,))


class CampaignRepo:
    def __init__(self, db: Database) -> None:
        self._db = db

    def removed(self, dyn_id: str) -> bool:
        return bool(
            self._db.connect()
            .execute("SELECT 1 FROM catalog_removed WHERE dyn_id=?", (dyn_id,))
            .fetchone()
        )

    def blocked(self, uid: str) -> bool:
        return bool(
            self._db.connect()
            .execute("SELECT 1 FROM up_block WHERE uid=? AND active=1", (uid,))
            .fetchone()
        )

    def upsert(self, campaign: Campaign, *, source_ref: str = "") -> int:
        from dataclasses import replace

        from ...domain.prize_label import concise_reward

        campaign = replace(campaign, reward=concise_reward(campaign.reward))
        now = stamp(campaign.seen_at or _utcnow())
        with self._db.write() as conn:
            conn.execute(
                """
                INSERT INTO campaign (dyn_id, up_uid, up_name, title, reward, kind,
                                      deadline_at, confidence, source_ref, raw_ref,
                                      first_seen_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(dyn_id) DO UPDATE SET
                    up_name = CASE WHEN excluded.up_name!='' THEN excluded.up_name ELSE campaign.up_name END,
                    up_uid = CASE WHEN excluded.up_uid!='' THEN excluded.up_uid ELSE campaign.up_uid END,
                    title = excluded.title,
                    reward = CASE WHEN campaign.reward_locked=1 OR campaign.reward_status!='pending' OR EXISTS(SELECT 1 FROM prize_review p WHERE p.campaign_id=campaign.id AND p.checked_at!='') THEN campaign.reward ELSE excluded.reward END,
                    kind = CASE WHEN campaign.draw_business_type=10 THEN 'reserve' WHEN campaign.draw_business_type=1 THEN 'share' ELSE excluded.kind END,
                    deadline_at = COALESCE(campaign.deadline_at,excluded.deadline_at),
                    confidence = excluded.confidence,
                    raw_ref = excluded.raw_ref,
                    updated_at = excluded.updated_at
                """,
                (
                    campaign.dyn_id,
                    campaign.up_uid,
                    campaign.up_name,
                    campaign.title,
                    campaign.reward,
                    campaign.kind.value,
                    stamp(campaign.deadline) if campaign.deadline else None,
                    campaign.confidence,
                    source_ref or campaign.source_ref,
                    campaign.raw_ref,
                    now,
                    now,
                ),
            )
            row = conn.execute(
                "SELECT id FROM campaign WHERE dyn_id = ?", (campaign.dyn_id,)
            ).fetchone()
            conn.execute(
                "UPDATE campaign SET published_at=COALESCE(?,published_at),reward_status=CASE WHEN reward_locked=0 AND draw_state='' AND ?!='' AND ?!=body_text THEN 'pending' ELSE reward_status END,body_text=CASE WHEN ?!='' THEN ? ELSE body_text END WHERE id=?",
                (
                    stamp(campaign.published_at) if campaign.published_at else None,
                    campaign.body_text,
                    campaign.body_text,
                    campaign.body_text,
                    campaign.body_text,
                    row["id"],
                ),
            )
            if campaign.draw:
                draw = campaign.draw
                manual = False
                if conn.execute("SELECT 1 FROM sqlite_master WHERE name='pro_edits'").fetchone():
                    edited = conn.execute(
                        "SELECT accepted FROM pro_edits WHERE dyn=?", (campaign.dyn_id,)
                    ).fetchone()
                    manual = bool(edited and "deadline_at" in json.loads(edited[0]))
                conn.execute(
                    "UPDATE campaign SET draw_business_id=?,draw_business_type=?,draw_state=CASE WHEN draw_state='ended' THEN draw_state ELSE ? END,draw_checked_at=?,draw_reason=CASE WHEN draw_state='ended' AND ?='' THEN draw_reason ELSE ? END WHERE id=?",
                    (
                        draw.business_id,
                        draw.business_type,
                        draw.state,
                        now,
                        draw.reason,
                        draw.reason,
                        row["id"],
                    ),
                )
                if draw.verified:
                    if draw.deadline and not manual:
                        conn.execute("DELETE FROM deadline_check WHERE campaign_id=?", (row["id"],))
                    conn.execute(
                        "UPDATE campaign SET kind=?,reward=CASE WHEN reward_locked=0 AND ?!='' THEN ? ELSE reward END,reward_status=CASE WHEN reward_locked=0 AND ?!='' THEN 'verified' ELSE reward_status END,deadline_at=CASE WHEN ?=0 THEN COALESCE(?,deadline_at) ELSE deadline_at END WHERE id=?",
                        (
                            "reserve" if draw.business_type == 10 else "share",
                            draw.reward,
                            draw.reward,
                            draw.reward,
                            int(manual),
                            stamp(draw.deadline) if draw.deadline else None,
                            row["id"],
                        ),
                    )
            override = conn.execute(
                "SELECT value FROM deadline_override WHERE campaign_id=?", (row["id"],)
            ).fetchone()
            if override:
                conn.execute(
                    "UPDATE campaign SET deadline_at=? WHERE id=?", (override[0], row["id"])
                )
                conn.execute("DELETE FROM deadline_check WHERE campaign_id=?", (row["id"],))
        return int(row["id"])

    def get(self, campaign_id: int) -> dict[str, object] | None:
        row = (
            self._db.connect()
            .execute(
                f"SELECT c.*, {_DATE_HINT} AS deadline_date, {_EXPIRES} AS expires_at FROM campaign c WHERE id = ?",
                (campaign_id,),
            )
            .fetchone()
        )
        return dict(row) if row else None

    def shelve_closed(self, account_id: int, now: datetime) -> int:
        with self._db.write() as conn:
            return conn.execute(
                f"INSERT OR IGNORE INTO closed_shelf(account_id,campaign_id) SELECT ?,c.id FROM campaign c WHERE {_EXPIRES} <= ?",
                (account_id, stamp(now)),
            ).rowcount

    def shelved(self, account_id: int, campaign_id: int) -> bool:
        return bool(
            self._db.connect()
            .execute(
                "SELECT 1 FROM closed_shelf WHERE account_id=? AND campaign_id=?",
                (account_id, campaign_id),
            )
            .fetchone()
        )

    def dismiss(self, account_id: int, campaign_id: int, *, prize_only: bool = False) -> None:
        with self._db.write() as conn:
            row = conn.execute(
                "SELECT reward_status FROM campaign WHERE id=?", (campaign_id,)
            ).fetchone()
            if not row:
                raise LookupError("活动不存在")
            if prize_only and row[0] != "unknown":
                raise ValueError("奖品已补全，请刷新列表")
            if (
                not prize_only
                and conn.execute(
                    "SELECT 1 FROM enrollment WHERE account_id=? AND campaign_id=?",
                    (account_id, campaign_id),
                ).fetchone()
            ):
                raise ValueError("已有参与记录，请在开奖核验中处理")
            conn.execute(
                "INSERT INTO closed_shelf(account_id,campaign_id,reason) VALUES(?,?,'manual') "
                "ON CONFLICT(account_id,campaign_id) DO UPDATE SET reason='manual'",
                (account_id, campaign_id),
            )
            conn.execute(
                "DELETE FROM pool_item WHERE account_id=? AND campaign_id=?",
                (account_id, campaign_id),
            )

    def unshelve(self, account_id: int, campaign_id: int) -> None:
        with self._db.write() as conn:
            conn.execute(
                "DELETE FROM closed_shelf WHERE account_id=? AND campaign_id=?",
                (account_id, campaign_id),
            )

    def catalog(
        self,
        account_id: int,
        *,
        keyword: str = "",
        kind: str = "",
        state: str = "",
        limit: int = 100,
        offset: int = 0,
        statuses: str = "",
        time_field: str = "published",
        after: str = "",
        before: str = "",
        order: str = "newest",
        prize_state: str = "",
        availability: str = "",
    ) -> dict[str, object]:
        """活动库。一个活动的四种身份拼在一行里：它是什么、我报了没、判成什么、清没清。

        分开查会让界面要发四个请求再自己对齐，而"这条到底能不能清"恰恰取决于这四者
        的组合 —— 拼在一条 SQL 里，判断依据就只有一个来源。
        """
        where: list[str] = []
        if state not in ("review", "all_records", "cleaned"):
            where.append("NOT EXISTS(SELECT 1 FROM catalog_removed x WHERE x.dyn_id=c.dyn_id)")
            from ...usecases.catalog_policy import enabled

            if enabled(self._db):
                where.extend(
                    [
                        "c.kind NOT IN ('unknown','check_in')",
                        "c.reward_status IN ('verified','manual')",
                        "trim(c.reward)!=''",
                        "c.draw_state NOT IN ('ended','unverified','unsupported')",
                        f"{_EXPIRES}>strftime('%Y-%m-%dT%H:%M:%S+00:00','now')",
                    ]
                )
        if state not in ("review", "all_records", "cleaned", "shelved"):
            where.append("NOT EXISTS(SELECT 1 FROM up_block b WHERE b.uid=c.up_uid AND b.active=1)")
        filters: list[object] = []
        if prize_state == "unknown":
            where.append("c.reward_status='unknown'")
        if availability == "open":
            where.append("c.draw_state NOT IN ('ended','unverified','unsupported')")
            where.append(
                f"({_EXPIRES} IS NULL OR {_EXPIRES}>strftime('%Y-%m-%dT%H:%M:%S+00:00','now'))"
            )
        if state not in ("review", "all_records", "cleaned"):
            where.append(
                ("EXISTS" if state == "shelved" else "NOT EXISTS")
                + " (SELECT 1 FROM closed_shelf s WHERE s.account_id=? AND s.campaign_id=c.id)"
            )
            filters.append(account_id)
        if state in ("available", "selection"):
            where.append("c.draw_state NOT IN ('ended','unverified','unsupported')")
            where.extend(
                [
                    "e.id IS NULL",
                    f"({_EXPIRES} IS NULL OR {_EXPIRES} > ?)",
                ]
            )
            filters.append(stamp(_utcnow()))
            if state == "available":
                where.append(
                    "NOT EXISTS (SELECT 1 FROM pool_item q WHERE q.account_id=? AND q.campaign_id=c.id)"
                )
                filters.append(account_id)
        if state == "review":
            where.append("e.id IS NOT NULL")
        if keyword.strip():
            where.append("(c.title LIKE ? OR c.reward LIKE ? OR c.up_name LIKE ? OR c.dyn_id = ?)")
            like = f"%{keyword.strip()}%"
            filters.extend([like, like, like, keyword.strip()])
        if kind:
            kinds = [k for k in kind.split(",") if k]
            where.append(
                "(CASE WHEN c.draw_business_type=1 THEN 'interactive' ELSE c.kind END) IN ("
                + ",".join("?" for _ in kinds)
                + ")"
            )
            filters.extend(kinds)
        predicates = {
            "enrolled": "e.id IS NOT NULL",
            "unjoined": "e.id IS NULL",
            "won": "v.outcome='won'",
            "lost": "v.outcome='lost'",
            "pending": "e.id IS NOT NULL AND (v.outcome IS NULL OR v.outcome='pending')",
            "open": f"({_EXPIRES} IS NULL OR {_EXPIRES}>strftime('%Y-%m-%dT%H:%M:%S+00:00','now'))",
            "closed": f"{_EXPIRES}<=strftime('%Y-%m-%dT%H:%M:%S+00:00','now')",
        }
        chosen = [predicates[s] for s in statuses.split(",") if s in predicates]
        if chosen:
            where.append("(" + " OR ".join(chosen) + ")")
        field = "c.deadline_at" if time_field == "deadline" else "c.published_at"
        if after:
            where.append(
                f"({field} >= ? OR (c.deadline_at IS NULL AND date({_DATE_HINT},'+8 hours') >= date(?,'+8 hours')))"
                if time_field == "deadline"
                else field + " >= ?"
            )
            filters.append(after)
            if time_field == "deadline":
                filters.append(after)
        if before:
            where.append(
                f"({field} <= ? OR (c.deadline_at IS NULL AND date({_DATE_HINT},'+8 hours') <= date(?,'+8 hours')))"
                if time_field == "deadline"
                else field + " <= ?"
            )
            filters.append(before)
            if time_field == "deadline":
                filters.append(before)
        if state == "enrolled":
            where.append("e.id IS NOT NULL")
        elif state in ("won", "lost"):
            where.append("v.outcome = ?")
            filters.append(state)
        elif state == "pending":
            where.append("e.id IS NOT NULL AND v.outcome IS NULL")
        elif state == "cleaned":
            where.append(
                "EXISTS (SELECT 1 FROM tidy_item i WHERE i.campaign_id = c.id AND i.plan_id IN (SELECT id FROM tidy_plan WHERE account_id=e.account_id)"
                " AND i.state = 'done' AND i.action = 'drop_share')"
            )
        clause = f" WHERE {' AND '.join(where)}" if where else ""

        joins = (
            " FROM campaign c"
            " LEFT JOIN enrollment e ON e.campaign_id = c.id AND e.account_id = ?"
            " LEFT JOIN verdict v ON v.campaign_id = c.id AND v.account_id = ?"
        )
        rows = (
            self._db.connect()
            .execute(
                "SELECT c.id, c.dyn_id, c.up_uid, c.up_name, c.title, c.reward, CASE WHEN c.draw_business_type=1 THEN 'interactive' ELSE c.kind END AS kind,c.draw_business_type,c.draw_state,c.draw_reason,"
                " c.deadline_at, c.confidence, c.first_seen_at,c.published_at,c.reward_status,"
                f" {_DATE_HINT} AS deadline_date, {_EXPIRES} AS expires_at,"
                " (SELECT s.reason FROM closed_shelf s WHERE s.account_id=? AND s.campaign_id=c.id) AS shelf_reason,"
                " EXISTS(SELECT 1 FROM pool_item q WHERE q.account_id=? AND q.campaign_id=c.id) AS queued,"
                " e.id AS enrollment_id, e.stage AS enrollment_stage,"
                " v.outcome AS verdict, v.source AS verdict_source, v.note AS verdict_note,"
                " v.decided_at AS decided_at,"
                " EXISTS (SELECT 1 FROM tidy_item i WHERE i.campaign_id = c.id AND i.plan_id IN (SELECT id FROM tidy_plan WHERE account_id=e.account_id)"
                "   AND i.state = 'done' AND i.action = 'drop_share') AS cleaned,"
                " EXISTS (SELECT 1 FROM tidy_keep k WHERE k.account_id = ?"
                "   AND ((k.kind = 'up' AND k.value = c.up_uid)"
                "     OR (k.kind = 'campaign' AND k.value = c.dyn_id))) AS kept"
                + joins
                + clause
                + (
                    f" ORDER BY CASE WHEN e.id IS NULL THEN 0 WHEN e.stage IN ('blocked','failed','planned') THEN 1 ELSE 2 END, COALESCE({_EXPIRES}, '9999'),c.id DESC"
                    if order == "deadline"
                    else " ORDER BY CASE WHEN e.id IS NULL THEN 0 WHEN e.stage IN ('blocked','failed','planned') THEN 1 ELSE 2 END, COALESCE(c.published_at,c.first_seen_at) DESC,CAST(c.dyn_id AS INTEGER) DESC,c.id DESC"
                )
                + " LIMIT ? OFFSET ?",
                (
                    account_id,
                    account_id,
                    account_id,
                    account_id,
                    account_id,
                    *filters,
                    max(1, limit),
                    max(0, offset),
                ),
            )
            .fetchall()
        )
        total = (
            self._db.connect()
            .execute(
                "SELECT COUNT(*) AS n" + joins + clause,
                (account_id, account_id, *filters),
            )
            .fetchone()
        )
        return {"items": [dict(row) for row in rows], "total": int(total["n"] if total else 0)}

    def recent(self, limit: int = 200, offset: int = 0) -> Sequence[dict[str, object]]:
        """最近看到的活动，新的在前。

        整理稿的候选池要的是"我库里有啥"，不是"现在还能参与啥"，所以这里不按
        截止时间过滤 —— 已开奖的活动照样能写进稿子，只是预览时会带一句提醒。
        """
        rows = self._db.connect().execute(
            f"SELECT c.id, dyn_id, up_uid, up_name, title, reward, CASE WHEN draw_business_type=1 THEN 'interactive' ELSE kind END AS kind, deadline_at, {_DATE_HINT} AS deadline_date,"
            " confidence, first_seen_at FROM campaign c WHERE NOT EXISTS(SELECT 1 FROM up_block b WHERE b.uid=c.up_uid AND b.active=1)"
            " ORDER BY first_seen_at DESC, id DESC LIMIT ? OFFSET ?",
            (max(1, limit), max(0, offset)),
        )
        # 时间在这里就解析成 datetime，和 `lost_before` 保持一致 —— 让用例层
        # 自己去认字符串格式，等于把存储细节漏上去。
        return [
            {
                **dict(row),
                "deadline_at": parse_stamp(row["deadline_at"]) if row["deadline_at"] else None,
            }
            for row in rows
        ]

    def by_dyn(self, dyn_id: str) -> tuple[int, Campaign] | None:
        row = (
            self._db.connect()
            .execute("SELECT * FROM campaign WHERE dyn_id = ?", (dyn_id,))
            .fetchone()
        )
        return (int(row["id"]), _to_campaign(row)) if row else None

    def open_between(self, start: datetime, end: datetime) -> Sequence[tuple[int, Campaign]]:
        rows = self._db.connect().execute(
            "SELECT * FROM campaign WHERE deadline_at IS NULL OR "
            "(deadline_at >= ? AND deadline_at <= ?) ORDER BY deadline_at IS NULL, deadline_at",
            (stamp(start), stamp(end)),
        )
        return [(int(r["id"]), _to_campaign(r)) for r in rows]

    def attach_source(self, campaign_id: int, *, feed_id: int, label: str, now: datetime) -> bool:
        """记下"这个活动被这个源看到了"。已记过则返回 False，不算失败。"""
        with self._db.write() as conn:
            cursor = conn.execute(
                """
                INSERT INTO campaign_source (campaign_id, feed_id, feed_label, first_seen_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(campaign_id, feed_id) DO NOTHING
                """,
                (campaign_id, feed_id, label, stamp(now)),
            )
            return cursor.rowcount > 0

    def sources_of(self, campaign_id: int) -> Sequence[str]:
        rows = self._db.connect().execute(
            "SELECT feed_label FROM campaign_source WHERE campaign_id = ? ORDER BY first_seen_at",
            (campaign_id,),
        )
        return [str(r["feed_label"]) for r in rows]

    def no_candidate_reason(self, account_id: int, *, now: datetime, floor: float) -> str:
        rows = (
            self._db.connect()
            .execute(
                f"""SELECT CASE
            WHEN EXISTS(SELECT 1 FROM up_block b WHERE b.uid=c.up_uid AND b.active=1) THEN 'UP 已屏蔽'
            WHEN EXISTS(SELECT 1 FROM closed_shelf s WHERE s.account_id=? AND s.campaign_id=c.id) THEN '已收存'
            WHEN {_EXPIRES} IS NOT NULL AND {_EXPIRES}<=? THEN '已截止'
            WHEN c.draw_state='ended' THEN '已截止'
            WHEN c.draw_state IN ('unverified','unsupported') THEN '参与方式待核对'
            WHEN e.id IS NOT NULL AND e.stage!='planned' THEN '已有参与记录'
            WHEN c.kind='unknown' OR c.confidence<? THEN '参与方式待核对'
            ELSE '可参与' END candidate_reason,COUNT(*) amount
            FROM campaign c LEFT JOIN enrollment e ON e.campaign_id=c.id AND e.account_id=?
            GROUP BY candidate_reason""",
                (account_id, stamp(now), floor, account_id),
            )
            .fetchall()
        )
        counts = {r["candidate_reason"]: r["amount"] for r in rows}
        details = "、".join(
            f"{key} {counts[key]} 条"
            for key in ("参与方式待核对", "已截止", "已有参与记录", "UP 已屏蔽", "已收存")
            if counts.get(key)
        )
        return "待参与为空；奖品库暂无自动候选" + (f"：{details}" if details else "")

    def candidates(
        self, account_id: int, *, now: datetime, floor: float, limit: int
    ) -> Sequence[dict[str, object]]:
        """可以自动参与的活动：还没真正做过、仍然开放、置信度够。

        故意不包含"上次失败"和"上次阻塞"的记录。一条活动失败通常意味着它需要人看
        一眼（要求看不懂、接口变了、被风控），自动重试只会把同一个错误重复一遍。

        `planned` 要放进来：那是预演留下的状态，如果不算候选，预演过一次的活动就
        再也不会被真正执行，而这恰恰是"预演完再确认"的正常用法。
        """
        rows = self._db.connect().execute(
            f"""
            SELECT c.*, e.stage AS enrolled_stage
            FROM campaign c
            LEFT JOIN enrollment e
                   ON e.campaign_id = c.id AND e.account_id = ?
            WHERE (e.id IS NULL OR e.stage = 'planned')
              AND NOT EXISTS(SELECT 1 FROM catalog_removed x WHERE x.dyn_id=c.dyn_id)
              AND (NOT EXISTS(SELECT 1 FROM setting WHERE account_id=0 AND key='catalog.curated' AND value='1') OR (c.reward_status IN ('verified','manual') AND trim(c.reward)!='' AND c.kind NOT IN ('unknown','check_in') AND {_EXPIRES} IS NOT NULL))
              AND ({_EXPIRES} IS NULL OR {_EXPIRES} > ?)
              AND c.confidence >= ?
              AND c.kind != 'unknown'
              AND c.draw_state NOT IN ('ended','unverified','unsupported')
              AND NOT EXISTS(SELECT 1 FROM pool_item p WHERE p.account_id=? AND p.campaign_id=c.id AND p.state IN ('review','failed'))
              AND NOT EXISTS(SELECT 1 FROM up_block b WHERE b.uid=c.up_uid AND b.active=1)
              AND NOT EXISTS(SELECT 1 FROM closed_shelf s WHERE s.account_id=? AND s.campaign_id=c.id)
            ORDER BY c.deadline_at IS NULL, c.deadline_at, c.confidence DESC
            LIMIT ?
            """,
            (account_id, stamp(now), floor, account_id, account_id, max(1, limit)),
        )
        return [dict(r) for r in rows]


def _to_campaign(row) -> Campaign:  # type: ignore[no-untyped-def]
    from ...domain.draw import DrawFacts

    return Campaign(
        dyn_id=row["dyn_id"],
        up_uid=row["up_uid"],
        title=row["title"],
        kind=CampaignKind(row["kind"]),
        up_name=row["up_name"],
        reward=row["reward"],
        deadline=parse_stamp(row["deadline_at"]),
        confidence=float(row["confidence"]),
        source_ref=row["source_ref"],
        raw_ref=row["raw_ref"],
        seen_at=parse_stamp(row["first_seen_at"]),
        published_at=parse_stamp(row["published_at"]),
        body_text=row["body_text"],
        draw=DrawFacts(
            row["draw_business_id"],
            row["draw_business_type"],
            row["reward"],
            parse_stamp(row["deadline_at"]),
            row["draw_state"],
            row["draw_reason"],
        )
        if row["draw_state"]
        else None,
    )


class EnrollmentRepo:
    def __init__(self, db: Database) -> None:
        self._db = db

    def create(self, account_id: int, campaign_id: int, *, now: datetime) -> int:
        with self._db.write() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO enrollment (account_id, campaign_id, stage,"
                " created_at, updated_at) VALUES (?, ?, 'planned', ?, ?)",
                (account_id, campaign_id, stamp(now), stamp(now)),
            )
            row = conn.execute(
                "SELECT id FROM enrollment WHERE account_id = ? AND campaign_id = ?",
                (account_id, campaign_id),
            ).fetchone()
        return int(row["id"])

    def get(self, enrollment_id: int) -> Enrollment | None:
        row = (
            self._db.connect()
            .execute("SELECT * FROM enrollment WHERE id = ?", (enrollment_id,))
            .fetchone()
        )
        if row is None:
            return None
        return Enrollment(
            id=int(row["id"]),
            account_id=int(row["account_id"]),
            campaign_id=int(row["campaign_id"]),
            stage=Stage(row["stage"]),
            created_at=parse_stamp(row["created_at"]) or _utcnow(),
            updated_at=parse_stamp(row["updated_at"]) or _utcnow(),
            reason=row["reason"],
        )

    def stage_of(self, account_id: int, campaign_id: int) -> Stage | None:
        """这条活动在这个账号下已经走到哪一步了。预选池靠它判断"要不要再排一次"。"""
        row = (
            self._db.connect()
            .execute(
                "SELECT stage FROM enrollment WHERE account_id = ? AND campaign_id = ?",
                (account_id, campaign_id),
            )
            .fetchone()
        )
        return Stage(row["stage"]) if row else None

    def move(self, enrollment_id: int, stage: Stage, *, now: datetime, reason: str = "") -> None:
        with self._db.write() as conn:
            conn.execute(
                "UPDATE enrollment SET stage = ?, reason = ?, updated_at = ? WHERE id = ?",
                (stage.value, reason, stamp(now), enrollment_id),
            )

    def pending_for(self, account_id: int) -> Sequence[Enrollment]:
        rows = self._db.connect().execute(
            "SELECT * FROM enrollment WHERE account_id = ? AND stage IN ('planned','running','blocked')"
            " ORDER BY id",
            (account_id,),
        )
        return [
            Enrollment(
                id=int(r["id"]),
                account_id=int(r["account_id"]),
                campaign_id=int(r["campaign_id"]),
                stage=Stage(r["stage"]),
                created_at=parse_stamp(r["created_at"]) or _utcnow(),
                updated_at=parse_stamp(r["updated_at"]) or _utcnow(),
                reason=r["reason"],
            )
            for r in rows
        ]

    def settleable(self, account_id: int, *, before: datetime) -> Sequence[dict[str, object]]:
        """已过截止时间、且还没结算的报名。联表取出开奖判定要用的字段。"""
        rows = self._db.connect().execute(
            "SELECT e.id AS enrollment_id, e.campaign_id, e.stage, c.dyn_id, c.up_uid,"
            " c.up_name, c.title, c.kind,c.deadline_at,c.draw_business_id,c.draw_business_type FROM enrollment e"
            " JOIN campaign c ON c.id = e.campaign_id"
            " LEFT JOIN verdict v ON v.account_id=e.account_id AND v.campaign_id=e.campaign_id"
            " LEFT JOIN result_probe p ON p.account_id=e.account_id AND p.campaign_id=e.campaign_id"
            " WHERE e.account_id = ? AND e.stage IN ('planned','running','blocked','settled')"
            " AND (v.outcome IS NULL OR v.outcome='pending')"
            " AND c.deadline_at IS NOT NULL AND c.deadline_at <= ?"
            " AND (p.retry_at IS NULL OR p.retry_at <= ?)"
            " AND NOT EXISTS(SELECT 1 FROM result_dismissal d WHERE d.account_id=e.account_id AND d.campaign_id=e.campaign_id)"
            " ORDER BY COALESCE(p.attempted_at,v.decided_at,''),c.deadline_at",
            (account_id, stamp(before), stamp(before)),
        )
        return [dict(r) for r in rows]

    def record_check(self, account_id, campaign_id, *, now, retry_at, error=""):
        with self._db.write() as conn:
            conn.execute(
                "INSERT INTO result_probe(account_id,campaign_id,attempted_at,retry_at,error)"
                " VALUES(?,?,?,?,?) ON CONFLICT(account_id,campaign_id) DO UPDATE SET"
                " attempted_at=excluded.attempted_at,retry_at=excluded.retry_at,error=excluded.error",
                (account_id, campaign_id, stamp(now), stamp(retry_at), error),
            )

    def by_campaign(self, campaign_id: int) -> Sequence[Enrollment]:
        rows = self._db.connect().execute(
            "SELECT * FROM enrollment WHERE campaign_id = ? ORDER BY id", (campaign_id,)
        )
        return [
            Enrollment(
                id=int(r["id"]),
                account_id=int(r["account_id"]),
                campaign_id=int(r["campaign_id"]),
                stage=Stage(r["stage"]),
                created_at=parse_stamp(r["created_at"]) or _utcnow(),
                updated_at=parse_stamp(r["updated_at"]) or _utcnow(),
                reason=r["reason"],
            )
            for r in rows
        ]


class IntentRepo:
    """幂等核心。

    `reserve` 用 `INSERT OR IGNORE` + `rowcount` 判断是否首次占位。这一步必须
    在发请求之前完成，而且必须在同一个事务里拿到结果 —— 它是"这件事只能做一次"
    的物理保证，不是一次检查。
    """

    def __init__(self, db: Database) -> None:
        self._db = db

    def prior_action(self, account_id: int, campaign_id: int, action: ActionKind):
        row = (
            self._db.connect()
            .execute(
                "SELECT id,state,error_class,message FROM intent WHERE account_id=? AND campaign_id=? AND action=? ORDER BY id DESC LIMIT 1",
                (account_id, campaign_id, action.value),
            )
            .fetchone()
        )
        return dict(row) if row else None

    def reserve(
        self,
        *,
        account_id: int,
        campaign_id: int,
        action: ActionKind,
        fingerprint: str,
        content: str,
        now: datetime,
    ) -> tuple[int, bool]:
        with self._db.write() as conn:
            if action in (
                ActionKind.FOLLOW,
                ActionKind.SHARE,
                ActionKind.COMMENT,
                ActionKind.LIKE,
                ActionKind.RESERVE,
                ActionKind.FAVORITE,
            ):
                previous = conn.execute(
                    "SELECT id,state FROM intent WHERE account_id=? AND campaign_id=? AND action=? ORDER BY id DESC LIMIT 1",
                    (account_id, campaign_id, action.value),
                ).fetchone()
                if previous:
                    if (action is ActionKind.FOLLOW and previous["state"] == "done") or (
                        action is ActionKind.LIKE and previous["state"] == "legacy_like_rejected"
                    ):
                        # A live ABSENT relation probe may follow again after an
                        # earlier confirmed follow was removed. Preserve the old row.
                        import uuid

                        fingerprint = fingerprint + ":" + uuid.uuid4().hex
                    else:
                        return int(previous["id"]), False
            cursor = conn.execute(
                "INSERT OR IGNORE INTO intent (account_id, campaign_id, action, fingerprint,"
                " content, state, created_at) VALUES (?, ?, ?, ?, ?, 'reserved', ?)",
                (
                    account_id,
                    campaign_id,
                    action.value,
                    fingerprint,
                    content,
                    now.astimezone(UTC).isoformat(timespec="microseconds"),
                ),
            )
            created = cursor.rowcount == 1
            row = conn.execute(
                "SELECT id FROM intent WHERE account_id = ? AND campaign_id = ?"
                " AND action = ? AND fingerprint = ?",
                (account_id, campaign_id, action.value, fingerprint),
            ).fetchone()
        return int(row["id"]), created

    def settle(
        self,
        intent_id: int,
        *,
        ok: bool,
        ambiguous: bool,
        fault: str,
        message: str,
        now: datetime,
    ) -> None:
        state = "done" if ok else ("unknown" if ambiguous else "rejected")
        with self._db.write() as conn:
            conn.execute(
                "UPDATE intent SET state = ?, error_class = ?, message = ?, finished_at = ?"
                " WHERE id = ?",
                (
                    state,
                    fault,
                    message,
                    now.astimezone(UTC).isoformat(timespec="microseconds"),
                    intent_id,
                ),
            )

    def unresolved(self, account_id: int) -> Sequence[dict[str, object]]:
        rows = self._db.connect().execute(
            "SELECT id, campaign_id, action, fingerprint, state, created_at FROM intent"
            " WHERE account_id = ? AND state IN ('reserved','unknown') ORDER BY id",
            (account_id,),
        )
        return [dict(r) for r in rows]

    def state_of(self, intent_id: int) -> str:
        row = (
            self._db.connect()
            .execute("SELECT state FROM intent WHERE id = ?", (intent_id,))
            .fetchone()
        )
        return str(row["state"]) if row else ""

    def writes_since(self, account_id: int, *, since: datetime) -> tuple[int, datetime | None]:
        """一段时间内发起过多少次写、最后一次是什么时候。配额门禁的唯一数据来源。"""
        row = (
            self._db.connect()
            .execute(
                "SELECT COUNT(*) AS n, MAX(COALESCE(finished_at,created_at)) AS last FROM intent"
                " WHERE account_id = ? AND created_at >= ?",
                (account_id, stamp(since)),
            )
            .fetchone()
        )
        return int(row["n"] or 0), parse_stamp(row["last"])

    def risk_hits_since(self, account_id: int, *, since: datetime) -> int:
        """一段时间内被风控拒绝过几次。

        只数 `fault = 'risk'` 这个分类。它包含了风控校验失败、请求被拦截、
        操作太频繁三种码 —— 对清理闸门来说它们是同一件事：现在别动手。
        """
        row = (
            self._db.connect()
            .execute(
                "SELECT COUNT(*) AS n FROM intent"
                " WHERE account_id = ? AND created_at >= ? AND error_class = ?",
                (account_id, stamp(since), Fault.RISK.value),
            )
            .fetchone()
        )
        return int(row["n"] or 0)


class VerdictRepo:
    def __init__(self, db: Database) -> None:
        self._db = db

    def record(
        self,
        *,
        campaign_id: int,
        account_id: int,
        outcome: Outcome,
        decided_at: datetime,
        evidence: str = "",
        source: str = "auto",
        note: str = "",
    ) -> bool:
        """带 `WHERE verdict.source != 'manual'` 的 upsert。

        这一句是"人工改判"能成立的全部原因。少了它，人改一次、下一轮自动核验
        又改回去 —— 界面上的改判按钮就等于一个装饰。反过来，人工写入不受这个
        限制，永远覆盖，因为人比机器更该有最终发言权。
        """
        with self._db.write() as conn:
            cursor = conn.execute(
                "INSERT INTO verdict (campaign_id, account_id, outcome, decided_at,"
                " evidence, source, note) VALUES (?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(campaign_id, account_id) DO UPDATE SET"
                " outcome = excluded.outcome, decided_at = excluded.decided_at,"
                " evidence = excluded.evidence, source = excluded.source,"
                " note = excluded.note"
                " WHERE verdict.source != 'manual' OR excluded.source = 'manual'",
                (
                    campaign_id,
                    account_id,
                    outcome.value,
                    stamp(decided_at),
                    evidence,
                    source,
                    note,
                ),
            )
        return cursor.rowcount > 0

    def of(self, campaign_id: int, account_id: int) -> Outcome | None:
        row = (
            self._db.connect()
            .execute(
                "SELECT outcome FROM verdict WHERE campaign_id = ? AND account_id = ?",
                (campaign_id, account_id),
            )
            .fetchone()
        )
        return Outcome(row["outcome"]) if row else None

    def detail(self, campaign_id: int, account_id: int) -> dict[str, object] | None:
        row = (
            self._db.connect()
            .execute(
                "SELECT outcome, decided_at, evidence, source, note FROM verdict"
                " WHERE campaign_id = ? AND account_id = ?",
                (campaign_id, account_id),
            )
            .fetchone()
        )
        if not row:
            return None
        return {
            "outcome": row["outcome"],
            "decided_at": parse_stamp(row["decided_at"]),
            "evidence": row["evidence"],
            "source": row["source"],
            "note": row["note"],
        }

    def lost_before(self, account_id: int, *, before: datetime) -> Sequence[dict[str, object]]:
        """未中奖且判定时间在 `before` 之前的记录，供清理预演使用。

        按 campaign 聚合：同一条活动可能被多个账号报名，清理是针对"我这条报名"的。
        """
        rows = self._db.connect().execute(
            "SELECT v.campaign_id, v.decided_at, c.dyn_id, c.up_uid, c.up_name, c.title,"
            " MIN(e.id) AS enrollment_id FROM verdict v"
            " JOIN campaign c ON c.id = v.campaign_id"
            " LEFT JOIN enrollment e ON e.campaign_id = v.campaign_id"
            " AND e.account_id = v.account_id"
            " WHERE v.account_id = ? AND v.outcome = 'lost' AND v.decided_at <= ?"
            " GROUP BY v.campaign_id, v.decided_at ORDER BY v.decided_at",
            (account_id, stamp(before)),
        )
        return [
            {
                **dict(row),
                "decided_at": parse_stamp(row["decided_at"]),
                "enrollment_id": int(row["enrollment_id"] or 0),
            }
            for row in rows
        ]


class TidyRepo:
    def __init__(self, db: Database) -> None:
        self._db = db

    def create_plan(self, account_id: int, *, now: datetime) -> int:
        with self._db.write() as conn:
            cursor = conn.execute(
                "INSERT INTO tidy_plan (account_id, state, created_at) VALUES (?, 'draft', ?)",
                (account_id, stamp(now)),
            )
        return int(cursor.lastrowid or 0)

    def add_item(self, plan_id: int, *, campaign_id: int, action: str, snapshot: str) -> int:
        with self._db.write() as conn:
            cursor = conn.execute(
                "INSERT INTO tidy_item (plan_id, campaign_id, action, snapshot)"
                " VALUES (?, ?, ?, ?)",
                (plan_id, campaign_id, action, snapshot),
            )
        return int(cursor.lastrowid or 0)

    def mark_previewed(self, plan_id: int, *, now: datetime) -> None:
        with self._db.write() as conn:
            conn.execute(
                "UPDATE tidy_plan SET state = 'previewed', previewed_at = ? WHERE id = ?",
                (stamp(now), plan_id),
            )

    def mark_confirmed(self, plan_id: int, *, now: datetime) -> None:
        with self._db.write() as conn:
            conn.execute(
                "UPDATE tidy_plan SET state = 'confirmed', confirmed_at = ? WHERE id = ?",
                (stamp(now), plan_id),
            )

    def items_of(self, plan_id: int) -> Sequence[dict[str, object]]:
        # 带上判定时间：界面上"为什么可以清"最有力的依据是"什么时候判定未中奖"，
        # 而不是一句"等你确认"。用子查询而不是 JOIN，是因为同一活动可能被多个
        # 账号报名，JOIN 会把一条清单行变成好几行。
        rows = self._db.connect().execute(
            "SELECT i.id, i.campaign_id, i.action, i.target, i.snapshot, i.state, i.reason,"
            " i.executed_at, c.dyn_id, c.up_uid, c.title, c.reward, c.up_name,"
            " (SELECT decided_at FROM verdict v WHERE v.campaign_id = i.campaign_id"
            "  ORDER BY v.decided_at DESC LIMIT 1) AS decided_at"
            " FROM tidy_item i"
            " JOIN campaign c ON c.id = i.campaign_id WHERE i.plan_id = ? ORDER BY i.id",
            (plan_id,),
        )
        return [dict(r) for r in rows]

    def pending_items(self, plan_id: int) -> Sequence[dict[str, object]]:
        """还没动过的条目。已执行、已失败、已跳过都不在返回里。

        `skipped` 单独存在，是为了让"这一条没找到转发"和"这一条还没轮到"分得开：
        前者重跑一万次也是跳过，后者只是还没轮到。
        """
        rows = self._db.connect().execute(
            "SELECT i.id, i.campaign_id, i.action, i.target, i.snapshot, i.state, i.reason,"
            " c.dyn_id, c.up_uid, c.title, c.up_name FROM tidy_item i"
            " JOIN campaign c ON c.id = i.campaign_id"
            " WHERE i.plan_id = ? AND i.state = 'pending' ORDER BY i.id",
            (plan_id,),
        )
        return [dict(r) for r in rows]

    def selectable_items(self, plan_id: int) -> Sequence[dict[str, object]]:
        """清单里还能被勾选或取消的条目。

        只有 `pending`（还没动）和 `dropped`（人手动取消）在这两个状态之间来回，
        已经执行过的不参与 —— 勾选是"要不要做"的决定，不能改写"已经做了"。
        """
        rows = self._db.connect().execute(
            "SELECT i.id, i.campaign_id, i.action, i.state, i.reason,"
            " c.dyn_id, c.up_uid, c.title, c.up_name FROM tidy_item i"
            " JOIN campaign c ON c.id = i.campaign_id"
            " WHERE i.plan_id = ? AND i.state IN ('pending', 'dropped') ORDER BY i.id",
            (plan_id,),
        )
        return [dict(r) for r in rows]

    def drop_item(self, item_id: int, *, reason: str, now: datetime) -> bool:
        """把一条从"待执行"改成"人不要了"。

        带 `state = 'pending'` 约束是必须的：这个函数会被界面来回调用，如果不限定
        起点状态，一次误点就能把已经执行成功的条目改写成"未执行"，让事后取证失真。
        返回是否真的改了，调用方据此统计，而不是假设自己改成功了。
        """
        with self._db.write() as conn:
            cursor = conn.execute(
                "UPDATE tidy_item SET state = 'dropped', reason = ?, executed_at = ?"
                " WHERE id = ? AND state = 'pending'",
                (reason, stamp(now), item_id),
            )
        return cursor.rowcount > 0

    def revive_item(self, item_id: int, *, now: datetime) -> bool:
        """反悔：取消掉的条目重新勾上。理由会被清空，因为它已经不再是"被取消"。"""
        with self._db.write() as conn:
            cursor = conn.execute(
                "UPDATE tidy_item SET state = 'pending', reason = '', executed_at = NULL"
                " WHERE id = ? AND state = 'dropped'",
                (item_id,),
            )
        return cursor.rowcount > 0

    def counts_of(self, plan_id: int) -> dict[str, int]:
        rows = self._db.connect().execute(
            "SELECT state, COUNT(*) AS n FROM tidy_item WHERE plan_id = ? GROUP BY state",
            (plan_id,),
        )
        return {str(row["state"]): int(row["n"]) for row in rows}

    def recent_plans(self, account_id: int, *, limit: int = 10) -> Sequence[dict[str, object]]:
        """最近的计划，带条目计数。界面靠它回到上一次没做完的清单。"""
        rows = self._db.connect().execute(
            "SELECT p.id, p.state, p.note, p.created_at, p.previewed_at, p.confirmed_at,"
            " p.finished_at,"
            " (SELECT COUNT(*) FROM tidy_item i WHERE i.plan_id = p.id) AS total,"
            " (SELECT COUNT(*) FROM tidy_item i WHERE i.plan_id = p.id"
            "  AND i.state = 'pending') AS pending,"
            " (SELECT COUNT(*) FROM tidy_item i WHERE i.plan_id = p.id"
            "  AND i.state = 'done') AS done"
            " FROM tidy_plan p WHERE p.account_id = ? ORDER BY p.id DESC LIMIT ?",
            (account_id, limit),
        )
        return [dict(row) for row in rows]

    def plan_of(self, plan_id: int) -> dict[str, object] | None:
        row = (
            self._db.connect()
            .execute("SELECT * FROM tidy_plan WHERE id = ?", (plan_id,))
            .fetchone()
        )
        return dict(row) if row else None

    def latest_plan(self, account_id: int) -> dict[str, object] | None:
        row = (
            self._db.connect()
            .execute(
                "SELECT * FROM tidy_plan WHERE account_id = ? ORDER BY id DESC LIMIT 1",
                (account_id,),
            )
            .fetchone()
        )
        return dict(row) if row else None

    def state_of(self, plan_id: int) -> str:
        row = (
            self._db.connect()
            .execute("SELECT state FROM tidy_plan WHERE id = ?", (plan_id,))
            .fetchone()
        )
        return str(row["state"]) if row else ""

    def finish_item(
        self,
        item_id: int,
        *,
        state: str,
        reason: str = "",
        target: str = "",
        now: datetime,
    ) -> None:
        """收尾一条。`state` 只有三种合法值：done / failed / skipped。

        把状态作为参数而不是 `ok: bool`，是因为"跳过"不是失败也不是成功 ——
        它表示"这一条没动，将来也不会再动"，混进 ok 里会让统计说假话。
        """
        if state not in {"done", "failed", "skipped"}:
            raise ValueError(f"非法条目状态 {state}")
        with self._db.write() as conn:
            # target 空着表示"这次不改"，而不是"清空"。前面已经写进去的
            # "我到底删了哪一条"不能被收尾动作抹掉。
            conn.execute(
                "UPDATE tidy_item SET state = ?, reason = ?,"
                " target = CASE WHEN ? <> '' THEN ? ELSE target END,"
                " error_class = CASE WHEN ? = 'failed' THEN ? ELSE '' END,"
                " executed_at = ? WHERE id = ?",
                (state, reason, target, target, state, reason, stamp(now), item_id),
            )

    def set_target(self, item_id: int, target: str, snapshot: str = "") -> None:
        """执行前把"到底动了哪一条"写下来。崩溃后靠它知道发生过什么。"""
        with self._db.write() as conn:
            conn.execute(
                "UPDATE tidy_item SET target = ?, snapshot = ? WHERE id = ?",
                (target, snapshot, item_id),
            )

    def finish_plan(self, plan_id: int, *, state: str, note: str = "", now: datetime) -> None:
        with self._db.write() as conn:
            conn.execute(
                "UPDATE tidy_plan SET state = ?, note = ?, finished_at = ? WHERE id = ?",
                (state, note, stamp(now), plan_id),
            )

    def mark_state(self, plan_id: int, state: str, *, now: datetime) -> None:
        """只改状态，不碰 finished_at。执行开始时标 running 用。"""
        with self._db.write() as conn:
            conn.execute("UPDATE tidy_plan SET state = ? WHERE id = ?", (state, plan_id))

    def cleaned_campaign_ids(self, account_id: int) -> set[int]:
        """已经清理成功过的活动。预演时把它们标成"已清理"，不再重复提议。

        没有这一条，每跑一次预演都会把删过的活动再列一遍，而删除操作对不存在的
        目标只会失败 —— 用户会看到一堆没有意义的失败。
        """
        rows = self._db.connect().execute(
            "SELECT DISTINCT i.campaign_id FROM tidy_item i"
            " JOIN tidy_plan p ON p.id = i.plan_id"
            " WHERE p.account_id = ? AND i.state = 'done'",
            (account_id,),
        )
        return {int(r["campaign_id"]) for r in rows}

    def record_undo(
        self, item_id: int, *, action: str, target: str, snapshot: str, now: datetime
    ) -> int:
        with self._db.write() as conn:
            cursor = conn.execute(
                "INSERT OR IGNORE INTO tidy_undo"
                " (item_id, action, target, snapshot, created_at) VALUES (?, ?, ?, ?, ?)",
                (item_id, action, target, snapshot, stamp(now)),
            )
        return int(cursor.lastrowid or 0)

    def undo_of(self, item_id: int) -> dict[str, object] | None:
        """回滚记录带上条目所属的账号与活动 —— 重新关注要写进 intent，需要这两列。"""
        row = (
            self._db.connect()
            .execute(
                "SELECT u.id, u.item_id, u.action, u.target, u.snapshot, u.created_at,"
                " u.used_at, p.account_id, i.campaign_id FROM tidy_undo u"
                " JOIN tidy_item i ON i.id = u.item_id"
                " JOIN tidy_plan p ON p.id = i.plan_id WHERE u.item_id = ?",
                (item_id,),
            )
            .fetchone()
        )
        return dict(row) if row else None

    def mark_undo_used(self, undo_id: int, *, now: datetime) -> None:
        with self._db.write() as conn:
            conn.execute("UPDATE tidy_undo SET used_at = ? WHERE id = ?", (stamp(now), undo_id))


class KeepRepo:
    """清理保护名单。

    只有"保护"，没有"强制清理"：想删掉一条被保护的东西，得先从名单里移出去，
    那是又一个需要人动手的动作。少删永远比多删安全。
    """

    def __init__(self, db: Database) -> None:
        self._db = db

    def add(self, account_id: int, *, kind: str, value: str, note: str, now: datetime) -> None:
        with self._db.write() as conn:
            conn.execute(
                "INSERT INTO tidy_keep (account_id, kind, value, note, created_at)"
                " VALUES (?, ?, ?, ?, ?)"
                " ON CONFLICT(account_id, kind, value) DO UPDATE SET note = excluded.note",
                (account_id, kind, value, note, stamp(now)),
            )

    def remove(self, account_id: int, *, kind: str, value: str) -> bool:
        with self._db.write() as conn:
            cursor = conn.execute(
                "DELETE FROM tidy_keep WHERE account_id = ? AND kind = ? AND value = ?",
                (account_id, kind, value),
            )
        return cursor.rowcount > 0

    def all_of(self, account_id: int) -> Sequence[dict[str, object]]:
        rows = self._db.connect().execute(
            "SELECT kind, value, note, created_at FROM tidy_keep"
            " WHERE account_id = ? ORDER BY kind, created_at",
            (account_id,),
        )
        return [dict(row) for row in rows]


class RunEventRepo:
    """事件历史。

    只读最近 N 条：这是给人看的"刚才发生了什么"，不是审计账本。真正的账本是
    `intent` 表（谁在哪条动态上做过什么、结果如何），那个从不删。
    """

    def __init__(self, db: Database) -> None:
        self._db = db

    def append(self, *, seq: int, topic: str, payload: str, at: datetime) -> None:
        with self._db.write() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO run_event (seq, topic, payload, at) VALUES (?, ?, ?, ?)",
                (seq, topic, payload, stamp(at)),
            )

    def last_seq(self) -> int:
        row = self._db.connect().execute("SELECT MAX(seq) AS m FROM run_event").fetchone()
        return int(row["m"] or 0) if row else 0

    def recent(
        self,
        *,
        limit: int = 200,
        topics: Sequence[str] | None = None,
        issues_only: bool = False,
        account_id: int | None = None,
        before_seq: int = 0,
    ) -> Sequence[dict[str, object]]:
        sql = "SELECT seq, topic, payload, at FROM run_event"
        params: list[object] = []
        clauses = []
        if topics:
            clauses.append(f"topic IN ({','.join('?' * len(topics))})")
            params.extend(topics)
        if account_id is not None:
            clauses.append(
                "(json_extract(payload,'$.account_id')=? OR json_extract(payload,'$.account_id') IS NULL OR json_extract(payload,'$.scope')='shared')"
            )
            params.append(account_id)
        if before_seq:
            clauses.append("seq<?")
            params.append(before_seq)
        if issues_only:
            clauses.append(
                "(json_extract(payload,'$.ok')=0 OR json_extract(payload,'$.ambiguous')=1 OR json_extract(payload,'$.state')='unknown' OR json_extract(payload,'$.level') IN ('error','warning') OR json_array_length(payload,'$.problems')>0 OR topic LIKE '%failed%')"
            )
        if clauses:
            filters = (
                " AND ".join(clauses)
                .replace(
                    "json_extract(payload,",
                    "json_extract(CASE WHEN json_valid(payload) THEN payload ELSE '{}' END,",
                )
                .replace(
                    "json_array_length(payload,",
                    "json_array_length(CASE WHEN json_valid(payload) THEN payload ELSE '{}' END,",
                )
            )
            sql += " WHERE " + filters
        sql += " ORDER BY seq DESC LIMIT ?"
        params.append(max(1, limit))
        rows = self._db.connect().execute(sql, params).fetchall()
        out: list[dict[str, object]] = []
        for row in rows:
            try:
                payload = json.loads(row["payload"])
            except (TypeError, ValueError):
                # 坏掉的一条不该让整个历史页打不开，留个能看出问题的占位。
                payload = {"raw": str(row["payload"])[:200]}
            out.append(
                {
                    "seq": int(row["seq"]),
                    "topic": row["topic"],
                    "payload": payload,
                    "at": parse_stamp(row["at"]),
                }
            )
        return out

    def prune(self, *, before: datetime) -> int:
        """删掉旧记录。事件本身没有取证价值，留着只会让库越来越大。"""
        with self._db.write() as conn:
            cursor = conn.execute("DELETE FROM run_event WHERE at < ?", (stamp(before),))
        return cursor.rowcount


class SettingRepo:
    def __init__(self, db: Database) -> None:
        self._db = db

    def get(self, account_id: int, key: str, default: str = "") -> str:
        row = (
            self._db.connect()
            .execute(
                "SELECT value FROM setting WHERE account_id = ? AND key = ?", (account_id, key)
            )
            .fetchone()
        )
        return str(row["value"]) if row else default

    def put(self, account_id: int, key: str, value: str) -> None:
        with self._db.write() as conn:
            conn.execute(
                "INSERT INTO setting (account_id, key, value) VALUES (?, ?, ?)"
                " ON CONFLICT(account_id, key) DO UPDATE SET value = excluded.value",
                (account_id, key, value),
            )

    def as_dict(self, account_id: int) -> dict[str, str]:
        rows = self._db.connect().execute(
            "SELECT key, value FROM setting WHERE account_id = ?", (account_id,)
        )
        return {str(r["key"]): str(r["value"]) for r in rows}


class PoolRepo:
    """预选池的存储。

    `save()` 是整表重写（先删后插），因为队列最多 200 行，而"顺序"这件事只有整批
    写回去才不会出现半路插入把自己挤歪。整批在一个事务里，读到的永远是完整的队列。

    `items()` 顺手把活动信息 join 出来：池子里只存"哪一条、第几位、什么状态"，
    标题和开奖时间这些会变的字段不进池子 —— 存一份快照，改一次活动就得同步一次，
    迟早不一致。
    """

    def __init__(self, db: Database) -> None:
        self._db = db

    def items(self, account_id: int) -> list[dict[str, object]]:
        rows = self._db.connect().execute(
            "SELECT p.campaign_id, p.position, p.state, p.message, p.updated_at,"
            " c.dyn_id, c.title, c.reward, c.up_uid, c.up_name, CASE WHEN c.draw_business_type=1 THEN 'interactive' ELSE c.kind END AS kind, c.draw_state,c.draw_reason,c.deadline_at,"
            f" {_EXPIRES} AS expires_at,"
            " (SELECT stage FROM enrollment e WHERE e.account_id=p.account_id AND e.campaign_id=p.campaign_id) AS enrolled_stage,"
            " (SELECT reason FROM enrollment e WHERE e.account_id=p.account_id AND e.campaign_id=p.campaign_id) AS enrolled_reason,"
            " EXISTS(SELECT 1 FROM up_block b WHERE b.uid=c.up_uid AND b.active=1) AS blocked,"
            " EXISTS(SELECT 1 FROM closed_shelf s WHERE s.account_id=p.account_id AND s.campaign_id=p.campaign_id) AS shelved"
            " FROM pool_item p LEFT JOIN campaign c ON c.id = p.campaign_id"
            " WHERE p.account_id = ? ORDER BY p.position",
            (account_id,),
        )
        return [dict(row) for row in rows]

    def states(self, account_id: int) -> list[PoolItem]:
        rows = self._db.connect().execute(
            "SELECT campaign_id, position, state, message FROM pool_item"
            " WHERE account_id = ? ORDER BY position",
            (account_id,),
        )
        return [
            PoolItem(
                campaign_id=int(row["campaign_id"]),
                position=int(row["position"]),
                state=_pool_state(row["state"]),
                message=str(row["message"] or ""),
            )
            for row in rows
        ]

    def save(self, account_id: int, rows: Sequence[PoolItem], *, now: datetime) -> None:
        with self._db.write() as conn:
            conn.execute("DELETE FROM pool_item WHERE account_id = ?", (account_id,))
            conn.executemany(
                "INSERT INTO pool_item"
                " (account_id, campaign_id, position, state, message, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                [
                    (
                        account_id,
                        row.campaign_id,
                        row.position,
                        row.state.value,
                        row.message,
                        stamp(now),
                    )
                    for row in rows
                ],
            )

    def update_one(self, account_id: int, row: PoolItem, *, now: datetime) -> None:
        """跑队的时候只改一行 —— 整表重写会把并发加进来的新行冲掉。"""
        with self._db.write() as conn:
            conn.execute(
                "UPDATE pool_item SET state = ?, message = ?, updated_at = ?"
                " WHERE account_id = ? AND campaign_id = ?",
                (row.state.value, row.message, stamp(now), account_id, row.campaign_id),
            )

    def drop(self, account_id: int, campaign_id: int) -> None:
        with self._db.write() as conn:
            conn.execute(
                "DELETE FROM pool_item WHERE account_id = ? AND campaign_id = ?",
                (account_id, campaign_id),
            )

    # ---- 暂停状态 ----
    def paused(self, account_id: int) -> tuple[bool, str]:
        row = (
            self._db.connect()
            .execute("SELECT paused, note FROM pool_state WHERE account_id = ?", (account_id,))
            .fetchone()
        )
        if row is None:
            return False, ""
        return bool(row["paused"]), str(row["note"] or "")

    def set_paused(self, account_id: int, paused: bool, *, note: str = "", now: datetime) -> None:
        with self._db.write() as conn:
            conn.execute(
                "INSERT INTO pool_state (account_id, paused, note, updated_at)"
                " VALUES (?, ?, ?, ?)"
                " ON CONFLICT(account_id) DO UPDATE SET"
                " paused = excluded.paused, note = excluded.note, updated_at = excluded.updated_at",
                (account_id, int(paused), note, stamp(now)),
            )


def _pool_state(value: object) -> PoolState:
    try:
        return PoolState(str(value))
    except ValueError:
        # 库里出现没见过的状态，当成待处理总比整页崩掉好。
        return PoolState.PENDING


class InboxRepo:
    """本机的"这条我看过了"记录。

    和 B 站的已读状态无关（详见 schema 里 `inbox_seen` 的注释）。保留多久都行：
    一条消息的 key 不会重复，积累下来只是几十行文本。
    """

    def __init__(self, db: Database) -> None:
        self._db = db

    def seen(self, account_id: int) -> set[str]:
        rows = self._db.connect().execute(
            "SELECT item_key FROM inbox_seen WHERE account_id = ?", (account_id,)
        )
        return {str(row["item_key"]) for row in rows}

    def irrelevant(self, account_id: int) -> set[str]:
        return {
            r[0]
            for r in self._db.connect().execute(
                "SELECT item_key FROM inbox_review WHERE account_id=?", (account_id,)
            )
        }

    def review(self, account_id: int, key: str, irrelevant: bool) -> None:
        with self._db.write() as db:
            if irrelevant:
                db.execute(
                    "INSERT OR REPLACE INTO inbox_review VALUES(?,?,?)",
                    (account_id, key, stamp(_utcnow())),
                )
            else:
                db.execute(
                    "DELETE FROM inbox_review WHERE account_id=? AND item_key=?", (account_id, key)
                )

    def mark(self, account_id: int, keys: Sequence[str]) -> int:
        clean = [key for key in dict.fromkeys(keys) if key]
        if not clean:
            return 0
        now = stamp(_utcnow())
        with self._db.write() as conn:
            conn.executemany(
                "INSERT INTO inbox_seen (account_id, item_key, seen_at) VALUES (?, ?, ?) "
                "ON CONFLICT(account_id, item_key) DO NOTHING",
                [(account_id, key, now) for key in clean],
            )
        return len(clean)

    def forget(self, account_id: int) -> None:
        """清掉本机的已看记录。消息本身在 B 站，一条都不会少。"""
        with self._db.write() as conn:
            conn.execute("DELETE FROM inbox_seen WHERE account_id = ?", (account_id,))

    def prune(self, *, before: datetime) -> int:
        with self._db.write() as conn:
            cursor = conn.execute("DELETE FROM inbox_seen WHERE seen_at < ?", (stamp(before),))
        return cursor.rowcount


class Repositories:
    """一个容器，把几组仓储装配在一起。用例只拿它，不关心底层连接。"""

    def __init__(self, db: Database) -> None:
        self.db = db
        self.accounts = AccountRepo(db)
        self.feeds = FeedRepo(db)
        self.campaigns = CampaignRepo(db)
        self.enrollments = EnrollmentRepo(db)
        self.intents = IntentRepo(db)
        self.verdicts = VerdictRepo(db)
        self.tidy = TidyRepo(db)
        self.keep = KeepRepo(db)
        self.pool = PoolRepo(db)
        self.inbox = InboxRepo(db)
        self.events = RunEventRepo(db)
        self.settings = SettingRepo(db)

    def dumps_requirements(self, campaign_id: int) -> str:
        rows = self.db.connect().execute(
            "SELECT action, payload, optional FROM requirement WHERE campaign_id = ?",
            (campaign_id,),
        )
        return json.dumps([dict(r) for r in rows], ensure_ascii=False)
