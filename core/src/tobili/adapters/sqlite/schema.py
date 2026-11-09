"""表结构。

十几张表，手写 DDL。不使用 ORM 是刻意的：这套系统的关键约束（意图唯一索引、状态机
迁移）必须能被一眼读出来，而不是藏在模型声明与自动迁移之间。

外键在这里是**真的有**——`PRAGMA foreign_keys=ON` 因此不是一个装饰。
"""

from __future__ import annotations

SCHEMA_VERSION = 16

# 后加的列。`CREATE TABLE IF NOT EXISTS` 不会改动已存在的表，老库要靠 ALTER 补。
# (表名, 列名, 声明) —— 全是常量，不拼接外部输入。
ADDED_COLUMNS = (
    ("closed_shelf", "reason", "TEXT NOT NULL DEFAULT 'expired'"),
    ("campaign", "reward_locked", "INTEGER NOT NULL DEFAULT 0"),
    ("campaign", "reward_evidence", "TEXT NOT NULL DEFAULT ''"),
    ("campaign", "published_at", "TEXT"),
    ("campaign", "published_source", "TEXT NOT NULL DEFAULT ''"),
    ("campaign", "body_text", "TEXT NOT NULL DEFAULT ''"),
    ("campaign", "prize_text", "TEXT NOT NULL DEFAULT ''"),
    ("campaign", "reward_status", "TEXT NOT NULL DEFAULT 'pending'"),
    ("campaign", "draw_business_id", "TEXT NOT NULL DEFAULT ''"),
    ("campaign", "draw_business_type", "INTEGER NOT NULL DEFAULT 0"),
    ("campaign", "draw_state", "TEXT NOT NULL DEFAULT ''"),
    ("campaign", "draw_checked_at", "TEXT NOT NULL DEFAULT ''"),
    ("campaign", "draw_reason", "TEXT NOT NULL DEFAULT ''"),
    ("account", "level", "INTEGER NOT NULL DEFAULT 0"),
    ("tidy_plan", "note", "TEXT NOT NULL DEFAULT ''"),
    ("tidy_plan", "finished_at", "TEXT"),
    ("tidy_item", "target", "TEXT NOT NULL DEFAULT ''"),
    ("tidy_item", "reason", "TEXT NOT NULL DEFAULT ''"),
    # 'auto' 机器核验，'manual' 人工改判。自动核验只覆盖 auto，人工结论永远保留 ——
    # 否则人改一次、下一轮扫描又改回去，改判功能等于不存在。
    ("verdict", "source", "TEXT NOT NULL DEFAULT 'auto'"),
    ("verdict", "note", "TEXT NOT NULL DEFAULT ''"),
)

