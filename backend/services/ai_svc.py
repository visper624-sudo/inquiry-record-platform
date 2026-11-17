# -*- coding: utf-8 -*-
"""AI 侦查研判服务：大模型（DeepSeek / OpenAI 兼容 / Ollama）+ 规则引擎双通道。

设计原则：
1. 默认接入 DeepSeek 开放平台，**真实调用大模型**完成类型判定、侦查指引与追问生成；
2. 大模型输出必须是受约束的 JSON，服务端逐字段校验，只允许引用「当前五流缺口」中的字段；
3. 未配置密钥、网络异常、输出非法时**自动回退规则引擎**，并在返回 meta 中标注原因；
4. 所有建议均为「待采纳」，必须民警人工点击「加入问询」才写入正式问答（合规红线）。
"""
from __future__ import annotations

import ipaddress
import json
import os
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from config import (DEFAULT_LLM_CONFIG, FLOW_LABELS, LLM_ALLOWED_HOSTS, LLM_ALLOW_PRIVATE,
                    LLM_KEY_FILE)
from core import db

# ------------------------------------------------------------------ 追问话术库（规则引擎）
ADVICE_QUESTIONS: dict[tuple[str, str], str] = {
    ("fund", "transfer_time"): "你一共向对方转账几次？请分别说明每次转账的具体日期与时间。",
    ("fund", "transfer_amount"): "你每次转账的金额分别是多少？请说明单笔金额与累计转账金额。",
    ("fund", "pay_channel"): "你是通过哪种方式转账的？是手机银行、微信、支付宝还是柜台转账？",
    ("fund", "payer_card"): "你转出资金使用的是哪张银行卡？请提供完整卡号及开户银行。",
    ("fund", "counterparty_bank"): "对方收款账户属于哪个银行？请说明具体的开户行名称。",
    ("fund", "counterparty_card"): "请明确对方收款银行卡号（或支付账号）的完整号码，如有多张请逐张说明。",
    ("fund", "counterparty_name"): "对方收款账户的开户人姓名是什么？",
    ("fund", "serial_no"): "转账凭证上的流水号或订单号是多少？",
    ("fund", "total_loss"): "你累计损失的总金额是多少？是否还包含手续费、保证金等附加费用？",
    ("communication", "call_number"): "对方使用哪个电话号码与你联系？请说明来电或去电号码。",
    ("communication", "call_time"): "你与对方的通话发生在什么时间？请说明首次及后续通话的时段。",
    ("communication", "call_duration"): "每次通话大约持续多长时间？累计通话多少次？",
    ("communication", "caller_role"): "来电方在电话中自称是什么身份？",
    ("communication", "sms_content"): "对方是否给你发送过短信或验证码？请说明短信内容。",
    ("network", "qr_code"): "你是通过什么渠道下载该APP的？是否扫描了对方提供的二维码？",
    ("network", "url_domain"): "涉案平台的网址或域名是什么？请尽可能完整提供。",
    ("network", "app_name"): "你下载使用的涉诈APP名称是什么？是在哪个渠道下载的？",
    ("network", "chat_account"): "对方的微信、QQ或其他聊天工具账号是多少？",
    ("network", "screen_share"): "对方是否要求你开启屏幕共享或远程控制？使用的是哪款软件？",
    ("network", "login_ip"): "涉案平台登录时的IP地址、手机型号等设备信息你是否知情？",
    ("personnel", "victim_identity"): "请提供你的姓名、身份证号、户籍地址与职业信息。",
    ("personnel", "victim_contact"): "你的联系电话是多少？",
    ("personnel", "suspect_claim_identity"): "对方自称是什么身份？例如冒充电商客服、公检法人员或投资导师。",
    ("personnel", "agent_person"): "是否有中间人、代办人或业务员参与？请说明其具体身份。",
    ("personnel", "gang_role"): "你了解对方团伙的内部角色分工吗？例如谁引流、谁谈单、谁收款。",
    ("personnel", "acquaintance"): "你是如何与对方认识并添加好友的？",
    ("delivery", "express_no"): "是否涉及快递包裹寄递？请提供快递单号与承运公司。",
    ("delivery", "sender_name"): "涉案包裹的寄件人是谁？请说明姓名与联系方式。",
    ("delivery", "receiver_name"): "涉案包裹的收件人是谁？",
    ("delivery", "address"): "涉案包裹的寄送地址是什么？",
    ("delivery", "parcel_content"): "包裹内装有哪些物品？价值多少？",
}

FLOW_SECTION = {
    "fund": "loss",
    "communication": "process",
    "network": "process",
    "personnel": "contact",
    "delivery": "process",
}

