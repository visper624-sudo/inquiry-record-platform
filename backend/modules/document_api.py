# -*- coding: utf-8 -*-
"""笔录文书预览与 DOCX 导出接口。"""
from __future__ import annotations

import io
from urllib.parse import quote

from core import audit
from core.http import Response
from core.router import api
from modules.records_api import _qa_items, _flow_state, _require_record
from services import docx_svc


def _model(record, user):
    items = _qa_items(record["id"])
    flow_state = _flow_state(record, items)
    owner = {
        "name": record.get("police_name") or user.get("name", ""),
        "badge_no": record.get("police_badge") or user.get("badge_no", ""),
        "org_path": user.get("org_path", ""),
    }
    return docx_svc.build_doc_model(record, items, flow_state, owner)


@api("GET", r"^/api/records/(?P<record_id>[A-Za-z0-9]+)/document$")
def document_preview(req, record_id):
    record = _require_record(record_id, req.user)
    return {"document": _model(record, req.user)}


@api("GET", r"^/api/records/(?P<record_id>[A-Za-z0-9]+)/document.docx$")
def document_export(req, record_id):
    record = _require_record(record_id, req.user)
    model = _model(record, req.user)
    path = docx_svc.render_docx(model)
    audit.write(req.user, "document.export", "record", record_id,
                {"case_no": record["case_no"], "file": path.name,
                 "answered": model["meta"]["answered_items"],
                 "filtered": model["meta"]["filtered_items"]}, req)
    filename = docx_svc.export_filename(model)
    headers = [
        ("Content-Disposition", f"attachment; filename*=UTF-8''{quote(filename)}"),
        ("X-Document-Name", quote(filename)),
    ]
    data = path.read_bytes()
    return Response(
        data, 200,
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers,
    )


@api("GET", r"^/api/records/(?P<record_id>[A-Za-z0-9]+)/document/text$")
def document_text(req, record_id):
    """返回纯文本版文书（用于在线核验/复制）。"""
    record = _require_record(record_id, req.user)
    model = _model(record, req.user)
    buffer = io.StringIO()
    buffer.write(f"{model['org_header']}\n{model['title']}\n\n")
    for section in model["sections"]:
        buffer.write(f"【{section['title']}】\n")
        for item in section["items"]:
            buffer.write(f"问：{item['question']}\n答：{item['answer']}\n")
    return {"text": buffer.getvalue(), "document": model}