DDL = """
CREATE TABLE IF NOT EXISTS inbox_review (
 account_id INTEGER NOT NULL REFERENCES account(id), item_key TEXT NOT NULL,
 decided_at TEXT NOT NULL, PRIMARY KEY(account_id,item_key)
);
CREATE TABLE IF NOT EXISTS deadline_check (
    campaign_id INTEGER PRIMARY KEY REFERENCES campaign(id) ON DELETE CASCADE,
    body_text TEXT NOT NULL, published_at TEXT, before_at TEXT, candidate_at TEXT,
    precision TEXT NOT NULL DEFAULT '', evidence TEXT NOT NULL DEFAULT '',
    state TEXT NOT NULL, checked_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS up_block (
    uid TEXT PRIMARY KEY, name TEXT NOT NULL DEFAULT '', reason TEXT NOT NULL DEFAULT '',
    active INTEGER NOT NULL DEFAULT 1, revision INTEGER NOT NULL DEFAULT 0,
    sync_state TEXT NOT NULL DEFAULT 'local', updated_at TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS prize_edit (
    id INTEGER PRIMARY KEY, campaign_id INTEGER NOT NULL REFERENCES campaign(id),
    before_text TEXT NOT NULL, after_text TEXT NOT NULL, evidence TEXT NOT NULL DEFAULT '',
    at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS prize_review (
    campaign_id INTEGER PRIMARY KEY REFERENCES campaign(id) ON DELETE CASCADE,
    state TEXT NOT NULL DEFAULT 'pending',
    note TEXT NOT NULL DEFAULT '',
    checked_at TEXT NOT NULL DEFAULT '',
    retry_at REAL NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS result_probe (
    account_id INTEGER NOT NULL REFERENCES account(id) ON DELETE CASCADE,
    campaign_id INTEGER NOT NULL REFERENCES campaign(id) ON DELETE CASCADE,
    attempted_at TEXT NOT NULL,
    retry_at TEXT NOT NULL,
    error TEXT NOT NULL DEFAULT '',
    PRIMARY KEY(account_id,campaign_id)
);
CREATE TABLE IF NOT EXISTS result_dismissal (
    account_id INTEGER NOT NULL REFERENCES account(id),
    campaign_id INTEGER NOT NULL REFERENCES campaign(id),
    reason TEXT NOT NULL,
    share_id TEXT NOT NULL DEFAULT '',
    state TEXT NOT NULL DEFAULT 'pending',
    note TEXT NOT NULL DEFAULT '',
    at TEXT NOT NULL,
    PRIMARY KEY(account_id,campaign_id)
);
CREATE TABLE IF NOT EXISTS participation_ref (
    account_id INTEGER NOT NULL REFERENCES account(id) ON DELETE CASCADE,
    campaign_id INTEGER NOT NULL REFERENCES campaign(id) ON DELETE CASCADE,
    share_id TEXT NOT NULL DEFAULT '',
    link_state TEXT NOT NULL DEFAULT 'unscanned',
    choices TEXT NOT NULL DEFAULT '{}',
    followed_before INTEGER,
    follow_added INTEGER NOT NULL DEFAULT 0,
    origin TEXT NOT NULL DEFAULT '',
    checked_at TEXT NOT NULL DEFAULT '',
    result_state TEXT NOT NULL DEFAULT 'unknown',
    PRIMARY KEY(account_id,campaign_id)
);
CREATE TABLE IF NOT EXISTS prior_cleanup (
    account_id INTEGER NOT NULL REFERENCES account(id) ON DELETE CASCADE,
    external_id TEXT NOT NULL,
    dyn_id TEXT NOT NULL DEFAULT '',
    action TEXT NOT NULL,
    target TEXT NOT NULL DEFAULT '',
    state TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    at TEXT NOT NULL,
    PRIMARY KEY(account_id,external_id)
);
CREATE TABLE IF NOT EXISTS account (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    bili_uid       TEXT    NOT NULL UNIQUE,
    nickname       TEXT    NOT NULL DEFAULT '',
    avatar         TEXT    NOT NULL DEFAULT '',
    level          INTEGER NOT NULL DEFAULT 0,
    credential_ref TEXT    NOT NULL DEFAULT '',
    created_at     TEXT    NOT NULL,
    disabled       INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS feed (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id   INTEGER NOT NULL REFERENCES account(id) ON DELETE CASCADE,
    kind         TEXT    NOT NULL,
    target       TEXT    NOT NULL,
    label        TEXT    NOT NULL DEFAULT '',
    cursor       TEXT    NOT NULL DEFAULT '',
    last_scan_at TEXT,
    enabled      INTEGER NOT NULL DEFAULT 1,
    UNIQUE (account_id, kind, target)
);
CREATE INDEX IF NOT EXISTS ix_feed_owner ON feed(account_id, enabled);
CREATE TABLE IF NOT EXISTS feed_refresh (
    feed_id INTEGER PRIMARY KEY REFERENCES feed(id) ON DELETE CASCADE,
    next_at REAL NOT NULL DEFAULT 0,
    last_finished REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS campaign (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    dyn_id        TEXT    NOT NULL UNIQUE,
    up_uid        TEXT    NOT NULL,
    up_name       TEXT    NOT NULL DEFAULT '',
    title         TEXT    NOT NULL DEFAULT '',
    reward        TEXT    NOT NULL DEFAULT '',
    kind          TEXT    NOT NULL DEFAULT 'unknown',
    deadline_at   TEXT,
    confidence    REAL    NOT NULL DEFAULT 0,
    source_ref    TEXT    NOT NULL DEFAULT '',
    raw_ref       TEXT    NOT NULL DEFAULT '',
    first_seen_at TEXT    NOT NULL,
    updated_at    TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_campaign_deadline ON campaign(deadline_at);
CREATE INDEX IF NOT EXISTS ix_campaign_up ON campaign(up_uid);

-- 一个活动被哪些源看到过。
-- campaign.dyn_id 的唯一约束只能保证"同一条动态只有一条记录"，但一条活动常常同时
-- 出现在关注流和某个 UP 的空间里。这张表把"从哪几个源看到"留下来，既不用重复建
-- 活动，又不会丢掉覆盖面 —— 覆盖越广的活动通常在榜单上越靠前。
CREATE TABLE IF NOT EXISTS campaign_source (
    campaign_id   INTEGER NOT NULL REFERENCES campaign(id) ON DELETE CASCADE,
    feed_id       INTEGER NOT NULL REFERENCES feed(id) ON DELETE CASCADE,
    feed_label    TEXT    NOT NULL DEFAULT '',
    first_seen_at TEXT    NOT NULL,
    PRIMARY KEY (campaign_id, feed_id)
);
CREATE INDEX IF NOT EXISTS ix_campaign_source_feed ON campaign_source(feed_id);

CREATE TABLE IF NOT EXISTS requirement (
    campaign_id INTEGER NOT NULL REFERENCES campaign(id) ON DELETE CASCADE,
    action      TEXT    NOT NULL,
    payload     TEXT    NOT NULL DEFAULT '{}',
    optional    INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (campaign_id, action)
);

CREATE TABLE IF NOT EXISTS enrollment (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id  INTEGER NOT NULL REFERENCES account(id) ON DELETE CASCADE,
    campaign_id INTEGER NOT NULL REFERENCES campaign(id) ON DELETE CASCADE,
    stage       TEXT    NOT NULL DEFAULT 'planned',
    reason      TEXT    NOT NULL DEFAULT '',
    created_at  TEXT    NOT NULL,
    updated_at  TEXT    NOT NULL,
    UNIQUE (account_id, campaign_id)
);
CREATE INDEX IF NOT EXISTS ix_enrollment_stage ON enrollment(account_id, stage);

-- 幂等的唯一来源。这四列的唯一约束就是"同一件事只做一次"的物理表达。
CREATE TABLE IF NOT EXISTS intent (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id  INTEGER NOT NULL REFERENCES account(id) ON DELETE CASCADE,
    campaign_id INTEGER NOT NULL REFERENCES campaign(id) ON DELETE CASCADE,
    action      TEXT    NOT NULL,
    fingerprint TEXT    NOT NULL,
    content     TEXT    NOT NULL DEFAULT '',
    state       TEXT    NOT NULL DEFAULT 'reserved',
    error_class TEXT    NOT NULL DEFAULT '',
    message     TEXT    NOT NULL DEFAULT '',
    created_at  TEXT    NOT NULL,
    finished_at TEXT,
    UNIQUE (account_id, campaign_id, action, fingerprint)
);
CREATE INDEX IF NOT EXISTS ix_intent_open ON intent(account_id, state);

CREATE TABLE IF NOT EXISTS verdict (
    campaign_id INTEGER NOT NULL REFERENCES campaign(id) ON DELETE CASCADE,
    account_id  INTEGER NOT NULL REFERENCES account(id) ON DELETE CASCADE,
    outcome     TEXT    NOT NULL,
    decided_at  TEXT    NOT NULL,
    evidence    TEXT    NOT NULL DEFAULT '',
    PRIMARY KEY (campaign_id, account_id)
);

CREATE TABLE IF NOT EXISTS tidy_plan (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id   INTEGER NOT NULL REFERENCES account(id) ON DELETE CASCADE,
    state        TEXT    NOT NULL DEFAULT 'draft',
    note         TEXT    NOT NULL DEFAULT '',
    created_at   TEXT    NOT NULL,
    previewed_at TEXT,
    confirmed_at TEXT,
    finished_at  TEXT
);
CREATE INDEX IF NOT EXISTS ix_tidy_plan_owner ON tidy_plan(account_id, state);

CREATE TABLE IF NOT EXISTS tidy_item (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_id     INTEGER NOT NULL REFERENCES tidy_plan(id) ON DELETE CASCADE,
    campaign_id INTEGER NOT NULL REFERENCES campaign(id) ON DELETE CASCADE,
    action      TEXT    NOT NULL,
    -- 实际动了哪一条：转发填我自己那条动态的 ID，取关填 UP 的 UID。
    -- 必须落库，因为"删了什么"是唯一的事后凭据。
    target      TEXT    NOT NULL DEFAULT '',
    snapshot    TEXT    NOT NULL DEFAULT '',
    state       TEXT    NOT NULL DEFAULT 'pending',
    reason      TEXT    NOT NULL DEFAULT '',
    error_class TEXT    NOT NULL DEFAULT '',
    executed_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_tidy_item_plan ON tidy_item(plan_id, state);

-- 后悔药。现在只有取关能真的回滚（重新关注），删转发没救 —— 这一列如实记录，
-- 不是"以后会支持"的占位。`used_at` 非空表示已经用过这次回滚机会。
CREATE TABLE IF NOT EXISTS tidy_undo (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id    INTEGER NOT NULL UNIQUE REFERENCES tidy_item(id) ON DELETE CASCADE,
    action     TEXT    NOT NULL,
    target     TEXT    NOT NULL,
    snapshot   TEXT    NOT NULL DEFAULT '',
    created_at TEXT    NOT NULL,
    used_at    TEXT
);

CREATE TABLE IF NOT EXISTS setting (
    account_id INTEGER NOT NULL,
    key        TEXT    NOT NULL,
    value      TEXT    NOT NULL DEFAULT '',
    PRIMARY KEY (account_id, key)
);

-- 清理保护名单。命中就不清，理由会原样显示在预演清单里。
-- 这是"宁可少删"那一边的开关：名单只保护，从不授予权限。
CREATE TABLE IF NOT EXISTS tidy_keep (
    account_id INTEGER NOT NULL REFERENCES account(id) ON DELETE CASCADE,
    kind       TEXT    NOT NULL,
    value      TEXT    NOT NULL,
    note       TEXT    NOT NULL DEFAULT '',
    created_at TEXT    NOT NULL,
    PRIMARY KEY (account_id, kind, value)
);

-- 事件落库。内存里那个 deque 容量 500 且随进程消失，界面一刷新就什么都不剩。
-- `seq` 由事件总线给，不用 AUTOINCREMENT：两边必须是同一个号，否则重启后
-- 浏览器带着旧 Last-Event-ID 重连会把新事件全丢掉。
CREATE TABLE IF NOT EXISTS run_event (
    seq      INTEGER PRIMARY KEY,
    topic    TEXT    NOT NULL,
    payload  TEXT    NOT NULL DEFAULT '{}',
    at       TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_run_event_at ON run_event(at);

-- 预选池：一个**有顺序**的待参与队列。
--
-- 它和 enrollment 不是一回事：enrollment 是"我已经报了这个活动"的账，
-- 这张表是"我打算按这个顺序报"。所以成功了就把行删掉（账已经记到 enrollment 里），
-- 失败的留着等人看一眼 —— 保留失败项是暂停队列的前提。
CREATE TABLE IF NOT EXISTS closed_shelf (
    account_id INTEGER NOT NULL REFERENCES account(id) ON DELETE CASCADE,
    campaign_id INTEGER NOT NULL REFERENCES campaign(id) ON DELETE CASCADE,
    PRIMARY KEY(account_id,campaign_id)
);

CREATE TABLE IF NOT EXISTS pool_item (
    account_id  INTEGER NOT NULL REFERENCES account(id) ON DELETE CASCADE,
    campaign_id INTEGER NOT NULL REFERENCES campaign(id) ON DELETE CASCADE,
    position    INTEGER NOT NULL,
    state       TEXT    NOT NULL DEFAULT 'pending',
    message     TEXT    NOT NULL DEFAULT '',
    updated_at  TEXT    NOT NULL,
    PRIMARY KEY (account_id, campaign_id)
);
CREATE INDEX IF NOT EXISTS ix_pool_order ON pool_item(account_id, position);

-- 队列的暂停状态按账号存。单独一张表而不是塞进 setting：setting 是给
-- "全局开关"用的，这个是账号级的运行时状态。
CREATE TABLE IF NOT EXISTS pool_state (
    account_id INTEGER PRIMARY KEY REFERENCES account(id) ON DELETE CASCADE,
    paused     INTEGER NOT NULL DEFAULT 0,
    note       TEXT    NOT NULL DEFAULT '',
    updated_at TEXT    NOT NULL
);

-- 收件箱里"我看过了"的本机记录。
--
-- 注意它**不是**已读回执：B 站没有把一条消息标为已读的公开接口，红点只能人自己
-- 点掉。这张表只用来"别每次都提醒我同一条"。所以键是远端 id 拼出来的稳定标识，
-- 而不是行号 —— 行号每次拉取都会变。
CREATE TABLE IF NOT EXISTS inbox_seen (
    account_id INTEGER NOT NULL REFERENCES account(id) ON DELETE CASCADE,
    item_key   TEXT    NOT NULL,
    seen_at    TEXT    NOT NULL,
    PRIMARY KEY (account_id, item_key)
);
CREATE TABLE IF NOT EXISTS feed_health (
    feed_id INTEGER PRIMARY KEY REFERENCES feed(id) ON DELETE CASCADE,
    attempted_at TEXT NOT NULL, error TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS catalog_removed (
    dyn_id TEXT PRIMARY KEY, reason TEXT NOT NULL, removed_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS deadline_override (
    campaign_id INTEGER PRIMARY KEY REFERENCES campaign(id) ON DELETE CASCADE,
    value TEXT NOT NULL, at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS deadline_origin (
    campaign_id INTEGER PRIMARY KEY REFERENCES campaign(id) ON DELETE CASCADE,
    source TEXT NOT NULL, value TEXT NOT NULL
);
"""