CATEGORY_GUIDES: dict[str, list[str]] = {
    "虚假网络投资理财": [
        "立即对涉案一级卡账户发起紧急止付，同步核查关联二、三级账户资金流向。",
        "提取涉案APP安装包与后台域名，送技术部门开展电子数据勘验与落地侦查。",
        "调取受害人聊天记录，锁定荐股群、导师账号及其绑定手机号，开展人员落地。",
    ],
    "刷单返利": [
        "梳理受害人参与的刷单群与返利话术证据，提取群主、客服账号。",
        "核查受害人收款二维码、付款订单号，追溯资金最终归集账户。",
        "对涉案“垫付任务”发布平台开展溯源，固定虚假订单截图。",
    ],
    "冒充电商物流客服": [
        "调取来电号码话单，核查是否为虚拟号段或境外号码，开展溯源。",
        "调取受害人退款、理赔操作记录，固定仿冒客服页面的网址与域名。",
        "对接电商平台核实是否存在信息泄露，排查内部人员倒卖订单数据风险。",
    ],
    "冒充公检法": [
        "第一时间核查受害人手机是否安装远程控制或屏幕共享软件，指导删除。",
        "调取涉诈“警官证”“通缉令”图片，提取文书编号开展溯源。",
        "对受害人已转账账户开展紧急止付，同步查询涉案账户开户信息。",
    ],
    "网络婚恋交友": [
        "固定婚恋平台注册信息、实名认证信息与登录IP，锁定嫌疑人真实身份。",
        "核查涉诈投资平台后台，提取充值、提现记录与客服账号。",
        "梳理嫌疑人使用的社交账号矩阵，开展串并案分析。",
    ],
    "虚假征信": [
        "调取受害人贷款、征信查询记录，固定“注销校园贷”话术证据。",
        "核查涉案金融机构名称真伪，取证仿冒客服工号与短信。",
        "对受害人已转账账户开展紧急止付，排查同类受害群体。",
    ],
    "冒充领导熟人": [
        "核查涉案微信、QQ账号注册信息与登录IP，锁定实际使用人。",
        "调取受害人转账凭证，对收款账户开展止付与资金流分析。",
        "核实被冒充人员的真实身份与是否知情，排除内外勾连。",
    ],
    "虚假网络贷款": [
        "固定涉诈贷款APP与客服联系方式，提取“解冻费”“保证金”话术证据。",
        "对收款账户开展止付，核查是否为跑分平台归集账户。",
        "排查同类受害人在同一APP的报案记录，开展串并侦查。",
    ],
    "裸聊敲诈勒索": [
        "立即固定聊天记录、录屏视频与威胁话术，指导受害人停止转账。",
        "核查敲诈方使用的聊天账号与收款二维码，开展溯源。",
        "对已转账资金开展止付，研判是否存在跨境作案特征。",
    ],
}

DEFAULT_GUIDES = [
    "对涉案资金开展紧急止付，核查一级卡开户信息与资金流向。",
    "提取涉案APP、网址、聊天账号等电子证据，送技术部门勘验。",
    "梳理受害人陈述中的时间线，形成案件证据链并移送研判。",
]

PROVIDER_LABELS = {
    "deepseek": "DeepSeek 开放平台",
    "openai_compatible": "OpenAI 兼容接口",
    "ollama": "Ollama 本地模型",
    "rule_engine": "规则引擎",
}

# 最近一次流式研判的回放缓存（进程内，最多保留 20 条记录）
LAST_STREAM: dict[str, dict] = {}


def remember_stream(record_id: str, payload: dict) -> None:
    LAST_STREAM.pop(record_id, None)
    LAST_STREAM[record_id] = payload
    while len(LAST_STREAM) > 20:
        LAST_STREAM.pop(next(iter(LAST_STREAM)))


def last_stream(record_id: str) -> dict:
    return LAST_STREAM.get(record_id, {})


# ------------------------------------------------------------------ 密钥与配置
def _api_key() -> tuple[str, str]:
    """返回 (密钥, 来源)。仅从环境变量或本地密钥文件读取，绝不落库、不回传前端。"""
    for env_name in ("PRACTICE_LLM_API_KEY", "DEEPSEEK_API_KEY", "OPENAI_API_KEY"):
        value = os.environ.get(env_name, "").strip()
        if value:
            return value, f"env:{env_name}"
    if LLM_KEY_FILE.exists():
        value = LLM_KEY_FILE.read_text(encoding="utf-8", errors="ignore").strip()
        if value:
            return value, f"file:{LLM_KEY_FILE}"
    return "", "none"


def _save_api_key(key: str) -> None:
    key = (key or "").strip()
    if not key:
        return
    LLM_KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
    LLM_KEY_FILE.write_text(key, encoding="utf-8")
    try:
        os.chmod(LLM_KEY_FILE, 0o600)
    except OSError:
        pass


def _clear_api_key() -> None:
    if LLM_KEY_FILE.exists():
        LLM_KEY_FILE.unlink()


def get_llm_config() -> dict:
    config = db.get_config("llm", {}) or {}
    merged = {**DEFAULT_LLM_CONFIG, **config}
    allowed = {"provider", "model", "base_url", "temperature", "max_tokens", "timeout_sec", "enabled"}
    return {k: v for k, v in merged.items() if k in allowed}


def save_llm_config(payload: dict) -> dict:
    payload = dict(payload or {})
    api_key = payload.pop("api_key", None)
    if api_key:
        _save_api_key(str(api_key))
    if payload.pop("clear_key", False):
        _clear_api_key()
    config = get_llm_config()
    for key in ("provider", "model", "base_url", "temperature", "max_tokens", "timeout_sec", "enabled"):
        if key in payload and payload[key] not in (None, ""):
            config[key] = payload[key]
    config["temperature"] = float(config.get("temperature", 0.2))
    config["max_tokens"] = int(config.get("max_tokens", 900))
    config["timeout_sec"] = int(config.get("timeout_sec", 40))
    config["enabled"] = bool(config.get("enabled", True))
    # 关键安全校验：服务地址白名单，防止 SSRF 与密钥外带（审查报告 S2）
    config["base_url"] = _normalize_base_url(config.get("provider", "deepseek"),
                                             config.get("base_url", ""))
    db.set_config("llm", config)
    return config


def _is_private_host(host: str) -> bool:
    """解析域名并判断是否指向本机/内网（防 SSRF）。解析失败时按不安全处理。"""
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        return True
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            continue
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
            return True
    return False


