# -*- coding: utf-8 -*-
"""历史笔录解析导入：文档/文本 → 问答对 → 章节归类 → 五流回溯报告 → 继续问询草稿。"""
from __future__ import annotations

import io
import re
from pathlib import PurePath

from config import SECTIONS
from services import fiveflow_svc

Q_LINE = re.compile(r"^\s*(?:\*\*)?(?:问|Q|问题|询问)(?:\*\*)?\s*[：:]\s*(.*)$")
A_LINE = re.compile(r"^\s*(?:\*\*)?(?:答|A|回答|答复|陈述)(?:\*\*)?\s*[：:]\s*(.*)$")
Q_ONLY = re.compile(r"^\s*问\s*$")
A_ONLY = re.compile(r"^\s*答\s*$")

SECTION_RULES: list[tuple[str, str]] = [
    ("evidence", r"截图|证据|记录|保存|留存|报案材料|凭证|流水照片"),
    ("opening", r"听清楚|听明白|如实陈述|如实回答|权利义务|告知|录音录像|全程同步|依法向你询问|核对笔录|"
                r"请说明你的姓名|你的姓名|身份证号|户籍|住址|职业"),
    ("loss", r"转账|汇款|充值|付款|金额|多少钱|卡号|银行卡|银行|开户|损失|流水|支付|账户|账号|收款"),
    ("contact", r"联系|添加|好友|加群|引流|广告|私信|怎么认识|认识|扫码|推广|自称|冒充|什么身份|"
                r"打电话|接到|来电|电话号码|手机号|联系方式"),
    ("process", r"下载|安装|APP|软件|平台|注册|共享|操作|流程|经过|导师|客服|登录|网址|链接|通缉令|"
                r"视频会议|怎么让|为什么|报案|什么事情|发现|被骗"),
]


def extract_text(filename: str, data: bytes) -> tuple[str, str]:
    """从上传文件抽取纯文本，返回 (文本, 来源类型)。"""
    suffix = PurePath(filename or "").suffix.lower()
    if suffix == ".docx":
        from docx import Document

        doc = Document(io.BytesIO(data))
        lines = [p.text for p in doc.paragraphs]
        for table in doc.tables:
            for row in table.rows:
                lines.append(" ".join(cell.text for cell in row.cells))
        return "\n".join(lines), "docx"
    if suffix == ".pdf":
        raise ValueError("暂不支持 PDF 直接解析，请粘贴文本或转换为 DOCX/TXT 后导入")
    for encoding in ("utf-8-sig", "utf-8", "gb18030", "gbk"):
        try:
            return data.decode(encoding), "text"
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", "ignore"), "text"


def parse_qa(text: str) -> list[dict]:
    """把笔录文本切分为问答对，兼容多种前缀写法。"""
    lines = [line.rstrip() for line in (text or "").replace("\r\n", "\n").split("\n")]
    pairs: list[dict] = []
    current: dict | None = None
    for raw in lines:
        line = raw.strip().strip("\ufeff")
        if not line:
            continue
        # 跳过明显的文档标题行
        if not pairs and current is None and re.search(r"笔录$|记录$|情况说明$", line) and len(line) <= 20:
            continue
        if Q_ONLY.match(line):
            if current and (current["question"] or current["answer"]):
                pairs.append(current)
            current = {"question": "", "answer": ""}
            continue
        if A_ONLY.match(line):
            if current is None:
                current = {"question": "", "answer": ""}
            continue
        q_match = Q_LINE.match(line)
        a_match = A_LINE.match(line)
        if q_match:
            if current and (current["question"] or current["answer"]):
                pairs.append(current)
            current = {"question": q_match.group(1).strip(), "answer": ""}
        elif a_match:
            if current is None:
                current = {"question": "", "answer": ""}
            piece = a_match.group(1).strip()
            current["answer"] = (current["answer"] + "\n" + piece).strip() if current["answer"] else piece
        elif current is not None:
            if not current["question"]:
                current["question"] = line
            else:
                current["answer"] = (current["answer"] + "\n" + line).strip() if current["answer"] else line
    if current and (current["question"] or current["answer"]):
        pairs.append(current)
    # 清洗与过滤
    cleaned = []
    for pair in pairs:
        question = pair["question"].strip()
        answer = pair["answer"].strip()
        if not question and not answer:
            continue
        if not question:
            question = "（原文未标注问句）"
        cleaned.append({"question": question, "answer": answer})
    return cleaned


