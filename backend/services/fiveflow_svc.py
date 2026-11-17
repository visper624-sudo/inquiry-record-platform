# -*- coding: utf-8 -*-
"""五流要素分析引擎。

设计要点：
1. 字段口径全部来自 data/five_flow_fields.json（可在设置页覆盖），不写死在代码里；
2. 抽取采用「问题元数据定向 → 字段启发式 → 全文正则兜底」三层策略；
3. 覆盖度按「必填要素收集率」计算，关键要素缺失即判『重点缺口』；
4. 不涉及必须由人工标记，引擎只给建议，不自动判定。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from config import DATA_DIR, FLOW_LABELS, FLOW_STATUS_LABELS, SECTION_TITLE
from core import db

FIELD_FILE = DATA_DIR / "five_flow_fields.json"
_config_cache: dict = {"mtime": 0, "data": None}


# ------------------------------------------------------------------ 配置
def load_field_config(force: bool = False) -> dict:
    """读取字段口径：优先 app_config 覆盖，其次 JSON 文件，按 mtime 缓存。"""
    override = db.get_config("five_flow_fields")
    if override and not force:
        return override
    mtime = FIELD_FILE.stat().st_mtime if FIELD_FILE.exists() else 0
    if force or _config_cache["mtime"] != mtime:
        _config_cache["data"] = json.loads(FIELD_FILE.read_text(encoding="utf-8"))
        _config_cache["mtime"] = mtime
    return _config_cache["data"]


def flows() -> list[dict]:
    return load_field_config()["flows"]


def flow_map() -> dict[str, dict]:
    return {f["key"]: f for f in flows()}


def field_lookup() -> dict[tuple[str, str], dict]:
    lookup = {}
    for flow in flows():
        for field in flow["fields"]:
            lookup[(flow["key"], field["key"])] = field
    return lookup


def save_field_config(payload: dict) -> dict:
    """设置页保存：校验后写入 app_config 覆盖。"""
    if not isinstance(payload.get("flows"), list) or len(payload["flows"]) != 5:
        raise ValueError("字段口径必须包含五条流")
    for flow in payload["flows"]:
        if flow.get("key") not in FLOW_LABELS:
            raise ValueError(f"未知流: {flow.get('key')}")
        fields = flow.get("fields", [])
        if not isinstance(fields, list) or len(fields) > 30:
            raise ValueError(f"{flow['key']} 字段数量不合法")
        for field in fields:
            if "key" not in field or "label" not in field:
                raise ValueError("字段缺少 key/label")
            patterns = field.get("patterns", [])
            if not isinstance(patterns, list) or len(patterns) > 12:
                raise ValueError(f"{field['key']} 的正则数量不合法")
            for pattern in patterns:
                _validate_pattern(pattern)
    db.set_config("five_flow_fields", payload)
    _config_cache["mtime"] = 0
    return payload


def reset_field_config() -> dict:
    with db.write() as conn:
        conn.execute("DELETE FROM app_config WHERE key = 'five_flow_fields'")
    _config_cache["mtime"] = 0
    return load_field_config(force=True)


# ------------------------------------------------------------------ 基础工具
# 检测嵌套量词（如 (a+)+ / (.*)*），这类正则可能造成 ReDoS（审查报告 P10）
_NESTED_QUANT = re.compile(r"\((?:[^()]|\([^()]*\))*[+*]\)[+*]")


def _validate_pattern(pattern) -> None:
    text = str(pattern or "")
    if not text:
        raise ValueError("正则不能为空")
    if len(text) > 200:
        raise ValueError(f"正则过长（>200 字符）：{text[:40]}…")
    if _NESTED_QUANT.search(text):
        raise ValueError(f"正则包含嵌套量词，存在 ReDoS 风险：{text[:40]}")
    try:
        re.compile(text)
    except re.error as exc:
        raise ValueError(f"正则不合法：{text[:40]}（{exc}）") from exc


def _clean(value: str) -> str:
    value = (value or "").strip()
    value = value.strip("，。；、：:,. \t\"'“”「」（）()【】")
    return re.sub(r"\s{2,}", " ", value)


def _sentence_around(text: str, start: int, end: int, radius: int = 26) -> str:
    left = max(0, start - radius)
    right = min(len(text), end + radius)
    snippet = text[left:right].replace("\n", " ")
    return ("…" if left > 0 else "") + snippet.strip() + ("…" if right < len(text) else "")


def luhn_ok(number: str) -> bool:
    digits = [int(c) for c in number if c.isdigit()]
    if len(digits) < 12:
        return False
    checksum = 0
    parity = len(digits) % 2
    for idx, digit in enumerate(digits):
        if idx % 2 == parity:
            digit *= 2
            if digit > 9:
                digit -= 9
        checksum += digit
    return checksum % 10 == 0


# ------------------------------------------------------------------ 字段启发式
FIELD_HEURISTICS: dict[str, list[str]] = {
    "counterparty_name": [
        r"(?:户名|开户名|收款人姓名|对方姓名|姓名|名字|名叫|名字叫|名为)\s*[：:是为]?\s*[\"'“「]?([\u4e00-\u9fa5]{2,4})[\"'”」]?",
        r"(?:对方|收款人)(?:叫|是)\s*[\"'“「]?([\u4e00-\u9fa5]{2,4})[\"'”」]?",
    ],
    "app_name": [
        r"(?:APP|App|app|软件|应用)(?:的名称|名称|名叫|叫|名为|是)?\s*[：:是为]?\s*[\"'“「]?([\u4e00-\u9fa5A-Za-z0-9·]{2,12})[\"'”」]?",
        r"下载(?:了|安装)(?:一款|一个)?\s*[\"'“「]?([\u4e00-\u9fa5A-Za-z0-9·]{2,12})[\"'”」]?\s*(?:APP|App|app|软件|应用)?",
    ],
    "qr_code": [r"(二维码|扫码下载|扫一扫|群内链接|安装包|应用市场|下载链接)"],
    "screen_share": [r"(屏幕共享|共享屏幕|远程控制|远程协助|视频会议|腾讯会议|钉钉会议|Zoom)"],
    "pay_channel": [r"(手机银行|网上银行|网银|微信支付|微信转账|支付宝|云闪付|数字人民币|银行卡转账|柜台转账|ATM|扫码支付|第三方支付|POS机)"],
    "caller_role": [r"自称\s*([\u4e00-\u9fa5A-Za-z0-9]{2,16})"],
    "suspect_claim_identity": [
        r"自称\s*([\u4e00-\u9fa5A-Za-z0-9]{2,16})",
        r"(?:说|称)自己是\s*([\u4e00-\u9fa5A-Za-z0-9]{2,16})",
    ],
    "victim_identity": [
        r"(?<!\d)(\d{17}[\dXx])(?!\d)",
        r"(?:姓名|名字|名叫)\s*[：:是为]?\s*([\u4e00-\u9fa5]{2,4})",
        r"(?:户籍|住址|家住|户籍地)\s*[：:是为]?\s*([^，。；\n]{4,30})",
    ],
    "sms_content": [r"(?:短信|信息)[^。；\n]{0,12}(验证码|链接|通知|涉嫌|冻结)[^。；\n]{0,20}"],
    "login_ip": [r"\b((?:\d{1,3}\.){3}\d{1,3})\b"],
    "address": [r"(?:地址|寄往|邮寄至|收货地)\s*[：:是为]?\s*([^，。；\n]{4,40})"],
    "sender_name": [r"寄件人\s*[：:是为]?\s*([\u4e00-\u9fa5]{2,4})"],
    "receiver_name": [r"收件人\s*[：:是为]?\s*([\u4e00-\u9fa5]{2,4})"],
    "parcel_content": [r"(?:包裹|快递|内含|装有)[^。；\n]{0,20}"],
    "agent_person": [r"(中间人|代办人|业务员|客户经理|客服经理|助理|拉我进群|推荐人)"],
    "gang_role": [r"(引流|话术|洗钱|取现|卡农|跑分|车手|技术组|财务)"],
    "acquaintance": [r"(?:通过|在)\s*([^，。；\n]{2,20})\s*(?:认识|添加|加为好友|关注)"],
}

# 允许「短答直取」的文本类字段：答案很短且问题已定向时，直接把答案作为要素值
TEXT_FIELDS = {
    "counterparty_name", "victim_identity", "suspect_claim_identity", "caller_role",
    "agent_person", "gang_role", "acquaintance", "parcel_content", "address",
    "sender_name", "receiver_name", "app_name", "qr_code", "pay_channel",
    "sms_content", "call_duration", "login_ip",
}
# 结构化字段：必须由正则命中，禁止短答直取（避免把噪声当卡号）
STRICT_FIELDS = {
    "transfer_time", "transfer_amount", "payer_card", "counterparty_card", "total_loss",
    "counterparty_bank", "serial_no", "call_number", "call_time", "url_domain", "chat_account",
    "express_no", "victim_contact",
}


def _apply_patterns(text: str, patterns: list[str]) -> tuple[str, str] | None:
    for pattern in patterns:
        try:
            match = re.search(pattern, text)
        except re.error:
            continue
        if not match:
            continue
        value = match.group(1) if match.groups() else match.group(0)
        value = _clean(value or "")
        if value:
            return value, _sentence_around(text, match.start(), match.end())
    return None


# ------------------------------------------------------------------ 问题推断
INFER_RULES: list[tuple[str, str, str, float]] = [
    (r"(对方|收款|诈骗|嫌疑).{0,8}(卡号|账号|账户)", "fund", "counterparty_card", 0.7),
    (r"(转出|本人|我的|你的).{0,8}(卡号|银行卡|账号)", "fund", "payer_card", 0.68),
    (r"(户名|开户名|收款人姓名)", "fund", "counterparty_name", 0.7),
    (r"(开户行|开户银行|哪个银行|什么银行)", "fund", "counterparty_bank", 0.7),
    (r"(流水号|订单号|交易号|凭证号)", "fund", "serial_no", 0.7),
    (r"(损失|被骗|合计|一共|总共).{0,8}(金额|多少|数额)", "fund", "total_loss", 0.7),
    (r"(转账|汇款|充值).{0,10}(金额|多少|数额|钱数)", "fund", "transfer_amount", 0.66),
    (r"(转账|汇款|充值|付款).{0,10}(时间|几点|哪天|什么时候)", "fund", "transfer_time", 0.66),
    (r"(支付|转账).{0,8}(方式|通道|渠道|途径)", "fund", "pay_channel", 0.62),
    (r"(什么时间|什么时候|几点|哪天).{0,10}(电话|联系|通话)", "communication", "call_time", 0.66),
    (r"(电话|通话|联系).{0,10}(什么时间|什么时候|几点|哪天)", "communication", "call_time", 0.66),
    (r"(通话).{0,6}(时长|多久|多长时间)", "communication", "call_duration", 0.65),
    (r"(号码|手机号码|电话号码|来电显示|主叫|被叫)", "communication", "call_number", 0.68),
    (r"(短信|验证码)", "communication", "sms_content", 0.6),
    (r"(网址|域名|链接|网站|网页)", "network", "url_domain", 0.7),
    (r"(APP|软件|应用|平台名称)", "network", "app_name", 0.7),
    (r"(二维码|扫码|下载渠道|安装包)", "network", "qr_code", 0.68),
    (r"(微信|QQ|钉钉|抖音|聊天账号|加好友|好友)", "network", "chat_account", 0.6),
    (r"(屏幕共享|共享屏幕|远程|会议软件)", "network", "screen_share", 0.66),
    (r"(IP|设备|机型|手机型号|登录地)", "network", "login_ip", 0.6),
    (r"(身份证|户籍|住址|身份信息)", "personnel", "victim_identity", 0.68),
    (r"(自称|冒充|说自己是|什么身份)", "personnel", "suspect_claim_identity", 0.7),
    (r"(中间人|代办|推荐人|谁介绍)", "personnel", "agent_person", 0.6),
    (r"(团伙|角色|分工)", "personnel", "gang_role", 0.55),
    (r"(怎么认识|如何认识|添加方式)", "personnel", "acquaintance", 0.55),
    (r"(快递单号|运单号|包裹)", "delivery", "express_no", 0.7),
    (r"(寄件人)", "delivery", "sender_name", 0.65),
    (r"(收件人)", "delivery", "receiver_name", 0.65),
    (r"(地址|寄到哪里|收货地址)", "delivery", "address", 0.6),
]

_COMPILED_INFER = [(re.compile(p), flow, field, conf) for p, flow, field, conf in INFER_RULES]


def infer_flow_field(question: str, section: str = "") -> tuple[str | None, str | None, float]:
    """从问题文本推断其指向的五流要素（用于历史笔录导入等无元数据场景）。"""
    text = question or ""
    for pattern, flow, field, conf in _COMPILED_INFER:
        if pattern.search(text):
            # 资金损失类问题在 loss 章节权重更高
            if section == "loss" and flow == "fund":
                conf = min(0.95, conf + 0.05)
            return flow, field, conf
    return None, None, 0.0


# ------------------------------------------------------------------ 单条抽取
def extract_from_qa(question: str, answer: str, flow: str | None, field: str | None,
                    section: str = "") -> list[dict]:
    """对一条问答抽取要素候选。"""
    answer = (answer or "").strip()
    if not answer:
        return []
    lookup = field_lookup()
    candidates: list[dict] = []

    def add(flow_key: str, field_key: str, value: str, confidence: float, evidence: str, method: str):
        if (flow_key, field_key) not in lookup:
            return
        value = _clean(value)
        if not value:
            return
        candidates.append({
            "flow": flow_key,
            "field": field_key,
            "value": value,
            "confidence": round(confidence, 2),
            "evidence_span": evidence[:120],
            "method": method,
            "question": question,
        })

    # 第一层：问题元数据已定向
    if flow and field and (flow, field) in lookup:
        field_def = lookup[(flow, field)]
        hit = _apply_patterns(answer, field_def.get("patterns", []))
        if not hit:
            hit = _apply_patterns(answer, FIELD_HEURISTICS.get(field, []))
        if hit:
            add(flow, field, hit[0], 0.92, hit[1], "template_field")
        elif field in TEXT_FIELDS and len(answer) <= 24:
            add(flow, field, answer, 0.7, answer, "short_answer")
        elif field in TEXT_FIELDS and len(answer) > 24:
            # 长答案中取第一个分句作为要素值
            head = re.split(r"[，。；、,;]", answer)[0]
            if 1 < len(head) <= 24:
                add(flow, field, head, 0.6, _sentence_around(answer, 0, len(head)), "first_clause")
    else:
        # 第二层：无元数据时按问题文本推断字段，再抽值
        infer_flow, infer_field, conf = infer_flow_field(question, section)
        if infer_flow and infer_field:
            field_def = lookup.get((infer_flow, infer_field), {})
            hit = _apply_patterns(answer, field_def.get("patterns", []))
            if not hit:
                hit = _apply_patterns(answer, FIELD_HEURISTICS.get(infer_field, []))
            if hit:
                add(infer_flow, infer_field, hit[0], conf, hit[1], "inferred_field")
            elif infer_field in TEXT_FIELDS and len(answer) <= 24:
                add(infer_flow, infer_field, answer, max(0.5, conf - 0.15), answer, "inferred_short")

        # 第三层：全文正则兜底（仅结构性强特征）
        sweep = _generic_sweep(answer)
        for item in sweep:
            add(item["flow"], item["field"], item["value"], item["confidence"],
                item["evidence"], "generic_sweep")

    return candidates


def _generic_sweep(text: str) -> list[dict]:
    """结构化特征兜底扫描：手机号、卡号、身份证、网址、金额、日期、快递单号。"""
    results: list[dict] = []

    def push(flow, field, value, confidence, match):
        results.append({
            "flow": flow, "field": field, "value": _clean(value), "confidence": confidence,
            "evidence": _sentence_around(text, match.start(), match.end()),
        })

    # 身份证（先扫，避免被卡号规则吃掉）
    idcard_spans = []
    for match in re.finditer(r"(?<!\d)(\d{17}[\dXx])(?!\d)", text):
        idcard_spans.append((match.start(), match.end()))
        push("personnel", "victim_identity", match.group(1), 0.72, match)

    for match in re.finditer(r"(?<!\d)1[3-9]\d{9}(?!\d)", text):
        push("communication", "call_number", match.group(0), 0.58, match)

    for match in re.finditer(r"(?<!\d)(\d{16,19})(?!\d)", text):
        if any(s <= match.start() < e for s, e in idcard_spans):
            continue
        number = match.group(1)
        # 中文姓名/流水号等常被误判，仅信任 16~19 位纯数字
        if not luhn_ok(number) and not number.startswith(("62", "43", "45", "62")):
            continue
        push("fund", "counterparty_card", number, 0.52, match)

    for match in re.finditer(r"(?:https?://)?(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,30}[a-zA-Z0-9])?\.)+"
                             r"(?:com|cn|net|top|xyz|cc|vip|shop|info|club|site|app|online|tech)"
                             r"(?:/[^\s，。；、）)]*)?", text):
        push("network", "url_domain", match.group(0), 0.66, match)

    for match in re.finditer(r"([\u4e00-\u9fa5]{2,6})\s*(?:APP|App|app|软件)", text):
        push("network", "app_name", match.group(1), 0.5, match)

    for match in re.finditer(r"\d+(?:\.\d+)?\s*(?:万元|万|元|块)", text):
        context = text[max(0, match.start() - 12):match.start()]
        field = "total_loss" if re.search(r"损失|被骗|合计|一共|总共", context) else "transfer_amount"
        push("fund", field, match.group(0), 0.5, match)

    for match in re.finditer(r"(?:20\d{2}[-/.年]\d{1,2}[-/.月]\d{1,2}日?)|(?:\d{1,2}月\d{1,2}日)", text):
        context = text[max(0, match.start() - 10):match.start()]
        field = "transfer_time" if re.search(r"转|汇|付|充", context) else "call_time"
        push("fund" if field == "transfer_time" else "communication", field, match.group(0), 0.45, match)

    for match in re.finditer(r"(?<![A-Za-z0-9])(?:[A-Z]{2}\d{9,13}|\d{12,15})(?![0-9])", text):
        push("delivery", "express_no", match.group(0), 0.5, match)

    for match in re.finditer(r"(?:QQ|qq|微信|wx|WeChat|抖音|快手|钉钉)[号：:\s]{0,3}([A-Za-z0-9_\-]{4,24})", text):
        push("network", "chat_account", match.group(1), 0.55, match)

    return results


# ------------------------------------------------------------------ 汇总分析
def analyze(qa_items: list[dict], excluded_flows: list[str] | None = None) -> dict:
    """输入问答列表，输出五流覆盖状态、缺口与证据摘要。"""
    excluded = set(excluded_flows or [])
    lookup = field_lookup()
    buckets: dict[tuple[str, str], list[dict]] = {}

    for item in qa_items:
        if item.get("is_deleted"):
            continue
        answer = (item.get("answer") or "").strip()
        if not answer:
            continue
        section = item.get("section", "")
        for cand in extract_from_qa(
            item.get("question", ""), answer, item.get("flow"), item.get("field"), section
        ):
            buckets.setdefault((cand["flow"], cand["field"]), []).append({**cand, "qa_id": item.get("id")})

    flow_states = []
    all_gaps: list[dict] = []
    total_required = 0
    total_collected = 0

    for flow in flows():
        fkey = flow["key"]
        involved = fkey not in excluded
        fields_out = []
        collected_required = 0
        required_count = 0
        gaps = []
        for field in flow["fields"]:
            required_count += 1 if field["required"] else 0
            cands = buckets.get((fkey, field["key"]), [])
            chosen = max(cands, key=lambda c: c["confidence"]) if cands else None
            conflict = False
            if chosen:
                others = [c for c in cands if _clean(c["value"]) != _clean(chosen["value"])]
                conflict = bool(others)
            warning = ""
            if chosen and field["key"] in ("payer_card", "counterparty_card"):
                digits = re.sub(r"\D", "", chosen["value"])
                if digits and not luhn_ok(digits):
                    warning = "卡号校验位不合规，请人工核对原件"
            item_status = "collected" if chosen else ("conflict" if conflict else "missing")
            if involved and field["required"]:
                total_required += 1
                if chosen:
                    collected_required += 1
                    total_collected += 1
            fields_out.append({
                "key": field["key"],
                "label": field["label"],
                "required": bool(field["required"]),
                "key_item": bool(field["key_item"]),
                "hint": field.get("hint", ""),
                "status": item_status,
                "value": chosen["value"] if chosen else "",
                "confidence": chosen["confidence"] if chosen else 0,
                "evidence_span": chosen["evidence_span"] if chosen else "",
                "source_qa_id": chosen.get("qa_id") if chosen else None,
                "method": chosen["method"] if chosen else "",
                "warning": warning,
                "conflict": conflict,
            })
            if field["required"] and not chosen:
                gaps.append({
                    "flow": fkey,
                    "flow_name": flow["name"],
                    "field": field["key"],
                    "label": field["label"],
                    "key_item": bool(field["key_item"]),
                    "hint": field.get("hint", ""),
                    "priority": flow["priority"],
                })

        key_gaps = [g for g in gaps if g["key_item"]]
        if not involved:
            status = "not_involved"
            gaps = []          # 人工标记「不涉及」的流不再产生缺口
            key_gaps = []
        elif key_gaps:
            status = "key_gap"
        elif gaps:
            status = "needs_supplement"
        else:
            status = "collected"

        coverage = round(collected_required / required_count * 100, 1) if required_count else 100.0
        flow_states.append({
            "key": fkey,
            "name": flow["name"],
            "priority": flow["priority"],
            "description": flow.get("description", ""),
            "involved": involved,
            "status": status,
            "status_label": FLOW_STATUS_LABELS[status],
            "required_total": required_count,
            "collected_count": collected_required,
            "coverage_pct": coverage,
            "radar_value": 100 if status == "not_involved" else coverage,
            "fields": fields_out,
            "gaps": gaps,
            "can_mark_not_involved": involved and not any(f["status"] == "collected" for f in fields_out),
            "collected_labels": [f["label"] for f in fields_out if f["status"] == "collected"],
            "gap_labels": [g["label"] for g in gaps],
        })
        all_gaps.extend(gaps)
    all_gaps.sort(key=lambda g: (g["priority"], 0 if g["key_item"] else 1))
    stats = {label: 0 for label in FLOW_STATUS_LABELS}
    for state in flow_states:
        stats[state["status"]] += 1
    completeness = round(total_collected / total_required * 100, 1) if total_required else 0.0

    return {
        "flows": flow_states,
        "stats": {
            "collected": stats["collected"],
            "needs_supplement": stats["needs_supplement"],
            "key_gap": stats["key_gap"],
            "not_involved": stats["not_involved"],
            "total_flows": len(flow_states),
        },
        "gaps": all_gaps,
        "key_gaps": [g for g in all_gaps if g["key_item"]],
        "completeness": completeness,
        "involved_required_total": total_required,
        "involved_collected_total": total_collected,
        "generated_at": db.now_iso(),
    }


def empty_state() -> dict:
    """未开始问询时的空态，保证前端渲染一致。"""
    return analyze([])
