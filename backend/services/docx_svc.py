# -*- coding: utf-8 -*-
"""DOCX 红头询问笔录生成引擎。

排版口径（公安文印标准）：
  A4；上 3.7cm / 下 3.5cm / 左 2.8cm / 右 2.6cm；
  红头：二号（22pt）红色加粗居中，段后 24pt；
  正文：仿宋_GB2312 三号（16pt），固定行距 30pt，首行缩进 2 字符；
  「问：」加粗、「答：」常规；未作答条目一律过滤；落款含签名区与日期。
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

from config import DOC_LAYOUT, EXPORT_DIR, FLOW_LABELS, SECTIONS

CN_NUM = ["一", "二", "三", "四", "五", "六", "七", "八", "九", "十"]


# ------------------------------------------------------------------ 文书模型
def _duration_text(seconds: int) -> str:
    seconds = max(0, int(seconds or 0))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours} 小时 {minutes} 分钟"
    if minutes:
        return f"{minutes} 分钟 {secs} 秒"
    return f"{secs} 秒"


def _org_header(org_path: str, unit: str) -> str:
    """红头取机关全称：优先使用组织机构路径的前两级（市局 + 分局），回落到办案单位。"""
    if org_path:
        parts = [p for p in re.split(r"\s*/\s*", org_path) if p]
        if len(parts) >= 2:
            second = parts[1]
            if second.startswith(parts[0]):
                second = second[len(parts[0]):]
            return parts[0] + second
        if parts:
            return parts[0]
    return unit or "公安机关"


def build_doc_model(record: dict, qa_items: list[dict], flow_state: dict, user: dict) -> dict:
    """组装文书数据模型：预览页与 DOCX 共用同一份模型，保证所见即所得。"""
    sections = []
    total, answered, filtered = 0, 0, 0
    for section in SECTIONS:
        items = []
        for qa in qa_items:
            if qa.get("is_deleted") or qa.get("section") != section["key"]:
                continue
            total += 1
            answer = (qa.get("answer") or "").strip()
            if not answer:
                filtered += 1
                continue
            answered += 1
            items.append({"question": qa["question"].strip(), "answer": answer, "source": qa.get("source", "manual")})
        if items:
            sections.append({"key": section["key"], "title": section["title"], "items": items})

    start = record.get("created_at") or ""
    completed = record.get("completed_at") or ""
    start_text = _fmt_dt(start)
    end_text = _fmt_dt(completed) if completed else "（未完成）"
    org_path = user.get("org_path") or ""
    org_header = _org_header(org_path, record.get("unit", ""))

    model = {
        "title": "询问笔录",
        "org_header": org_header,
        "doc_no": f"{record.get('case_no', '')}",
        "case": {
            "case_no": record.get("case_no", ""),
            "category": record.get("case_category", ""),
            "brief": record.get("brief", ""),
            "unit": record.get("unit", "") or org_path,
            "police_name": user.get("name", ""),
            "police_badge": user.get("badge_no", ""),
            "reporter_name": record.get("reporter_name", ""),
            "reporter_phone": record.get("reporter_phone", ""),
            "reporter_id_no": record.get("reporter_id_no", ""),
            "report_time": record.get("report_time", "") or start_text,
            "location": record.get("case_location", "") or (record.get("unit", "") or org_path),
            "inquiry_start": start_text,
            "inquiry_end": end_text,
            "duration": _duration_text(record.get("elapsed_sec", 0)),
        },
        "sections": sections,
        "signature": {
            "date_text": datetime.now().strftime("%Y 年 %m 月 %d 日"),
            "org_name": org_header,
        },
        "meta": {
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "total_items": total,
            "answered_items": answered,
            "filtered_items": filtered,
        },
    }
    model["checks"] = compliance_checks(model, record, flow_state)
    return model


def _fmt_dt(value: str) -> str:
    if not value:
        return ""
    try:
        dt = datetime.strptime(value, "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return value
    return dt.strftime("%Y 年 %m 月 %d 日 %H 时 %M 分")


def compliance_checks(model: dict, record: dict, flow_state: dict) -> list[dict]:
    """文书合规自检：导出前提示，不阻断导出。"""
    meta = model["meta"]
    checks = [
        {
            "item": "未作答条目过滤",
            "passed": True,
            "detail": f"共过滤未作答问答 {meta['filtered_items']} 项，正文仅保留已作答内容",
        },
        {
            "item": "红头机关名称",
            "passed": bool(model["org_header"]),
            "detail": model["org_header"] or "缺少发文机关，请在组织机构中维护",
        },
        {
            "item": "被询问人身份信息",
            "passed": bool(record.get("reporter_name")) and bool(record.get("reporter_id_no") or record.get("reporter_phone")),
            "detail": f"{record.get('reporter_name') or '未填写'} / {record.get('reporter_id_no') or '未填写身份证号'}",
        },
        {
            "item": "问询时长记录",
            "passed": int(record.get("elapsed_sec", 0)) > 0,
            "detail": f"系统计时 {model['case']['duration']}",
        },
    ]
    key_gaps = flow_state.get("key_gaps", [])
    checks.append({
        "item": "五流关键要素完整性",
        "passed": not key_gaps,
        "detail": ("存在重点缺口：" + "、".join(f"{g['flow_name']}-{g['label']}" for g in key_gaps[:4]))
        if key_gaps else "五流关键要素均已收集",
    })
    fund = next((f for f in flow_state.get("flows", []) if f["key"] == "fund"), None)
    if fund:
        total_loss = next((x for x in fund["fields"] if x["key"] == "total_loss"), None)
        amounts = [x for x in fund["fields"] if x["key"] in ("transfer_amount", "total_loss") and x["status"] == "collected"]
        checks.append({
            "item": "金额一致性",
            "passed": bool(total_loss and total_loss["status"] == "collected"),
            "detail": f"损失总额：{total_loss['value'] if total_loss and total_loss['value'] else '未采集'}"
                      f"；金额类要素已采集 {len(amounts)} 项",
        })
    checks.append({
        "item": "落款签名区",
        "passed": True,
        "detail": "已生成被询问人签名区、询问人与记录人署名区及日期栏",
    })
    return checks


# ------------------------------------------------------------------ DOCX 渲染
def _set_run_font(run, name: str, size: int, bold: bool = False, color: RGBColor | None = None):
    run.font.name = name
    run.font.size = Pt(size)
    run.font.bold = bold
    if color is not None:
        run.font.color.rgb = color
    rpr = run._element.get_or_add_rPr()
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = OxmlElement("w:rFonts")
        rpr.append(rfonts)
    rfonts.set(qn("w:eastAsia"), name)
    rfonts.set(qn("w:ascii"), name)
    rfonts.set(qn("w:hAnsi"), name)


def _para(doc, text: str = "", font: str = "", size: int = 16, bold: bool = False,
          align=None, indent_chars: float = 0, line_spacing: int = 30,
          space_before: int = 0, space_after: int = 0):
    p = doc.add_paragraph()
    if align is not None:
        p.alignment = align
    fmt = p.paragraph_format
    if indent_chars:
        fmt.first_line_indent = Pt(size * indent_chars)
    fmt.line_spacing = Pt(line_spacing)
    fmt.space_before = Pt(space_before)
    fmt.space_after = Pt(space_after)
    if text:
        run = p.add_run(text)
        if font:
            _set_run_font(run, font, size, bold)
    return p


def _add_page_number_footer(section) -> None:
    footer = section.footer
    paragraph = footer.paragraphs[0]
    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = paragraph.add_run("第 ")
    _set_run_font(run, DOC_LAYOUT["body_fallback"], 10.5)
    for instr in ("PAGE", "NUMPAGES"):
        fld_begin = OxmlElement("w:fldChar")
        fld_begin.set(qn("w:fldCharType"), "begin")
        instr_el = OxmlElement("w:instrText")
        instr_el.set(qn("xml:space"), "preserve")
        instr_el.text = f" {instr} "
        fld_end = OxmlElement("w:fldChar")
        fld_end.set(qn("w:fldCharType"), "end")
        fld_run = paragraph.add_run()
        fld_run._r.append(fld_begin)
        fld_run._r.append(instr_el)
        fld_run._r.append(fld_end)
        if instr == "PAGE":
            mid = paragraph.add_run(" 页  共 ")
            _set_run_font(mid, DOC_LAYOUT["body_fallback"], 10.5)
    tail = paragraph.add_run(" 页")
    _set_run_font(tail, DOC_LAYOUT["body_fallback"], 10.5)


def _add_red_header(model: dict, doc) -> None:
    header = _para(doc, model["org_header"], font=DOC_LAYOUT["header_fallback"],
                   size=DOC_LAYOUT["header_size"], bold=True,
                   align=WD_ALIGN_PARAGRAPH.CENTER, line_spacing=36,
                   space_after=DOC_LAYOUT["title_spacing"],
                   )
    for run in header.runs:
        run.font.color.rgb = RGBColor(0xC0, 0x00, 0x00)
    # 红色分隔线
    ppr = header._p.get_or_add_pPr()
    pbdr = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    bottom.set(qn("w:val"), "single")
    bottom.set(qn("w:sz"), "12")
    bottom.set(qn("w:space"), "6")
    bottom.set(qn("w:color"), "C00000")
    pbdr.append(bottom)
    ppr.append(pbdr)


def _add_meta_block(model: dict, doc) -> None:
    case = model["case"]
    rows = [
        ("文书编号", model["doc_no"]),
        ("案件类别", case["category"]),
        ("询问时间", f"{case['inquiry_start']} 至 {case['inquiry_end']}（{case['duration']}）"),
        ("询问地点", case["location"]),
        ("询问人", f"{case['police_name']}（警号 {case['police_badge']}）"),
        ("记录人", case["police_name"]),
        ("被询问人", case["reporter_name"] or "（待填）"),
        ("身份证号", case["reporter_id_no"] or "（待填）"),
        ("联系方式", case["reporter_phone"] or "（待填）"),
    ]
    _para(doc, "　", line_spacing=10)
    for label, value in rows:
        p = _para(doc, "", size=DOC_LAYOUT["body_size"], line_spacing=28)
        run = p.add_run(f"{label}：")
        _set_run_font(run, DOC_LAYOUT["body_fallback"], DOC_LAYOUT["body_size"], bold=True)
        run2 = p.add_run(value or "　")
        _set_run_font(run2, DOC_LAYOUT["body_fallback"], DOC_LAYOUT["body_size"])
    _para(doc, "　", line_spacing=10)


def _add_body(model: dict, doc) -> None:
    body_font = DOC_LAYOUT["body_fallback"]
    size = DOC_LAYOUT["body_size"]
    spacing = DOC_LAYOUT["line_spacing"]
    # 模板已含「固定开头」告知性问题时不再重复生成开场白，避免同一告知出现两次
    has_opening = any(section["key"] == "opening" and section["items"] for section in model["sections"])
    if not has_opening:
        intro = _para(doc, "", size=size, line_spacing=spacing, indent_chars=2)
        run = intro.add_run("问：")
        _set_run_font(run, body_font, size, bold=True)
        intro_run = intro.add_run(
            f"我们是{model['org_header']}民警（警号 {model['case']['police_badge']}），"
            f"现就你报案的{model['case']['category']}案件依法向你询问，请你如实回答。你听清楚了吗？"
        )
        _set_run_font(intro_run, body_font, size)
        answer = _para(doc, "", size=size, line_spacing=spacing, indent_chars=2)
        run = answer.add_run("答：")
        _set_run_font(run, body_font, size)
        run2 = answer.add_run("听清楚了，我会如实回答。")
        _set_run_font(run2, body_font, size)

    for idx, section in enumerate(model["sections"]):
        heading = _para(doc, "", size=size, line_spacing=spacing, indent_chars=2, space_before=6)
        run = heading.add_run(f"{CN_NUM[idx] if idx < len(CN_NUM) else idx + 1}、{section['title']}")
        _set_run_font(run, body_font, size, bold=True)
        for item in section["items"]:
            q = _para(doc, "", size=size, line_spacing=spacing, indent_chars=2)
            r1 = q.add_run("问：")
            _set_run_font(r1, body_font, size, bold=True)
            r2 = q.add_run(item["question"])
            _set_run_font(r2, body_font, size)
            a = _para(doc, "", size=size, line_spacing=spacing, indent_chars=2)
            r3 = a.add_run("答：")
            _set_run_font(r3, body_font, size)
            r4 = a.add_run(item["answer"])
            _set_run_font(r4, body_font, size)


def _add_signature(model: dict, doc) -> None:
    size = DOC_LAYOUT["body_size"]
    body_font = DOC_LAYOUT["body_fallback"]
    _para(doc, "（以下无正文）", size=size, line_spacing=30, indent_chars=2, space_before=12)
    _para(doc, "", line_spacing=16)
    p = _para(doc, "", size=size, line_spacing=34, align=WD_ALIGN_PARAGRAPH.RIGHT)
    run = p.add_run("被询问人（签名）：　　　　　　　　　")
    _set_run_font(run, body_font, size)
    p2 = _para(doc, "", size=size, line_spacing=34, align=WD_ALIGN_PARAGRAPH.RIGHT)
    run = p2.add_run(f"日　期：{model['signature']['date_text']}　　")
    _set_run_font(run, body_font, size)
    _para(doc, "", line_spacing=18)
    p3 = _para(doc, "", size=size, line_spacing=34, align=WD_ALIGN_PARAGRAPH.RIGHT)
    run = p3.add_run("询问人（签名）：　　　　　记录人（签名）：　　　　　")
    _set_run_font(run, body_font, size)
    p4 = _para(doc, "", size=size, line_spacing=34, align=WD_ALIGN_PARAGRAPH.RIGHT)
    run = p4.add_run(f"{model['signature']['org_name']}（盖章）　　")
    _set_run_font(run, body_font, size)
    _para(doc, "", size=11, line_spacing=20, align=WD_ALIGN_PARAGRAPH.CENTER,
          space_before=12)
    # 生成水印说明（页脚上方）
    note = _para(doc, "本笔录由智能问询笔录平台生成，数据为脱敏演示数据，仅用于系统功能演示。",
                 size=9, line_spacing=16, align=WD_ALIGN_PARAGRAPH.CENTER)
    for run in note.runs:
        run.font.color.rgb = RGBColor(0x80, 0x80, 0x80)


def render_docx(model: dict, output_path: Path | None = None) -> Path:
    """把文书模型渲染为 DOCX 文件，返回文件路径。"""
    doc = Document()
    section = doc.sections[0]
    section.page_width = Cm(21.0)
    section.page_height = Cm(29.7)
    section.top_margin = Cm(DOC_LAYOUT["page"]["top"])
    section.bottom_margin = Cm(DOC_LAYOUT["page"]["bottom"])
    section.left_margin = Cm(DOC_LAYOUT["page"]["left"])
    section.right_margin = Cm(DOC_LAYOUT["page"]["right"])

    normal = doc.styles["Normal"]
    normal.font.name = DOC_LAYOUT["body_fallback"]
    normal.font.size = Pt(DOC_LAYOUT["body_size"])
    rpr = normal.element.get_or_add_rPr()
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = OxmlElement("w:rFonts")
        rpr.append(rfonts)
    rfonts.set(qn("w:eastAsia"), DOC_LAYOUT["body_fallback"])

    _add_page_number_footer(section)
    _add_red_header(model, doc)

    title = _para(doc, model["title"], font=DOC_LAYOUT["header_fallback"],
                  size=DOC_LAYOUT["header_size"], bold=True,
                  align=WD_ALIGN_PARAGRAPH.CENTER, line_spacing=40, space_after=8)
    for run in title.runs:
        run.font.color.rgb = RGBColor(0x00, 0x00, 0x00)

    _add_meta_block(model, doc)
    _add_body(model, doc)
    _add_signature(model, doc)

    core = doc.core_properties
    core.title = f"{model['case']['case_no']} {model['title']}"
    core.author = "智能问询笔录平台"
    core.subject = model["case"]["category"]

    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    if output_path is None:
        output_path = EXPORT_DIR / f"{model['case']['case_no']}_{model['title']}.docx"
    doc.save(str(output_path))
    return output_path


def export_filename(model: dict) -> str:
    return f"{model['case']['case_no']}_询问笔录.docx"


def flow_summary_text(flow_state: dict) -> str:
    """五流覆盖摘要（用于解析结果页与报告）。"""
    parts = []
    for flow in flow_state.get("flows", []):
        parts.append(f"{flow['name']}：{flow['status_label']}（{flow['collected_count']}/{flow['required_total']}）")
    return "；".join(parts)