def classify_section(question: str, index: int) -> str:
    """按关键词 + 位置启发式归类到五个章节。

    关键词优先；无命中时首问归入固定开头，其余归入被骗经过。
    """
    for key, pattern in SECTION_RULES:
        if re.search(pattern, question):
            return key
    return "opening" if index == 0 else "process"


def analyze_text(text: str, filename: str = "", source_type: str = "text") -> dict:
    pairs = parse_qa(text)
    if not pairs:
        raise ValueError("未识别到「问：/答：」结构的问答内容，请检查文本格式")
    items = []
    for idx, pair in enumerate(pairs):
        section = classify_section(pair["question"], idx)
        flow, field, conf = fiveflow_svc.infer_flow_field(pair["question"], section)
        items.append({
            "id": f"IMP{idx + 1:03d}",
            "section": section,
            "question": pair["question"],
            "answer": pair["answer"],
            "flow": flow,
            "field": field,
            "inferred": bool(flow),
            "infer_confidence": conf,
            "source": "imported",
        })
    flow_state = fiveflow_svc.analyze(items, excluded_flows=[])

    outline = []
    for section in SECTIONS:
        section_items = [item for item in items if item["section"] == section["key"]]
        outline.append({
            "key": section["key"],
            "title": section["title"],
            "questions": [
                {
                    "text": item["question"],
                    "answer": item["answer"],
                    "flow": item["flow"],
                    "field": item["field"],
                    "key_item": False,
                    "source_template": "历史笔录导入",
                    "source_template_name": "历史笔录导入",
                }
                for item in section_items
            ],
        })

    answered = sum(1 for item in items if item["answer"].strip())
    distribution = {s["key"]: sum(1 for i in items if i["section"] == s["key"]) for s in SECTIONS}
    missing_sections = [s["title"] for s in SECTIONS if distribution[s["key"]] == 0]
    return {
        "filename": filename,
        "source_type": source_type,
        "qa_pairs": items,
        "outline": outline,
        "flow_state": flow_state,
        "stats": {
            "total_pairs": len(items),
            "answered_pairs": answered,
            "empty_pairs": len(items) - answered,
            "section_distribution": distribution,
            "missing_sections": missing_sections,
            "inferred_fields": sum(1 for i in items if i["inferred"]),
        },
        "gaps": [
            {"flow": g["flow_name"], "label": g["label"], "key_item": g["key_item"], "hint": g["hint"]}
            for g in flow_state.get("gaps", [])
        ],
        "key_gaps": [
            {"flow": g["flow_name"], "label": g["label"], "hint": g["hint"]}
            for g in flow_state.get("key_gaps", [])
        ],
        "summary": _summary(items, flow_state),
        "draft": {
            "suggested_title": (filename or "历史笔录").rsplit(".", 1)[0][:40],
            "suggested_category": _guess_category("\n".join(i["question"] + i["answer"] for i in items)),
        },
    }


def _summary(items: list[dict], flow_state: dict) -> str:
    key_names = [g["flow_name"] + "-" + g["label"] for g in flow_state.get("key_gaps", [])]
    text = f"共解析问答 {len(items)} 组，五流整体完整度 {flow_state.get('completeness', 0)}%。"
    if key_names:
        text += "重点缺口：" + "、".join(key_names[:4]) + "。建议针对上述要素开展补充问询。"
    else:
        text += "未发现关键要素缺口。"
    return text


def _guess_category(text: str) -> str:
    rules = [
        ("虚假网络投资理财", r"投资|理财|荐股|导师|平台充值|提现"),
        ("刷单返利", r"刷单|返利|垫付|任务|佣金"),
        ("冒充电商物流客服", r"客服|退款|理赔|包裹丢失|物流"),
        ("冒充公检法", r"公安|检察院|法院|通缉|安全账户|洗钱"),
        ("网络婚恋交友", r"婚恋|男友|女友|交友|相亲"),
        ("虚假征信", r"征信|校园贷|注销|额度"),
        ("虚假网络贷款", r"贷款|放款|解冻费|保证金|额度提升"),
        ("网络游戏虚假交易", r"游戏|装备|账号交易|充值点券"),
        ("裸聊敲诈勒索", r"裸聊|视频敲诈|威胁|不雅"),
        ("虚假中奖", r"中奖|抽奖|兑奖|奖品"),
    ]
    for category, pattern in rules:
        if re.search(pattern, text):
            return category
    return "其他"