def _normalize_base_url(provider: str, base_url: str) -> str:
    """校验并归一化大模型服务地址。

    规则：
    - deepseek：只允许官方地址，忽略用户输入的其他值（密钥只发给官方域名）；
    - ollama：默认只允许公网地址，本地模型需显式 PRACTICE_LLM_ALLOW_PRIVATE=1；
    - openai_compatible：域名必须显式加入 PRACTICE_LLM_ALLOWED_HOSTS。
    """
    base = (base_url or "").strip().rstrip("/")
    if provider == "rule_engine":
        return ""
    if provider == "deepseek":
        if base and base != "https://api.deepseek.com":
            raise ValueError("DeepSeek 提供方只允许官方地址 https://api.deepseek.com")
        return "https://api.deepseek.com"
    if not base:
        raise ValueError("请填写服务地址")
    parsed = urllib.parse.urlsplit(base)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("服务地址必须是以 http(s):// 开头的合法 URL")
    host = parsed.hostname.lower()
    if host in {"localhost", "127.0.0.1", "::1", "0.0.0.0"}:
        if provider == "openai_compatible":
            raise ValueError("OpenAI 兼容接口不允许指向本机地址")
        if not LLM_ALLOW_PRIVATE:
            raise ValueError("默认禁止访问本机/内网模型服务；如确需本地 Ollama，"
                             "请设置 PRACTICE_LLM_ALLOW_PRIVATE=1")
        return base
    if _is_private_host(host) and not LLM_ALLOW_PRIVATE:
        raise ValueError("服务地址解析到内网地址，已拒绝（如确需请设置 PRACTICE_LLM_ALLOW_PRIVATE=1）")
    if provider == "openai_compatible" and host not in LLM_ALLOWED_HOSTS:
        raise ValueError(
            f"自定义端点 {host} 未授权：请将其加入环境变量 PRACTICE_LLM_ALLOWED_HOSTS 后再试")
    return base


def llm_status() -> dict:
    config = get_llm_config()
    key, source = _api_key()
    provider = config.get("provider", "deepseek")
    try:
        _normalize_base_url(provider, config.get("base_url", ""))
        base_ok, base_error = True, ""
    except ValueError as exc:
        base_ok, base_error = False, str(exc)
    active = bool(config.get("enabled") and key and provider != "rule_engine" and base_ok)
    if not base_ok:
        note = f"大模型服务地址被安全策略拒绝：{base_error}"
    elif active:
        note = ("大模型已接入：研判、追问与侦查指引由大模型生成，输出经服务端校验后落库；"
                "失败自动回退规则引擎。")
    else:
        note = "未检测到可用密钥或已关闭大模型：当前由可解释规则引擎生成，不影响演示链路。"
    return {
        "config": config,
        "key_configured": bool(key),
        "key_source": source,
        "base_url_safe": base_ok,
        "provider_label": PROVIDER_LABELS.get(provider, provider),
        "active_engine": "llm" if active else "rule_engine",
        "engine_label": (f"{PROVIDER_LABELS.get(provider, provider)} · {config.get('model')}"
                         if active else "规则引擎（离线可用）"),
        "note": note,
    }


# ------------------------------------------------------------------ 大模型调用
class LLMError(RuntimeError):
    pass


def _endpoint(config: dict) -> str:
    provider = config.get("provider", "deepseek")
    try:
        base = _normalize_base_url(provider, config.get("base_url", ""))
    except ValueError as exc:
        raise LLMError(str(exc)) from exc
    if provider == "deepseek":
        return base + "/chat/completions"
    if provider == "openai_compatible":
        if base.endswith("/v1"):
            return base + "/chat/completions"
        return base + "/v1/chat/completions"
    if provider == "ollama":
        return base + "/api/generate"
    raise LLMError(f"提供方 {provider} 不支持在线调用")


