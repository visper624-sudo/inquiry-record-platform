# -*- coding: utf-8 -*-
"""案件辅助材料接口。

合规设计（物理隔离）：
1. 材料仅以 record_id 归属案件，与 qa_item 之间**没有任何外键或字段关联**；
2. 材料不参与笔录正文组装，仅供研判算法读取；
3. 下载鉴权按案件归属机构校验，跨机构不可访问。
"""
from __future__ import annotations

import hashlib
import mimetypes
import re
from pathlib import Path
from urllib.parse import quote

from config import ALLOWED_MATERIAL_EXT, MATERIAL_DIR, MAX_UPLOAD_BYTES
from core import audit, db
from core.http import ApiError, Response
from core.router import api
from modules.records_api import _materials, _require_record

# 下载时强制以附件保存的“可执行/可渲染”类型，避免浏览器内联执行（S10）
_FORCE_OCTET_STREAM = {"text/html", "application/xhtml+xml", "image/svg+xml",
                       "application/javascript", "text/javascript", "text/xml"}


def _sniff_mime(filename: str) -> str:
    """按扩展名推断 MIME，忽略客户端声明，降低存储型 XSS 风险。"""
    mime, _ = mimetypes.guess_type(filename)
    if not mime or mime in _FORCE_OCTET_STREAM:
        return "application/octet-stream"
    return mime


def _safe_name(name: str) -> str:
    name = Path(name or "material").name
    name = re.sub(r'[\\/:*?"<>|\r\n\t]', "_", name).strip() or "material"
    return name[:120]


@api("GET", r"^/api/records/(?P<record_id>[A-Za-z0-9]+)/materials$")
def list_materials(req, record_id):
    _require_record(record_id, req.user)
    return {"items": _materials(record_id)}


@api("POST", r"^/api/records/(?P<record_id>[A-Za-z0-9]+)/materials$", roles=("police", "admin"))
def upload_material(req, record_id):
    _require_record(record_id, req.user, edit=True)
    if not req.files:
        raise ApiError("请选择需要上传的材料文件")
    uploaded = req.files[0]
    filename = _safe_name(uploaded["filename"])
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_MATERIAL_EXT:
        raise ApiError(f"不支持的文件类型 {ext or '(无扩展名)'}，可上传图片、PDF、Word、Excel 或文本")
    data = uploaded["data"] or b""
    if not data:
        raise ApiError("文件内容为空")
    if len(data) > MAX_UPLOAD_BYTES:
        raise ApiError(f"文件超过 {MAX_UPLOAD_BYTES // 1024 // 1024} MB 限制")
    digest = hashlib.sha256(data).hexdigest()
    existing = db.one("SELECT id FROM material WHERE record_id = ? AND sha256 = ?", (record_id, digest))
    if existing:
        return {"items": _materials(record_id), "duplicated": True,
                "message": "该材料已上传过，已自动去重"}
    stored_name = f"{digest[:12]}_{filename}"
    target_dir = MATERIAL_DIR / record_id
    target_dir.mkdir(parents=True, exist_ok=True)
    (target_dir / stored_name).write_bytes(data)
    material_id = db.new_id("MT", 10)
    with db.write() as conn:
        conn.execute(
            "INSERT INTO material (id, record_id, file_name, stored_name, size, mime, sha256, uploader_id, "
            "note, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (material_id, record_id, filename, stored_name, len(data), _sniff_mime(filename),
             digest, req.user["id"], req.arg("note"), db.now_iso()),
        )
    audit.write(req.user, "material.upload", "record", record_id,
                {"material_id": material_id, "file_name": filename, "size": len(data), "sha256": digest[:16]}, req)
    return {"items": _materials(record_id), "id": material_id}


@api("GET", r"^/api/materials/(?P<material_id>[A-Za-z0-9]+)/download$")
def download_material(req, material_id):
    row = db.one("SELECT * FROM material WHERE id = ?", (material_id,))
    if not row:
        raise ApiError("材料不存在", 404)
    _require_record(row["record_id"], req.user)
    path = MATERIAL_DIR / row["record_id"] / row["stored_name"]
    if not path.exists():
        raise ApiError("材料文件已丢失，请联系管理员", 410)
    headers = [("Content-Disposition", f"attachment; filename*=UTF-8''{quote(row['file_name'])}")]
    mime = row["mime"] or "application/octet-stream"
    if mime in _FORCE_OCTET_STREAM:
        mime = "application/octet-stream"
    return Response(path.read_bytes(), 200, mime, headers)


@api("DELETE", r"^/api/records/(?P<record_id>[A-Za-z0-9]+)/materials/(?P<material_id>[A-Za-z0-9]+)$",
     roles=("police", "admin"))
def delete_material(req, record_id, material_id):
    _require_record(record_id, req.user, edit=True)
    row = db.one("SELECT * FROM material WHERE id = ? AND record_id = ?", (material_id, record_id))
    if not row:
        raise ApiError("材料不存在", 404)
    path = MATERIAL_DIR / record_id / row["stored_name"]
    if path.exists():
        path.unlink()
    with db.write() as conn:
        conn.execute("DELETE FROM material WHERE id = ?", (material_id,))
    audit.write(req.user, "material.delete", "record", record_id,
                {"material_id": material_id, "file_name": row["file_name"]}, req)
    return {"items": _materials(record_id)}
