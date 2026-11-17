-- 智能问询笔录平台 数据库结构（SQLite）
-- 说明：material（材料）与 qa_item（正文问答）之间不建立任何外键，实现「材料物理隔离」。

CREATE TABLE IF NOT EXISTS org (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    parent_id   TEXT,
    level       INTEGER NOT NULL DEFAULT 1,
    code        TEXT,
    sort_no     INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS user (
    id            TEXT PRIMARY KEY,
    badge_no      TEXT UNIQUE NOT NULL,
    name          TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL CHECK (role IN ('police', 'analyst', 'admin')),
    org_id        TEXT NOT NULL,
    title         TEXT DEFAULT '',
    status        TEXT NOT NULL DEFAULT 'active',
    created_at    TEXT NOT NULL,
    last_login_at TEXT,
    FOREIGN KEY (org_id) REFERENCES org (id)
);

CREATE TABLE IF NOT EXISTS session (
    token      TEXT PRIMARY KEY,
    user_id    TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    ip         TEXT,
    ua         TEXT
);

CREATE TABLE IF NOT EXISTS template (
    template_id TEXT PRIMARY KEY,
    code        TEXT UNIQUE,
    name        TEXT NOT NULL,
    category    TEXT NOT NULL,
    description TEXT NOT NULL,
    signals     TEXT NOT NULL,
    keywords    TEXT NOT NULL,
    is_general  INTEGER NOT NULL DEFAULT 0,
    sections    TEXT NOT NULL,
    version     TEXT DEFAULT '1.0',
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS record (
    id                TEXT PRIMARY KEY,
    case_no           TEXT UNIQUE NOT NULL,
    case_category     TEXT NOT NULL,
    brief             TEXT NOT NULL,
    reporter_name     TEXT DEFAULT '',
    reporter_phone    TEXT DEFAULT '',
    reporter_id_no    TEXT DEFAULT '',
    report_time       TEXT DEFAULT '',
    case_location     TEXT DEFAULT '',
    unit              TEXT DEFAULT '',
    org_id            TEXT NOT NULL,
    police_user_id    TEXT NOT NULL,
    stage             TEXT NOT NULL DEFAULT 'intake',
    selected_templates TEXT NOT NULL DEFAULT '[]',
    flow_excluded     TEXT NOT NULL DEFAULT '[]',
    elapsed_sec       INTEGER NOT NULL DEFAULT 0,
    snapshot          TEXT NOT NULL DEFAULT '{}',
    external_source   TEXT DEFAULT '',
    external_case_id  TEXT DEFAULT '',
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    completed_at      TEXT,
    is_archived       INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (org_id) REFERENCES org (id),
    FOREIGN KEY (police_user_id) REFERENCES user (id)
);
CREATE INDEX IF NOT EXISTS idx_record_stage ON record (stage);
CREATE INDEX IF NOT EXISTS idx_record_org ON record (org_id);

CREATE TABLE IF NOT EXISTS qa_item (
    id         TEXT PRIMARY KEY,
    record_id  TEXT NOT NULL,
    section    TEXT NOT NULL,
    question   TEXT NOT NULL,
    answer     TEXT NOT NULL DEFAULT '',
    source     TEXT NOT NULL DEFAULT 'manual',
    flow       TEXT,
    field      TEXT,
    sort_no    INTEGER NOT NULL DEFAULT 0,
    is_deleted INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (record_id) REFERENCES record (id)
);
CREATE INDEX IF NOT EXISTS idx_qa_record ON qa_item (record_id, is_deleted);

CREATE TABLE IF NOT EXISTS material (
    id          TEXT PRIMARY KEY,
    record_id   TEXT NOT NULL,
    file_name   TEXT NOT NULL,
    stored_name TEXT NOT NULL,
    size        INTEGER NOT NULL DEFAULT 0,
    mime        TEXT DEFAULT '',
    sha256      TEXT DEFAULT '',
    uploader_id TEXT DEFAULT '',
    note        TEXT DEFAULT '',
    created_at  TEXT NOT NULL,
    FOREIGN KEY (record_id) REFERENCES record (id)
);
CREATE INDEX IF NOT EXISTS idx_material_record ON material (record_id);

CREATE TABLE IF NOT EXISTS advice (
    id         TEXT PRIMARY KEY,
    record_id  TEXT NOT NULL,
    kind       TEXT NOT NULL,
    title      TEXT NOT NULL,
    content    TEXT NOT NULL,
    priority   INTEGER NOT NULL DEFAULT 3,
    status     TEXT NOT NULL DEFAULT 'pending',
    engine     TEXT NOT NULL DEFAULT 'rule',
    evidence   TEXT NOT NULL DEFAULT '[]',
    payload    TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    adopted_at TEXT,
    FOREIGN KEY (record_id) REFERENCES record (id)
);
CREATE INDEX IF NOT EXISTS idx_advice_record ON advice (record_id);

CREATE TABLE IF NOT EXISTS audit_log (
    id          TEXT PRIMARY KEY,
    user_id     TEXT,
    badge_no    TEXT,
    org_id      TEXT,
    action      TEXT NOT NULL,
    target_type TEXT DEFAULT '',
    target_id   TEXT DEFAULT '',
    detail      TEXT DEFAULT '{}',
    ip          TEXT DEFAULT '',
    ua          TEXT DEFAULT '',
    prev_hash   TEXT DEFAULT '',
    hash        TEXT DEFAULT '',
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_log (created_at);

CREATE TABLE IF NOT EXISTS import_job (
    id          TEXT PRIMARY KEY,
    file_name   TEXT DEFAULT '',
    source_type TEXT DEFAULT 'text',
    content     TEXT DEFAULT '',
    qa_pairs    TEXT DEFAULT '[]',
    report      TEXT DEFAULT '{}',
    created_by  TEXT DEFAULT '',
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS external_case (
    id             TEXT PRIMARY KEY,
    system         TEXT NOT NULL,
    case_no        TEXT NOT NULL,
    case_category  TEXT NOT NULL,
    brief          TEXT NOT NULL,
    reporter_name  TEXT DEFAULT '',
    reporter_phone TEXT DEFAULT '',
    unit           TEXT DEFAULT '',
    report_time    TEXT DEFAULT '',
    location       TEXT DEFAULT '',
    officer        TEXT DEFAULT '',
    status         TEXT DEFAULT '受案'
);

CREATE TABLE IF NOT EXISTS app_config (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