def _llm_chat(messages: list[dict], json_mode: bool = False) -> tuple[str, dict]:
    """调用大模型，返回 (文本, 元信息)。失败抛出 LLMError。"""
    config = get_llm_config()
    key, _ = _api_key()
    provider = config.get("provider", "deepseek")
    if provider == "rule_engine":
        raise LLMError("当前配置为规则引擎，未启用大模型")
    if not key and provider != "ollama":
        raise LLMError("未配置大模型密钥")
    timeout = float(config.get("timeout_sec", 40))
    model = config.get("model", "deepseek-chat")
    temperature = float(config.get("temperature", 0.2))
    max_tokens = int(config.get("max_tokens", 900))
    url = _endpoint(config)

    if provider == "ollama":
        prompt = "\n".join(f"{m.get('role')}: {m.get('content')}" for m in messages)
        body = {
            "model": model, "prompt": prompt, "stream": False,
            "format": "json" if json_mode else "",
            "options": {"temperature": temperature, "num_predict": max_tokens},
        }
        headers = {"Content-Type": "application/json"}
    else:
        body = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        if json_mode and provider in ("deepseek", "openai_compatible"):
            body["response_format"] = {"type": "json_object"}
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {key}"}

    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers=headers, method="POST")
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "ignore")[:300]
        raise LLMError(f"HTTP {exc.code}: {detail}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise LLMError(f"网络异常: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise LLMError("响应不是合法 JSON") from exc

    latency = round((time.perf_counter() - started) * 1000)
    if provider == "ollama":
        text = (payload.get("response") or "").strip()
        usage = {"total_tokens": payload.get("eval_count")}
    else:
        try:
            text = (payload["choices"][0]["message"]["content"] or "").strip()
        except (KeyError, IndexError) as exc:
            raise LLMError(f"响应结构异常: {str(payload)[:200]}") from exc
        usage = payload.get("usage") or {}
    if not text:
        raise LLMError("大模型返回空内容")
    return text, {
        "provider": provider,
        "model": model,
        "latency_ms": latency,
        "tokens": usage.get("total_tokens"),
        "prompt_tokens": usage.get("prompt_tokens"),
        "completion_tokens": usage.get("completion_tokens"),
        "endpoint": url,
    }


def _stream_chat(messages: list[dict], json_mode: bool = True):
    """流式调用大模型，逐块 yield {"text": ...} / {"usage": {...}}。

    DeepSeek 与 OpenAI 兼容接口走标准 SSE；Ollama 退化为一次性返回。
    """
    config = get_llm_config()
    key, _ = _api_key()
    provider = config.get("provider", "deepseek")
    if provider == "rule_engine":
        raise LLMError("当前配置为规则引擎，未启用大模型")
    if not key and provider != "ollama":
        raise LLMError("未配置大模型密钥")
    if provider == "ollama":
        text, meta = _llm_chat(messages, json_mode=json_mode)
        yield {"text": text}
        yield {"usage": {"total_tokens": meta.get("tokens")}}
        return

    timeout = float(config.get("timeout_sec", 40))
    body = {
        "model": config.get("model", "deepseek-chat"),
        "messages": messages,
        "temperature": float(config.get("temperature", 0.2)),
        "max_tokens": int(config.get("max_tokens", 900)),
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    headers = {"Content-Type": "application/json", "Accept": "text/event-stream",
               "Authorization": f"Bearer {key}"}
    request = urllib.request.Request(
        _endpoint(config),
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            for raw_line in resp:
                line = raw_line.decode("utf-8", "ignore").strip()
                if not line or line.startswith(":") or not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    break
                try:
                    data = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                if data.get("usage"):
                    yield {"usage": data["usage"]}
                for choice in data.get("choices") or []:
                    content = (choice.get("delta") or {}).get("content")
                    if content:
                        yield {"text": content}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "ignore")[:300]
        raise LLMError(f"HTTP {exc.code}: {detail}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise LLMError(f"网络异常: {exc}") from exc


class PartialThoughts:
    """从流式 JSON 增量中提取人类可读的「思维链」片段（容错解析）。"""

    PATTERNS = {
        "category": re.compile(r'"category"\s*:\s*"((?:[^"\\]|\\.)*)"'),
        "summary": re.compile(r'"summary"\s*:\s*"((?:[^"\\]|\\.)*)"'),
        "confidence": re.compile(r'"confidence"\s*:\s*(\d{1,3})'),
        "title": re.compile(r'"title"\s*:\s*"((?:[^"\\]|\\.)*)"'),
        "content": re.compile(r'"content"\s*:\s*"((?:[^"\\]|\\.)*)"'),
        "question": re.compile(r'"question"\s*:\s*"((?:[^"\\]|\\.)*)"'),
        "why": re.compile(r'"why"\s*:\s*"((?:[^"\\]|\\.)*)"'),
    }

    def __init__(self):
        self.buffer = ""
        self._tail = ""
        self.seen: set[tuple[str, str]] = set()

    def feed(self, chunk: str) -> list[dict]:
        # 只对「上一块尾部 + 本块」做匹配，避免每块重扫 4 万字符（审查报告 P8）
        scan = (self._tail + chunk)[-1200:]
        self._tail = scan[-600:]
        self.buffer += chunk
        if len(self.buffer) > 40000:
            self.buffer = self.buffer[-20000:]
        out = []
        for kind, pattern in self.PATTERNS.items():
            for match in pattern.finditer(scan):
                text = (match.group(1) if match.groups() else match.group(0))
                text = text.replace('\\"', '"').replace("\\n", " ").strip()
                if len(text) < 2:
                    continue
                token = (kind, text)
                if token in self.seen:
                    continue
                self.seen.add(token)
                out.append({"kind": kind, "text": text})
        return out


def deep_analysis_stream(record: dict, flow_state: dict, recommendations: list[dict],
                         progress: dict):
    """流式深度研判：依次 yield start / delta / thought / fallback / done 事件。"""
    config = get_llm_config()
    key, _ = _api_key()
    started = time.perf_counter()
    if not config.get("enabled") or not key or config.get("provider") == "rule_engine":
        analysis = _fallback_analysis(record, flow_state, recommendations,
                                      "未启用大模型或未配置密钥，使用规则引擎")
        yield {"type": "start", "engine": "rule_engine", "provider": "rule_engine",
               "model": "rule_engine"}
        yield {"type": "fallback", "reason": analysis["fallback_reason"]}
        yield {"type": "done", "analysis": analysis,
               "meta": {"engine": "rule_engine", "fallback": True,
                        "reason": analysis["fallback_reason"], "latency_ms": 0}}
        return

    prompt = _build_prompt(record, flow_state, recommendations, progress)
    yield {"type": "start", "engine": "llm", "provider": config.get("provider"),
           "model": config.get("model")}
    buffer: list[str] = []
    thought_log: list[dict] = []
    thoughts = PartialThoughts()
    usage: dict = {}
    try:
        for chunk in _stream_chat([
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ], json_mode=True):
            if "usage" in chunk:
                usage.update(chunk["usage"] or {})
                continue
            text = chunk.get("text") or ""
            if not text:
                continue
            buffer.append(text)
            yield {"type": "delta", "text": text}
            for thought in thoughts.feed(text):
                thought_log.append(thought)
                yield {"type": "thought", **thought}
        full = "".join(buffer)
        raw = _extract_json(full)
        analysis = _normalize_analysis(raw, flow_state)
        latency = round((time.perf_counter() - started) * 1000)
        meta = {
            "engine": "llm", "fallback": False, "reason": "",
            "provider": config.get("provider"), "model": config.get("model"),
            "latency_ms": latency, "prompt_chars": len(prompt),
            "tokens": usage.get("total_tokens"),
            "prompt_tokens": usage.get("prompt_tokens"),
            "completion_tokens": usage.get("completion_tokens"),
            "streamed": True,
            "rejected_followups": analysis.get("rejected_followups", 0),
        }
        analysis.pop("rejected_followups", None)
        remember_stream(record["id"], {
            "model": config.get("model"), "provider": config.get("provider"),
            "latency_ms": latency, "tokens": usage.get("total_tokens"),
            "thoughts": thought_log, "text": full[-2400:],
            "summary": analysis.get("summary", ""), "captured_at": db.now_iso(),
        })
        yield {"type": "done", "analysis": analysis, "meta": meta}
    except LLMError as exc:
        analysis = _fallback_analysis(record, flow_state, recommendations, str(exc)[:200])
        latency = round((time.perf_counter() - started) * 1000)
        remember_stream(record["id"], {
            "model": "rule_engine", "provider": "rule_engine",
            "latency_ms": latency, "tokens": 0,
            "thoughts": [{"kind": "summary", "text": analysis.get("summary", "")}],
            "text": "", "summary": analysis.get("summary", ""),
            "fallback": True, "reason": str(exc)[:200], "captured_at": db.now_iso(),
        })
        yield {"type": "fallback", "reason": str(exc)[:200]}
        yield {"type": "done", "analysis": analysis,
               "meta": {"engine": "rule_engine", "fallback": True, "reason": str(exc)[:200],
                        "latency_ms": latency, "streamed": True}}


def llm_self_test() -> dict:
    """连通性测试：真实发起一次极小请求。"""
    status = llm_status()
    if status["active_engine"] != "llm":
        return {"reachable": False, "sample": None, "latency_ms": None,
                "note": status["note"], "engine": "rule_engine"}
    try:
        text, meta = _llm_chat([
            {"role": "system", "content": "你是公安笔录辅助模型，回答极简。"},
            {"role": "user", "content": "只回复两个字：联通"},
        ])
        return {"reachable": True, "sample": text[:120], "latency_ms": meta["latency_ms"],
                "model": meta["model"], "provider": meta["provider"],
                "tokens": meta.get("tokens"),
                "note": f"调用成功：{PROVIDER_LABELS.get(meta['provider'], meta['provider'])} · {meta['model']}"}
    except LLMError as exc:
        return {"reachable": False, "sample": None, "latency_ms": None,
                "note": f"调用失败：{exc}", "engine": "rule_engine"}


# ------------------------------------------------------------------ 规则引擎（兜底）
def type_judgement(record: dict, recommendations: list[dict]) -> dict:
    category = record.get("case_category", "") or "其他"
    if not recommendations:
        return {
            "category": category, "confidence": 0, "basis": ["未匹配到模板，按报案登记案由归类"],
            "signals": [], "template_id": None, "engine": "rule",
        }
    top = recommendations[0]
    basis = [
        f"案情文本与「{top['name']}」模板特征信号词命中 {len(top['signal_hits'])} 个（饱和阈值 8 个）："
        + ("、".join(top["signal_hits"][:6]) if top["signal_hits"] else "无"),
        f"特征词语义相似度 {round(top['breakdown']['semantic'] * 100, 1)}%，手法关键词命中 "
        + ("、".join(top["keyword_hits"][:4]) if top["keyword_hits"] else "无"),
        f"模板推荐匹配度 {top['match_score']}%，排序第一",
    ]
    confidence = min(0.97, 0.45 + top["match_score"] / 100 * 0.5)
    return {
        "category": top["name"], "template_id": top["template_id"],
        "confidence": round(confidence * 100, 1), "basis": basis,
        "signals": top["signal_hits"],
        "alternatives": [{"name": r["name"], "score": r["match_score"]} for r in recommendations[1:3]],
        "engine": "rule",
    }


def investigation_guides(category: str) -> list[dict]:
    for key, guides in CATEGORY_GUIDES.items():
        if key and (key in category or category in key):
            return [{"title": f"侦查指引 {i + 1}", "content": text} for i, text in enumerate(guides)]
    return [{"title": f"侦查指引 {i + 1}", "content": text} for i, text in enumerate(DEFAULT_GUIDES)]


def followup_question(flow_key: str, field_key: str, label: str, hint: str) -> str:
    text = ADVICE_QUESTIONS.get((flow_key, field_key))
    if text:
        text = text.rstrip()
        if not text.endswith(("？", "?")):
            text = re.sub(r"[。.；;]$", "", text) + "？"
        return text
    return f"请补充说明{label}的具体情况（{hint}）？" if hint else f"请补充说明{label}的具体情况？"


# ------------------------------------------------------------------ 大模型深度研判
SYSTEM_PROMPT = (
    "你是公安机关反诈中心的资深研判民警，熟悉电信网络诈骗案件办理与询问笔录规范。"
    "你的任务是基于民警提供的案情与已采集要素，输出结构化研判结果。"
    "必须严格输出 JSON，不得输出 Markdown 代码块或任何解释性文字；不得编造未提供的号码、账号、金额、网址等具体信息。"
)

PROMPT_TEMPLATE = """【案件信息】
案件类别：{category}
简要案情：{brief}
已选问询模板：{templates}
问询进度：已作答 {answered} 题 / 共 {total} 题

【已采集要素】
{collected}

【当前五流缺口（按严重度排序，最多 8 项）】
{gaps}

请输出一个 JSON 对象，结构如下：
{{
  "summary": "一句话案情研判（不超过 60 字）",
  "type_judgement": {{
    "category": "涉诈类型名称",
    "confidence": 0 到 100 的整数,
    "basis": ["判定依据1", "判定依据2", "判定依据3"]
  }},
  "investigation_guides": [
    {{"title": "侦查指引标题（不超过 12 字）", "content": "可执行的侦查动作（不超过 60 字）"}}
  ],
  "followups": [
    {{
      "flow": "缺口所属流，只能取 fund/communication/network/personnel/delivery",
      "field": "缺口字段 key，必须与上方缺口列表中的字段完全一致",
      "question": "向被询问人发问的问句（以问号结尾，20~80 字，符合民警询问口吻）",
      "why": "为什么必须问这个要素（不超过 30 字）"
    }}
  ]
}}

硬性要求：
1. investigation_guides 恰好 3 条，动作要具体可执行（止付、调证、勘验、落地、串并等）；
2. followups 最多 4 条，只能使用【当前五流缺口】中列出的 flow 与 field，优先资金流；
3. question 不得包含任何具体号码、卡号、金额、网址，只描述需要补充的证据要素；
4. 若无缺口，followups 返回空数组；confidence 必须与依据强度匹配，不确定时取低值。"""


def _build_prompt(record: dict, flow_state: dict, recommendations: list[dict], progress: dict) -> str:
    collected_lines = []
    for flow in flow_state.get("flows", []):
        for field in flow.get("fields", []):
            if field.get("status") == "collected" and field.get("value"):
                collected_lines.append(f"- {flow['name']}·{field['label']}：{field['value']}")
    gap_lines = []
    for gap in flow_state.get("gaps", [])[:8]:
        flag = "关键要素" if gap.get("key_item") else "一般要素"
        gap_lines.append(f"- {gap['flow_name']}·{gap['label']}（{gap['flow']}={gap['field']}，{flag}）：{gap.get('hint', '')}")
    templates = "、".join(t["name"] for t in (recommendations[:1] or [])) or "未选定"
    return PROMPT_TEMPLATE.format(
        category=record.get("case_category", "") or "其他",
        brief=(record.get("brief") or "")[:600],
        templates=templates,
        answered=progress.get("answered", 0),
        total=progress.get("total", 0),
        collected="\n".join(collected_lines[:12]) or "（暂无）",
        gaps="\n".join(gap_lines) or "（无缺口）",
    )


def _extract_json(text: str) -> dict:
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise LLMError("大模型未返回 JSON 对象")
    try:
        data = json.loads(text[start:end + 1])
    except json.JSONDecodeError as exc:
        raise LLMError(f"JSON 解析失败: {exc}") from exc
    if not isinstance(data, dict):
        raise LLMError("大模型返回的 JSON 不是对象")
    return data


def _normalize_analysis(raw: dict, flow_state: dict) -> dict:
    """校验大模型输出：字段白名单、问句格式、上限与兜底。"""
    gaps = {(g["flow"], g["field"]): g for g in flow_state.get("gaps", [])}
    judgement = raw.get("type_judgement") or {}
    category = str(judgement.get("category") or "").strip()
    if not category:
        raise LLMError("缺少 type_judgement.category")
    try:
        confidence = float(judgement.get("confidence", 0))
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(100.0, confidence))
    basis = [str(item).strip() for item in (judgement.get("basis") or []) if str(item).strip()][:5]
    if not basis:
        basis = ["大模型综合案情与已采集要素给出判断"]

    guides = []
    for item in (raw.get("investigation_guides") or [])[:4]:
        title = str(item.get("title") or "").strip()[:20]
        content = str(item.get("content") or "").strip()[:120]
        if title and content:
            guides.append({"title": title, "content": content})
    if not guides:
        raise LLMError("缺少 investigation_guides")

    followups = []
    rejected = 0
    seen_fields = set()
    for item in (raw.get("followups") or []):
        flow = str(item.get("flow") or "").strip()
        field = str(item.get("field") or "").strip()
        if (flow, field) not in gaps or (flow, field) in seen_fields:
            rejected += 1
            continue
        question = str(item.get("question") or "").strip()
        if len(question) < 8:
            rejected += 1
            continue
        question = question.rstrip("。.；;，, ")
        if not question.endswith(("？", "?")):
            question += "？"
        gap = gaps[(flow, field)]
        seen_fields.add((flow, field))
        followups.append({
            "flow": flow,
            "field": field,
            "flow_name": gap["flow_name"],
            "label": gap["label"],
            "key_item": bool(gap.get("key_item")),
            "question": question[:160],
            "why": str(item.get("why") or "").strip()[:60],
            "section": FLOW_SECTION.get(flow, "process"),
        })
        if len(followups) >= 4:
            break

    summary = str(raw.get("summary") or "").strip()[:120]
    return {
        "summary": summary or f"研判为{category}，当前仍有 {len(gaps)} 项要素缺口。",
        "judgement": {"category": category, "confidence": round(confidence, 1), "basis": basis},
        "guides": guides,
        "followups": followups,
        "rejected_followups": rejected,
        "engine": "llm",
    }


def _fallback_analysis(record: dict, flow_state: dict, recommendations: list[dict], reason: str) -> dict:
    judgement = type_judgement(record, recommendations)
    guides = [{"title": g["title"], "content": g["content"]}
              for g in investigation_guides(judgement["category"])]
    followups = []
    for gap in flow_state.get("gaps", [])[:4]:
        followups.append({
            "flow": gap["flow"], "field": gap["field"], "flow_name": gap["flow_name"],
            "label": gap["label"], "key_item": bool(gap.get("key_item")),
            "question": followup_question(gap["flow"], gap["field"], gap["label"], gap.get("hint", "")),
            "why": gap.get("hint", ""),
            "section": FLOW_SECTION.get(gap["flow"], "process"),
        })
    return {
        "summary": f"研判为{judgement['category']}（置信度 {judgement['confidence']}%），当前有 "
                   f"{len(flow_state.get('key_gaps', []))} 项重点缺口。",
        "judgement": {"category": judgement["category"], "confidence": judgement["confidence"],
                      "basis": judgement["basis"]},
        "guides": guides,
        "followups": followups,
        "rejected_followups": 0,
        "engine": "rule_engine",
        "fallback_reason": reason,
    }


def deep_analysis(record: dict, flow_state: dict, recommendations: list[dict],
                  progress: dict) -> dict:
    """执行一次深度研判：优先大模型，失败回退规则引擎。"""
    config = get_llm_config()
    key, _ = _api_key()
    if not config.get("enabled") or not key or config.get("provider") == "rule_engine":
        analysis = _fallback_analysis(record, flow_state, recommendations,
                                      "未启用大模型或未配置密钥，使用规则引擎")
        return {"analysis": analysis,
                "meta": {"engine": "rule_engine", "fallback": True,
                         "reason": analysis["fallback_reason"], "latency_ms": 0}}

    prompt = _build_prompt(record, flow_state, recommendations, progress)
    started = time.perf_counter()
    try:
        text, meta = _llm_chat([
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ], json_mode=True)
        raw = _extract_json(text)
        analysis = _normalize_analysis(raw, flow_state)
        meta.update({
            "engine": "llm", "fallback": False, "reason": "",
            "latency_ms": meta.get("latency_ms", round((time.perf_counter() - started) * 1000)),
            "prompt_chars": len(prompt), "rejected_followups": analysis["rejected_followups"],
        })
        analysis.pop("rejected_followups", None)
        return {"analysis": analysis, "meta": meta, "raw_text": text}
    except LLMError as exc:
        analysis = _fallback_analysis(record, flow_state, recommendations, str(exc)[:200])
        return {"analysis": analysis,
                "meta": {"engine": "rule_engine", "fallback": True, "reason": str(exc)[:200],
                         "latency_ms": round((time.perf_counter() - started) * 1000)}}


# ------------------------------------------------------------------ 建议落库
def persist_analysis_advice(record: dict, analysis: dict, engine: str) -> list[dict]:
    """把深度研判结果写为建议卡片（可采纳/仅展示），并去重同一缺口。"""
    record_id = record["id"]
    llm_keys = {(f["flow"], f["field"]) for f in analysis.get("followups", [])}
    with db.write() as conn:
        conn.execute("DELETE FROM advice WHERE record_id = ? AND engine = ? AND status = 'pending'",
                     (record_id, engine))
        if engine == "llm":
            # 大模型已生成完整研判，规则的待处理卡片（类型判定/指引/追问）让位，避免重复展示
            conn.execute("DELETE FROM advice WHERE record_id = ? AND engine = 'rule' AND status = 'pending'",
                         (record_id,))
        conn.execute(
            "INSERT INTO advice (id, record_id, kind, title, content, priority, status, engine, evidence, "
            "payload, created_at) VALUES (?, ?, 'llm_judgement', ?, ?, 0, 'pending', ?, ?, '{}', ?)",
            (db.new_id("AD", 10), record_id,
             f"大模型研判：{analysis['judgement']['category']}",
             analysis.get("summary", ""), engine,
             db.json_dump(analysis["judgement"].get("basis", [])), db.now_iso()),
        )
        for guide in analysis.get("guides", []):
            conn.execute(
                "INSERT INTO advice (id, record_id, kind, title, content, priority, status, engine, evidence, "
                "payload, created_at) VALUES (?, ?, 'investigation', ?, ?, 7, 'pending', ?, ?, '{}', ?)",
                (db.new_id("AD", 10), record_id, guide["title"], guide["content"], engine,
                 db.json_dump([analysis["summary"]]), db.now_iso()),
            )
        for follow in analysis.get("followups", []):
            priority = {"fund": 1, "communication": 2, "network": 3, "personnel": 4, "delivery": 5}.get(
                follow["flow"], 6)
            conn.execute(
                "INSERT INTO advice (id, record_id, kind, title, content, priority, status, engine, evidence, "
                "payload, created_at) VALUES (?, ?, 'followup', ?, ?, ?, 'pending', ?, ?, ?, ?)",
                (db.new_id("AD", 10), record_id,
                 f"{follow['flow_name']}关键要素缺失：{follow['label']}",
                 follow["question"], priority, engine,
                 db.json_dump([f"缺口要素：{follow['label']}", follow.get("why") or follow.get("label", "")]),
                 db.json_dump({
                     "question": follow["question"], "section": follow["section"],
                     "flow": follow["flow"], "field": follow["field"],
                     "key_item": follow["key_item"], "engine": engine,
                 }),
                 db.now_iso()),
            )
    return list_advice(record_id)


# ------------------------------------------------------------------ 规则建议（自动刷新）
def refresh_advice(record: dict, flow_state: dict, recommendations: list[dict]) -> list[dict]:
    """重新生成规则建议：保留已采纳与大模型建议，只刷新 rule 引擎的待处理项。"""
    record_id = record["id"]
    judgement = type_judgement(record, recommendations)
    guides = investigation_guides(judgement["category"])
    gaps = flow_state.get("gaps", [])
    # 已被大模型追问覆盖的缺口不再生成规则追问，避免同一要素出现两张卡片
    covered = set()
    for row in db.rows(
        "SELECT payload FROM advice WHERE record_id = ? AND engine = 'llm' "
        "AND status = 'pending' AND kind = 'followup'", (record_id,)
    ):
        payload = db.json_load(row["payload"], {})
        covered.add((payload.get("flow"), payload.get("field")))
    key_gaps = [g for g in gaps if g["key_item"] and (g["flow"], g["field"]) not in covered]
    other_gaps = [g for g in gaps if not g["key_item"] and (g["flow"], g["field"]) not in covered]

    items: list[dict] = []
    has_llm_judgement = bool(db.one(
        "SELECT id FROM advice WHERE record_id = ? AND engine = 'llm' AND status = 'pending' "
        "AND kind = 'llm_judgement' LIMIT 1", (record_id,)))
    if not has_llm_judgement:
        items.append({
            "kind": "type",
            "title": f"涉诈类型判定：{judgement['category']}",
            "content": "；".join(judgement["basis"]),
            "priority": 0,
            "evidence": judgement["basis"],
            "payload": {"judgement": judgement},
        })
    for idx, gap in enumerate(key_gaps[:4]):
        question = followup_question(gap["flow"], gap["field"], gap["label"], gap["hint"])
        items.append({
            "kind": "followup",
            "title": f"{gap['flow_name']}关键要素缺失：{gap['label']}",
            "content": question,
            "priority": gap["priority"],
            "evidence": [f"当前{gap['flow_name']}已收集要素中缺少「{gap['label']}」", gap["hint"]],
            "payload": {"question": question, "section": FLOW_SECTION.get(gap["flow"], "process"),
                        "flow": gap["flow"], "field": gap["field"], "key_item": True, "order": idx},
        })
    for gap in other_gaps[:2]:
        question = followup_question(gap["flow"], gap["field"], gap["label"], gap["hint"])
        items.append({
            "kind": "followup",
            "title": f"{gap['flow_name']}要素补充：{gap['label']}",
            "content": question,
            "priority": gap["priority"] + 1,
            "evidence": [f"「{gap['label']}」尚未采集", gap["hint"]],
            "payload": {"question": question, "section": FLOW_SECTION.get(gap["flow"], "process"),
                        "flow": gap["flow"], "field": gap["field"], "key_item": False},
        })
    for guide in guides:
        if has_llm_judgement:
            break
        items.append({
            "kind": "investigation", "title": guide["title"], "content": guide["content"],
            "priority": 6, "evidence": [judgement["category"]], "payload": {},
        })

    # 内容未变化时不写库：答复 800ms 防抖保存会高频触发刷新（审查报告 P7）
    existing = db.rows(
        "SELECT kind, title, content, priority, evidence, payload FROM advice "
        "WHERE record_id = ? AND engine = 'rule' AND status = 'pending'", (record_id,))

    def _sig(kind, title, content, priority, evidence, payload):
        return (kind, title, content, priority, db.json_dump(evidence), db.json_dump(payload))

    old_sigs = sorted(_sig(r["kind"], r["title"], r["content"], r["priority"],
                           db.json_load(r["evidence"], []), db.json_load(r["payload"], {}))
                      for r in existing)
    new_sigs = sorted(_sig(i["kind"], i["title"], i["content"], i["priority"],
                           i["evidence"], i["payload"]) for i in items)
    if old_sigs == new_sigs:
        return list_advice(record_id)

    with db.write() as conn:
        conn.execute("DELETE FROM advice WHERE record_id = ? AND engine = 'rule' AND status = 'pending'",
                     (record_id,))
        for item in items:
            conn.execute(
                "INSERT INTO advice (id, record_id, kind, title, content, priority, status, engine, evidence, "
                "payload, created_at) VALUES (?, ?, ?, ?, ?, ?, 'pending', 'rule', ?, ?, ?)",
                (db.new_id("AD", 10), record_id, item["kind"], item["title"], item["content"],
                 item["priority"], db.json_dump(item["evidence"]), db.json_dump(item["payload"]), db.now_iso()),
            )
    return list_advice(record_id)


def list_advice(record_id: str) -> list[dict]:
    rows = db.rows(
        "SELECT * FROM advice WHERE record_id = ? AND status != 'dismissed' "
        "ORDER BY priority ASC, created_at ASC",
        (record_id,),
    )
    result = []
    for row in rows:
        result.append({
            "id": row["id"],
            "kind": row["kind"],
            "title": row["title"],
            "content": row["content"],
            "priority": row["priority"],
            "status": row["status"],
            "engine": row.get("engine", "rule"),
            "evidence": db.json_load(row["evidence"], []),
            "payload": db.json_load(row["payload"], {}),
            "created_at": row["created_at"],
            "adopted_at": row["adopted_at"],
        })
    return result


def advice_stats(record_id: str) -> dict:
    rows = db.rows(
        "SELECT kind, engine, status, COUNT(*) AS n FROM advice WHERE record_id = ? GROUP BY kind, engine, status",
        (record_id,),
    )
    total = sum(r["n"] for r in rows)
    adopted = sum(r["n"] for r in rows if r["status"] == "adopted")
    llm = sum(r["n"] for r in rows if r["engine"] == "llm")
    return {
        "total": total,
        "adopted": adopted,
        "llm": llm,
        "adoption_rate": round(adopted / total * 100, 1) if total else 0.0,
        "by_kind": {r["kind"]: r["n"] for r in rows},
    }
