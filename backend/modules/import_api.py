# -*- coding: utf-8 -*-
"""历史笔录导入与转存接口。"""
from __future__ import annotations

import json

from config import CASE_CATEGORIES, DEMO_DIR
from core import audit, db
from core.http import ApiError
from core.router import api
from services import importer_svc


@api("POST", r"^/api/import/parse$", roles=("police", "admin"))
def parse_import(req):
    text = req.arg("text")
    filename = req.arg("filename") or "粘贴文本.txt"
    source_type = "text"
    if req.files:
        uploaded = req.files[0]
        filename = uploaded["filename"]
        try:
            text, source_type = importer_svc.extract_text(filename, uploaded["data"])
        except ValueError as exc:
            raise ApiError(str(exc)) from exc
    if not text or not text.strip():
        raise ApiError("请上传笔录文件或粘贴笔录文本")
    try:
        analysis = importer_svc.analyze_text(text, filename, source_type)
    except ValueError as exc:
        raise ApiError(str(exc)) from exc
    job_id = db.new_id("IJ", 10)
    with db.write() as conn:
        conn.execute(
            "INSERT INTO import_job (id, file_name, source_type, content, qa_pairs, report, created_by, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (job_id, filename, source_type, text[:20000], db.json_dump(analysis["qa_pairs"]),
             db.json_dump({k: analysis[k] for k in ("flow_state", "stats", "gaps", "key_gaps", "summary",
                                                   "draft", "outline")}),
             req.user["id"], db.now_iso()),
        )
    audit.write(req.user, "import.parse", "import_job", job_id,
                {"filename": filename, "pairs": analysis["stats"]["total_pairs"],
                 "key_gaps": len(analysis["key_gaps"])}, req)
    return {"job_id": job_id, **analysis}


@api("POST", r"^/api/import/commit$", roles=("police", "admin"))
def commit_import(req):
    job_id = req.arg("job_id")
    job = db.one("SELECT * FROM import_job WHERE id = ?", (job_id,))
    if not job:
        raise ApiError("导入任务不存在或已过期，请重新解析", 404)
    # 归属校验：普通账号只能转存自己解析的任务，防止拿他人 job_id 越权（审查报告 S3）
    if job["created_by"] != req.user["id"] and req.user["role"] != "admin":
        raise ApiError("无权使用该导入任务", 403, "job_forbidden")
    report = db.json_load(job["report"], {})
    pairs = db.json_load(job["qa_pairs"], [])
    if not pairs:
        raise ApiError("该导入任务没有可用的问答对")
    category = req.arg("case_category") or report.get("draft", {}).get("suggested_category") or "其他"
    if category not in CASE_CATEGORIES:
        category = "其他"
    brief = req.arg("brief")
    if len(brief) < 10:
        first_answer = next((p["answer"] for p in pairs if p.get("answer")), "")
        brief = req.arg("filename") or job["file_name"] or "历史笔录导入"
        brief = f"历史笔录回溯导入：{brief}；样本答复：{first_answer[:60]}"[:200]
    case_no = req.arg("case_no") or _next_case_no()
    record_id = db.new_id("RC", 10)
    now = db.now_iso()
    with db.write() as conn:
        conn.execute(
            "INSERT INTO record (id, case_no, case_category, brief, reporter_name, reporter_phone, "
            "reporter_id_no, report_time, case_location, unit, org_id, police_user_id, stage, "
            "selected_templates, snapshot, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, '', ?, ?, ?, ?, ?, 'inquiry', '[]', ?, ?, ?)",
            (record_id, case_no, category, brief, req.arg("reporter_name"), req.arg("reporter_phone"),
             now, req.arg("case_location"), req.user.get("org_name", ""), req.user["org_id"],
             req.user["id"], db.json_dump({"imported_from": job["file_name"], "job_id": job_id}), now, now),
        )
        sort_counter: dict[str, int] = {}
        for pair in pairs:
            section = pair.get("section") or "process"
            sort_counter[section] = sort_counter.get(section, -1) + 1
            conn.execute(
                "INSERT INTO qa_item (id, record_id, section, question, answer, source, flow, field, sort_no, "
                "is_deleted, created_at, updated_at) VALUES (?, ?, ?, ?, ?, 'imported', ?, ?, ?, 0, ?, ?)",
                (db.new_id("QA", 12), record_id, section, pair["question"], pair.get("answer", ""),
                 pair.get("flow"), pair.get("field"), sort_counter[section], now, now),
            )
    audit.write(req.user, "import.commit", "record", record_id,
                {"job_id": job_id, "case_no": case_no, "pairs": len(pairs)}, req)
    return {
        "record_id": record_id,
        "case_no": case_no,
        "redirect": f"/workbench.html?record={record_id}",
        "gaps": report.get("key_gaps", []),
    }


def _next_case_no() -> str:
    from datetime import datetime

    prefix = "JC" + datetime.now().strftime("%Y%m%d")
    count = db.scalar("SELECT COUNT(*) FROM record WHERE case_no LIKE ?", (prefix + "%",), 0) or 0
    while True:
        count += 1
        candidate = f"{prefix}{count:03d}"
        if not db.one("SELECT id FROM record WHERE case_no = ?", (candidate,)):
            return candidate


@api("GET", r"^/api/import/samples$", roles=("police", "admin"))
def import_samples(req):
    """演示用：返回两份预置历史笔录文本（虚构脱敏）。"""
    path = DEMO_DIR / "history_samples.json"
    if not path.exists():
        return {"items": []}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {"items": [
        {"name": item["name"], "description": item["description"],
         "expected_gaps": item.get("expected_gaps", []), "text": item["text"]}
        for item in data
    ]}


@api("GET", r"^/api/import/jobs$", roles=("police", "admin", "analyst"))
def list_jobs(req):
    if req.user["role"] == "admin":
        rows = db.rows(
            "SELECT id, file_name, source_type, created_at, created_by FROM import_job "
            "ORDER BY created_at DESC LIMIT 20")
    else:
        rows = db.rows(
            "SELECT id, file_name, source_type, created_at, created_by FROM import_job "
            "WHERE created_by = ? ORDER BY created_at DESC LIMIT 20", (req.user["id"],))
    return {"items": rows}
