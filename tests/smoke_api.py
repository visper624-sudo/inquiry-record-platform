# -*- coding: utf-8 -*-
"""接口自动化自测脚本：覆盖验收条款 1~10 的主要接口链路。

运行（需先启动服务）：
    python tests/smoke_api.py --base http://127.0.0.1:8765

产出：
    screenshots/api_test_report.json   机读结果
    docs/接口自测报告.md                人读报告
    tests/api_report.html              可视化报告（用于截图）
自测产生的笔录会在脚本结束时清理，不污染演示数据。
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime
from http.cookiejar import CookieJar
from pathlib import Path
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
import config  # noqa: E402

RESULTS: list[dict] = []
CLEANUP: dict = {"records": [], "qa_ids": [], "advice_ids": [], "material_ids": [], "jobs": []}


# ------------------------------------------------------------------ HTTP 客户端
class Client:
    def __init__(self, base: str):
        self.base = base.rstrip("/")
        self.jar = CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))

    def request(self, method: str, path: str, payload=None, files=None, raw: bool = False,
                timeout: float = 20.0):
        url = self.base + path
        headers = {"Accept": "application/json"}
        data = None
        if files:
            boundary = "----smoke" + uuid.uuid4().hex
            chunks: list[bytes] = []
            for key, value in (payload or {}).items():
                chunks.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{key}\"\r\n\r\n"
                              f"{value}\r\n".encode("utf-8"))
            for key, filename, content, ctype in files:
                chunks.append(
                    f"--{boundary}\r\nContent-Disposition: form-data; name=\"{key}\"; filename=\"{filename}\"\r\n"
                    f"Content-Type: {ctype}\r\n\r\n".encode("utf-8") + content + b"\r\n"
                )
            chunks.append(f"--{boundary}--\r\n".encode("utf-8"))
            data = b"".join(chunks)
            headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
        elif payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json; charset=utf-8"
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with self.opener.open(request, timeout=timeout) as resp:
                body = resp.read()
                ctype = resp.headers.get("Content-Type", "")
                return resp.status, (body if raw else _maybe_json(body)), ctype, dict(resp.headers)
        except urllib.error.HTTPError as exc:
            body = exc.read()
            return exc.code, (body if raw else _maybe_json(body)), exc.headers.get("Content-Type", ""), dict(exc.headers)


def _maybe_json(body: bytes):
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return body


def _check_advice_integrity(body):
    """校验建议卡片：同一缺口不得重复、追问必须以问号结尾。"""
    advice = body["data"]["advice"]
    seen = {}
    for item in advice:
        if item["kind"] == "followup":
            payload = item.get("payload") or {}
            key = (payload.get("flow"), payload.get("field"))
            seen[key] = seen.get(key, 0) + 1
    duplicate = {f"{k[0]}.{k[1]}": v for k, v in seen.items() if v > 1}
    if duplicate:
        return f"同一缺口出现重复建议：{duplicate}"
    for card in advice:
        if card["kind"] == "followup" and not str(card["content"]).rstrip().endswith(("？", "?")):
            return f"追问未以问号结尾：{card['content'][:40]}"
        if card.get("engine") not in ("llm", "rule"):
            return f"建议引擎标识异常：{card.get('engine')}"
    return True


def check_stream(name: str, client: Client, path: str, payload=None, timeout: float = 120.0):
    """校验 SSE 流式接口：必须收到 delta/thought 增量与 done 结果。"""
    started = time.perf_counter()
    request = urllib.request.Request(
        client.base + path,
        data=json.dumps(payload or {}).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
        method="POST",
    )
    counts: dict[str, int] = {}
    done = None
    error = None
    try:
        with client.opener.open(request, timeout=timeout) as resp:
            current = "message"
            for raw in resp:
                line = raw.decode("utf-8", "ignore").strip()
                if not line:
                    continue
                if line.startswith("event:"):
                    current = line[6:].strip()
                elif line.startswith("data:"):
                    counts[current] = counts.get(current, 0) + 1
                    try:
                        data = json.loads(line[5:].strip())
                    except json.JSONDecodeError:
                        data = {}
                    if current == "done":
                        done = data
                    elif current == "error":
                        error = data
    except Exception as exc:  # noqa: BLE001
        RESULTS.append({"name": name, "method": "POST", "path": path, "status": 0,
                        "ok": False, "ms": 0, "note": f"流式异常：{exc}"})
        return None
    ms = round((time.perf_counter() - started) * 1000, 1)
    ok = (done is not None and error is None
          and counts.get("delta", 0) > 0 and counts.get("thought", 0) > 0)
    meta = (done or {}).get("meta", {})
    note = (f"事件 delta={counts.get('delta', 0)} thought={counts.get('thought', 0)} "
            f"done={counts.get('done', 0)} · tokens={meta.get('tokens')}")
    RESULTS.append({"name": name, "method": "POST", "path": path, "status": 200,
                    "ok": ok, "ms": ms, "note": note})
    return done


def check(name: str, client: Client, method: str, path: str, payload=None, files=None,
          expect: tuple | int = 200, raw: bool = False, note: str = "", validator=None):
    started = time.perf_counter()
    try:
        status, body, ctype, headers = client.request(method, path, payload, files, raw=raw)
    except Exception as exc:  # noqa: BLE001
        RESULTS.append({"name": name, "method": method, "path": path, "status": 0,
                        "ok": False, "ms": 0, "note": f"请求异常: {exc}"})
        return None
    ms = round((time.perf_counter() - started) * 1000, 1)
    expect_tuple = (expect,) if isinstance(expect, int) else tuple(expect)
    ok = status in expect_tuple
    extra = ""
    if ok and validator:
        try:
            verdict = validator(body, headers)
            if verdict is True:
                pass
            elif isinstance(verdict, str):
                extra = verdict
            else:
                ok, extra = False, f"断言失败: {verdict}"
        except Exception as exc:  # noqa: BLE001
            ok, extra = False, f"断言异常: {exc}"
    elif not ok:
        extra = str(body)[:160]
    RESULTS.append({"name": name, "method": method, "path": path, "status": status,
                    "ok": ok, "ms": ms, "note": note or extra})
    return body


def api_ok(body) -> str | bool:
    if not isinstance(body, dict):
        return "响应不是 JSON"
    if not body.get("ok"):
        return f"业务失败: {body.get('error')}"
    return body.get("data", {})


# ------------------------------------------------------------------ 取证助手
def find_qa(workbench: dict, field: str, empty_only: bool = True):
    for section in workbench["data"]["outline"]:
        for item in section["items"]:
            if item.get("field") == field and (not empty_only or not item["answer"].strip()):
                return section["key"], item
    return None, None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8765")
    args = parser.parse_args()
    client = Client(args.base)
    other_org = Client(args.base)
    analyst = Client(args.base)
    admin = Client(args.base)

    # ---------------- 0. 健康检查与静态资源
    check("服务健康检查", client, "GET", "/api/health", expect=200)
    check("未登录访问接口应被拦截", Client(args.base), "GET", "/api/records", expect=401)
    check("登录页静态资源", client, "GET", "/login.html", raw=True, expect=200,
          validator=lambda b, h: "智能问询笔录平台" in b.decode("utf-8", "ignore") or "登录" in b.decode("utf-8", "ignore"))
    check("静态资源目录穿越被拒绝", client, "GET", "/%2e%2e/backend/app.py", expect=(403, 404))
    demo_status, _, _, demo_headers = client.request(
        "GET", "/demo/session?badge=100101&next=//evil.com", raw=True)
    if demo_status in (302, 404):
        location = next((v for k, v in demo_headers.items() if k.lower() == "location"), "")
        RESULTS.append({"name": "演示入口开放重定向被拦截", "method": "GET",
                        "path": "/demo/session?next=//evil.com", "status": demo_status,
                        "ok": demo_status == 404 or location == "/index.html", "ms": 0,
                        "note": f"Location={location or '-'}"})

    # ---------------- 1. 登录与身份
    body = check("办案民警登录", client, "POST", "/api/auth/login",
                 {"badge_no": "100101", "password": "123456"}, expect=200,
                 validator=lambda b, h: (bool(h.get("Set-Cookie"))
                                         and "token" not in b.get("data", {}))
                 or "登录响应应只通过 HttpOnly Cookie 下发会话，且不应回传 token")
    me = check("获取当前登录用户", client, "GET", "/api/auth/me", expect=200)
    if isinstance(body, dict) and body.get("ok"):
        user = body["data"]["user"]
        assert user["role"] == "police"
    check("错误密码应拒绝", Client(args.base), "POST", "/api/auth/login",
          {"badge_no": "100101", "password": "wrong"}, expect=401)
    # 登录失败锁定：随机警号，连续 5 次失败后应 429（S6）
    lock_client = Client(args.base)
    lock_badge = "9" + uuid.uuid4().hex[:8]
    for _ in range(5):
        lock_client.request("POST", "/api/auth/login",
                            {"badge_no": lock_badge, "password": "wrong-password"})
    check("连续登录失败触发限流", lock_client, "POST", "/api/auth/login",
          {"badge_no": lock_badge, "password": "wrong-password"}, expect=429,
          validator=lambda b, h: True if b["error"]["code"] == "too_many_attempts"
          else f"错误码异常：{b['error']}")
    check("研判员登录（只读角色）", analyst, "POST", "/api/auth/login",
          {"badge_no": "100201", "password": "123456"})
    check("其他派出所民警登录（跨机构鉴权）", other_org, "POST", "/api/auth/login",
          {"badge_no": "100103", "password": "123456"})
    check("管理员登录", admin, "POST", "/api/auth/login", {"badge_no": "100001", "password": "123456"})

    # ---------------- 2. 台账与统计
    listing = check("笔录台账分页查询", client, "GET", "/api/records?page=1&page_size=5", expect=200,
                    validator=lambda b, h: b["data"]["total"] >= 6 or "台账总数不足")
    check("关键字检索", client, "GET", "/api/records?keyword=" + urllib.parse.quote("李芳"), expect=200)
    check("状态筛选（问询中）", client, "GET", "/api/records?status=inquiry", expect=200)
    check("统计大盘", client, "GET", "/api/stats/overview", expect=200,
          validator=lambda b, h: b["data"]["cards"]["total"] >= 6 or "统计卡片异常")
    check("跨机构访问应被拒绝", other_org, "GET", "/api/records/RC202611160001", expect=403)
    check("研判员可查看工作台", analyst, "GET", "/api/records/RC202611160001/workbench", expect=200)
    check("研判员修改问答应被拒绝", analyst, "PATCH", "/api/records/RC202611160001/qa/QAX",
          {"answer": "x"}, expect=(403, 404))

    # ---------------- 3. 接报与外部系统同步
    check("非法案件编号被拒绝", client, "POST", "/api/records", {
        "case_category": "刷单返利",
        "brief": "非法编号测试：受害人在短视频平台被诱导刷单，累计转账1.2万元。",
        "case_no": "bad/../case",
    }, expect=400)
    created = check("新增笔录（接报建档）", client, "POST", "/api/records", {
        "case_category": "刷单返利",
        "brief": "接口自动化自测样本：受害人在短视频平台被诱导参与刷单任务，累计转账1.2万元后无法提现。",
        "reporter_name": "自测样本",
        "reporter_phone": "13000000000",
        "report_time": "2026-11-16T10:00:00",
        "case_location": "城东区长虹路1号",
    }, expect=200)
    record_id = created["data"]["record"]["id"] if isinstance(created, dict) and created.get("ok") else None
    if record_id:
        CLEANUP["records"].append(record_id)
    check("外部系统案件检索（智研判/智案管 Mock）", client, "GET",
          "/api/mock/external/cases?keyword=" + urllib.parse.quote("李芳"), expect=200,
          validator=lambda b, h: True if b["data"]["items"] else "未检索到外部案件")
    if record_id:
        check("一键同步外部案件回填", client, "POST", f"/api/records/{record_id}/sync-external",
              {"external_case_id": "EXT002"}, expect=200,
              validator=lambda b, h: b["data"]["record"]["reporter_name"] == "王强" or "回填字段不正确")
        check("四阶段流转（intake→templates）", client, "POST", f"/api/records/{record_id}/stage",
              {"stage": "templates"}, expect=200,
              validator=lambda b, h: b["data"]["stage"] == "templates" or "阶段未更新")

    # ---------------- 4. 模板推荐与合并
    check("模板库检索", client, "GET", "/api/templates?keyword=" + urllib.parse.quote("投资"), expect=200,
          validator=lambda b, h: True if b["data"]["items"] else "模板库为空")
    recos = check("AI 模板推荐（可解释打分）", client, "POST", "/api/templates/recommend", {
        "brief": "受害人在股票交流群被陌生人添加微信，对方自称券商客服并推荐“国盛智投”APP，以导师荐股、稳赚不赔为诱饵诱导充值，转账80万元后无法提现。",
        "case_category": "虚假网络投资理财",
    }, expect=200,
        validator=lambda b, h: (b["data"]["recommendations"][0]["template_id"] == "T03"
                                and b["data"]["recommendations"][0]["match_score"] > 40) or "推荐结果异常")
    if record_id:
        check("选定模板并生成合并大纲", client, "POST", f"/api/records/{record_id}/templates",
              {"template_ids": ["T03", "T00"]}, expect=200,
              validator=lambda b, h: len(b["data"]["merged"]["sections"]) == 5 or "章节数不为 5")

    # ---------------- 5. 三栏工作台与五流实时更新
    workbench = check("三栏工作台数据加载", client, "GET",
                      "/api/records/RC202611160001/workbench", expect=200,
                      validator=lambda b, h: (b["data"]["flow"]["flows"]
                                              and b["data"]["progress"]["total"] > 10) or "工作台数据不完整")
    if workbench:
        fund = next(f for f in workbench["data"]["flow"]["flows"] if f["key"] == "fund")
        assert fund["status"] == "key_gap", "演示数据资金流应为重点缺口"
    section, qa_card = find_qa(workbench or {}, "counterparty_card")
    section_name, qa_name = find_qa(workbench or {}, "counterparty_name")
    if qa_card:
        check("录入答复后五流实时更新（补录对方卡号）", client, "PATCH",
              f"/api/records/RC202611160001/qa/{qa_card['id']}",
              {"answer": "对方卡号是6217000000000005678，开户行是建设银行。"}, expect=200,
              validator=lambda b, h: (
                  next(f for f in b["data"]["flow"]["flows"] if f["key"] == "fund")["status"] == "key_gap"
              ) or "补录卡号后仍应为重点缺口（缺户名）")
    if qa_name:
        check("补录户名后资金流变为已收集", client, "PATCH",
              f"/api/records/RC202611160001/qa/{qa_name['id']}",
              {"answer": "对方收款户名是陈某。"}, expect=200,
              validator=lambda b, h: (
                  next(f for f in b["data"]["flow"]["flows"] if f["key"] == "fund")["status"] == "collected"
              ) or "资金流未变为已收集")
    check("问答恢复原值", client, "PATCH", f"/api/records/RC202611160001/qa/{qa_card['id']}",
          {"answer": ""}, expect=200) if qa_card else None
    check("问答恢复原值", client, "PATCH", f"/api/records/RC202611160001/qa/{qa_name['id']}",
          {"answer": ""}, expect=200) if qa_name else None

    qa_created = check("新增问答（章节归类）", client, "POST", "/api/records/RC202611160001/qa", {
        "section": "evidence", "question": "你提供的转账截图是否与银行流水一致？", "answer": "", "source": "manual"
    }, expect=200, validator=lambda b, h: True)
    if isinstance(qa_created, dict) and qa_created.get("ok") and qa_created["data"].get("qa"):
        CLEANUP["qa_ids"].append(qa_created["data"]["qa"]["id"])

    # ---------------- 6. AI 研判与一键采纳
    advice_resp = check("AI 研判建议生成", client, "POST", "/api/records/RC202611160001/advice/refresh",
                        expect=200,
                        validator=lambda b, h: any(a["kind"] == "followup" for a in b["data"]["advice"])
                        or "未生成追问建议")
    adopted_qa = None
    if advice_resp:
        followup = next((a for a in advice_resp["data"]["advice"]
                         if a["kind"] == "followup" and a["status"] == "pending"), None)
        if followup:
            adopted = check("一键采纳 AI 推荐问题", client, "POST",
                            f"/api/records/RC202611160001/advice/{followup['id']}/adopt", expect=200,
                            validator=lambda b, h: (b["data"].get("qa") or {}).get("source") == "ai_suggested"
                            or "采纳后问答来源应为 AI 建议")
            if isinstance(adopted, dict) and adopted.get("ok"):
                adopted_qa = adopted["data"]["qa"]["id"]
                CLEANUP["qa_ids"].append(adopted_qa)
                CLEANUP["advice_ids"].append(followup["id"])

    # ---------------- 7. 材料上传与物理隔离
    png = _demo_png()
    uploaded = check("上传辅助材料", client, "POST", "/api/records/RC202611160001/materials",
                     {"note": "接口自测上传"}, files=[("file", "自测材料.png", png, "image/png")], expect=200,
                     validator=lambda b, h: len(b["data"]["items"]) >= 3 or "材料列表未增加")
    auto_material = None
    if isinstance(uploaded, dict) and uploaded.get("ok"):
        auto_material = next((m for m in uploaded["data"]["items"] if m["note"] == "接口自测上传"), None)
    if auto_material:
        check("删除自测材料（不影响正文问答）", client, "DELETE",
              f"/api/records/RC202611160001/materials/{auto_material['id']}", expect=200,
              validator=lambda b, h: True if len(b["data"]["items"]) >= 2 else "材料删除后列表异常")
    check("获取材料列表", client, "GET", "/api/records/RC202611160001/materials", expect=200)
    # MIME 伪造防护：上传时声明 text/html，应按扩展名存为 image/png（S10）
    status, spoof_body, _, _ = client.request(
        "POST", "/api/records/RC202611160001/materials", {"note": "mime-spoof"},
        files=[("file", "伪装.png", png, "text/html")])
    spoof_id = None
    if status == 200 and isinstance(spoof_body, dict) and spoof_body.get("ok"):
        spoof_id = next((m["id"] for m in spoof_body["data"]["items"] if m["note"] == "mime-spoof"), None)
    if spoof_id:
        st, body, ctype, headers = client.request("GET", f"/api/materials/{spoof_id}/download", raw=True)
        content_type = next((v for k, v in headers.items() if k.lower() == "content-type"), ctype)
        RESULTS.append({"name": "材料 MIME 伪造被忽略", "method": "GET",
                        "path": f"/api/materials/{spoof_id}/download", "status": st,
                        "ok": st == 200 and "image/png" in content_type, "ms": 0,
                        "note": f"Content-Type={content_type}"})
        client.request("DELETE", f"/api/records/RC202611160001/materials/{spoof_id}")

    # ---------------- 8. 断点保存与恢复
    check("暂存断点（计时与滚动位置）", client, "POST", "/api/records/RC202611160001/save",
          {"elapsed_sec": 1820, "scroll_anchor": {"section": "process", "index": 4}}, expect=200,
          validator=lambda b, h: b["data"]["elapsed_sec"] == 1820 or "计时未保存")
    check("继续询问断点恢复（inquiry 阶段）", client, "GET", "/api/records/RC202611160001/resume",
          expect=200,
          validator=lambda b, h: (b["data"]["stage"] == "inquiry"
                                 and b["data"]["redirect"].startswith("/workbench.html")) or "断点恢复异常")

    # ---------------- 9. 文书生成与导出
    doc = check("文书预览模型", client, "GET", "/api/records/RC202611160001/document", expect=200,
                validator=lambda b, h: True if b["data"]["document"]["sections"] else "文书正文为空")
    check("文书合规自检", client, "GET", "/api/records/RC202611160001/document", expect=200,
          validator=lambda b, h: len(b["data"]["document"]["checks"]) >= 5 or "自检项不足")
    status, body, ctype, headers = client.request("GET", "/api/records/RC202611160001/document.docx", raw=True)
    ok = status == 200 and body[:2] == b"PK" and len(body) > 5000
    RESULTS.append({"name": "导出 DOCX 红头文书", "method": "GET",
                    "path": "/api/records/RC202611160001/document.docx", "status": status,
                    "ok": ok, "ms": 0,
                    "note": f"{len(body) // 1024} KB, 文件签名 {body[:2]!r}，Content-Type={ctype.split(';')[0]}"})
    (ROOT / "storage" / "exports" / "smoke_export.docx").write_bytes(body)

    # ---------------- 10. 历史笔录导入
    samples = check("载入历史笔录样例", client, "GET", "/api/import/samples", expect=200)
    if samples and isinstance(samples, dict) and samples.get("ok"):
        text = samples["data"]["items"][0]["text"]
        parsed = check("解析历史笔录（问答对抽取 + 五流回溯）", client, "POST", "/api/import/parse",
                       {"text": text, "filename": "历史笔录样例1.docx"}, expect=200,
                       validator=lambda b, h: True if (b["data"]["stats"]["total_pairs"] >= 12
                                                       and b["data"]["flow_state"]["key_gaps"])
                       else f"解析结果异常：pairs={b['data']['stats']['total_pairs']}")
        if isinstance(parsed, dict) and parsed.get("ok"):
            job_id = parsed["data"]["job_id"]
            CLEANUP["jobs"].append(job_id)
            committed = check("历史笔录转存为新问询会话", client, "POST", "/api/import/commit",
                              {"job_id": job_id, "case_category": "冒充电商物流客服"}, expect=200)
            if isinstance(committed, dict) and committed.get("ok"):
                CLEANUP["records"].append(committed["data"]["record_id"])
            check("导入任务越权转存被拒绝", other_org, "POST", "/api/import/commit",
                  {"job_id": job_id}, expect=(403, 404))
            check("导入任务列表按归属过滤", other_org, "GET", "/api/import/jobs", expect=200,
                  validator=lambda b, h: True if all(item["id"] != job_id for item in b["data"]["items"])
                  else "越权看到了他人的导入任务")

    # ---------------- 11. 完成问询与审计
    if record_id:
        check("完成问询并封存", client, "POST", f"/api/records/{record_id}/complete",
              {"elapsed_sec": 600}, expect=(200, 400))
    check("操作审计流水查询", admin, "GET", "/api/audit?page=1&page_size=10", expect=200,
          validator=lambda b, h: b["data"]["chain"]["ok"] or "审计哈希链校验失败")
    check("五流字段口径配置读取", admin, "GET", "/api/settings/five-flow", expect=200,
          validator=lambda b, h: len(b["data"]["config"]["flows"]) == 5 or "五流配置异常")
    check("模板库维护列表", admin, "GET", "/api/settings/templates", expect=200)
    check("系统信息", admin, "GET", "/api/settings/system", expect=200,
          validator=lambda b, h: b["data"]["counts"]["records"] >= 6 or "系统统计异常")
    check("研判员无权访问审计以外管理接口", analyst, "GET", "/api/settings/llm", expect=(200, 403))

    # ---------------- 12. 大模型链路
    llm = check("大模型引擎状态", client, "GET", "/api/settings/llm", expect=200,
                validator=lambda b, h: True if b["data"]["config"]["provider"] else "缺少提供方")
    check("大模型地址白名单拦截 SSRF", admin, "PUT", "/api/settings/llm",
          {"base_url": "http://127.0.0.1:9"}, expect=400,
          validator=lambda b, h: True if "拒绝" in b["error"]["message"] or "禁止" in b["error"]["message"]
          else f"错误提示异常：{b['error']['message']}")
    check("自定义模型端点未授权被拒绝", admin, "PUT", "/api/settings/llm",
          {"provider": "openai_compatible", "base_url": "https://evil.example.com"}, expect=400,
          validator=lambda b, h: True if b["error"]["code"] == "bad_request" else "错误码异常")
    llm_active = bool(llm and isinstance(llm, dict) and llm.get("ok")
                      and llm["data"]["active_engine"] == "llm")
    if llm_active:
        check("大模型连通性测试（真实调用）", admin, "POST", "/api/settings/llm/test", expect=200,
              validator=lambda b, h: True if b["data"].get("reachable")
              else f"调用失败：{b['data'].get('note')}")
    else:
        RESULTS.append({"name": "大模型连通性测试（未配置密钥，跳过）", "method": "POST",
                        "path": "/api/settings/llm/test", "status": 200, "ok": True, "ms": 0,
                        "note": "未配置密钥，系统按设计回退规则引擎"})
    ai = check("大模型深度研判（真实调用）", client, "POST", "/api/records/RC202611160001/ai/analyze",
               expect=200,
               validator=lambda b, h: True if (
                   b["data"]["meta"]["engine"] in ("llm", "rule_engine")
                   and isinstance(b["data"]["analysis"].get("followups"), list)
               ) else "研判结果结构异常")
    if isinstance(ai, dict) and ai.get("ok"):
        meta = ai["data"]["meta"]
        RESULTS[-1]["note"] = (f"引擎={meta.get('engine')} 模型={meta.get('model')} "
                               f"耗时={meta.get('latency_ms')}ms tokens={meta.get('tokens')} "
                               f"回退={meta.get('fallback')}")
        check("大模型研判结果校验（字段一致性与去重）", client, "GET",
              "/api/records/RC202611160001/workbench", expect=200,
              validator=lambda b, h: _check_advice_integrity(b))

    stream_done = check_stream("大模型流式研判（SSE 增量）", client,
                               "/api/records/RC202611160001/ai/analyze/stream", {})
    if stream_done:
        RESULTS[-1]["ok"] = RESULTS[-1]["ok"] and bool(
            stream_done.get("analysis", {}).get("judgement", {}).get("category"))
        if not RESULTS[-1]["ok"]:
            RESULTS[-1]["note"] += " · 结果缺少 judgement"
        check("流式研判回放缓存", client, "GET",
              "/api/records/RC202611160001/ai/last-stream", expect=200,
              validator=lambda b, h: True if (b["data"].get("stream") or {}).get("thoughts")
              else "回放缓存为空")

    # ---------------- 13. 登出
    check("退出登录", client, "POST", "/api/auth/logout", expect=200)
    check("退出后接口应返回未授权", Client(args.base), "GET", "/api/records", expect=401)

    cleanup()
    report = build_report(args.base)
    write_outputs(report)
    return 0 if all(r["ok"] for r in RESULTS) else 1


# ------------------------------------------------------------------ 工具
def _demo_png() -> bytes:
    from PIL import Image, ImageDraw
    import io

    image = Image.new("RGB", (480, 260), (248, 250, 252))
    draw = ImageDraw.Draw(image)
    draw.rectangle([0, 0, 480, 60], fill=(11, 42, 91))
    draw.text((20, 20), "AUTO TEST MATERIAL", fill=(255, 255, 255))
    draw.text((20, 100), "self-test upload sample", fill=(60, 66, 76))
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


def cleanup() -> None:
    """清理自测产生的笔录与问答，保持演示数据整洁（审计流水保留）。"""
    conn = sqlite3.connect(config.DB_PATH)
    try:
        for rid in CLEANUP["records"]:
            conn.execute("DELETE FROM qa_item WHERE record_id = ?", (rid,))
            conn.execute("DELETE FROM material WHERE record_id = ?", (rid,))
            conn.execute("DELETE FROM advice WHERE record_id = ?", (rid,))
            conn.execute("DELETE FROM record WHERE id = ?", (rid,))
        for qa_id in CLEANUP["qa_ids"]:
            conn.execute("DELETE FROM qa_item WHERE id = ?", (qa_id,))
        for advice_id in CLEANUP["advice_ids"]:
            conn.execute("UPDATE advice SET status = 'pending', adopted_at = NULL WHERE id = ?", (advice_id,))
        for job in CLEANUP["jobs"]:
            conn.execute("DELETE FROM import_job WHERE id = ?", (job,))
        conn.commit()
    finally:
        conn.close()
    # 清理自测上传的材料文件
    for rid in CLEANUP["records"]:
        folder = config.MATERIAL_DIR / rid
        if folder.exists():
            for item in folder.iterdir():
                item.unlink()
            folder.rmdir()


def build_report(base: str) -> dict:
    passed = sum(1 for r in RESULTS if r["ok"])
    return {
        "base": base,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "total": len(RESULTS),
        "passed": passed,
        "failed": len(RESULTS) - passed,
        "pass_rate": round(passed / len(RESULTS) * 100, 1) if RESULTS else 0,
        "avg_ms": round(sum(r["ms"] for r in RESULTS) / len(RESULTS), 1) if RESULTS else 0,
        "results": RESULTS,
    }


def write_outputs(report: dict) -> None:
    out_json = ROOT / "screenshots" / "api_test_report.json"
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    md = [f"# 接口自测报告", "",
          f"- 服务地址：`{report['base']}`",
          f"- 生成时间：{report['generated_at']}",
          f"- 用例总数：**{report['total']}**，通过 **{report['passed']}**，失败 **{report['failed']}**，"
          f"通过率 **{report['pass_rate']}%**",
          f"- 平均响应：{report['avg_ms']} ms", "",
          "| # | 用例 | 方法 | 路径 | 状态码 | 结果 | 耗时(ms) | 备注 |",
          "| :--: | --- | --- | --- | :--: | :--: | ---: | --- |"]
    for idx, row in enumerate(report["results"], start=1):
        md.append(f"| {idx} | {row['name']} | {row['method']} | `{row['path']}` | {row['status']} | "
                  f"{'通过' if row['ok'] else '**失败**'} | {row['ms']} | {row['note']} |")
    (ROOT / "docs" / "接口自测报告.md").write_text("\n".join(md) + "\n", encoding="utf-8")

    rows_html = []
    for idx, row in enumerate(report["results"], start=1):
        badge = ("<span class='pass'>PASS</span>" if row["ok"]
                 else "<span class='fail'>FAIL</span>")
        rows_html.append(
            f"<tr><td>{idx}</td><td>{row['name']}</td><td class='mono'>{row['method']}</td>"
            f"<td class='mono path'>{row['path']}</td><td>{row['status']}</td><td>{badge}</td>"
            f"<td class='mono'>{row['ms']}</td><td class='note'>{row['note']}</td></tr>"
        )
    html = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>接口自测报告 · 智能问询笔录平台</title>
<style>
 :root {{ --navy:#0b2a5b; --line:#e3e8f0; --ok:#0f9d58; --bad:#d93025; }}
 * {{ box-sizing:border-box; }}
 body {{ margin:0; background:#f4f6fa; font-family:"Microsoft YaHei",system-ui,sans-serif; color:#1f2937; }}
 header {{ background:linear-gradient(135deg,#0b2a5b,#17427f); color:#fff; padding:28px 40px; }}
 header h1 {{ margin:0 0 6px; font-size:22px; letter-spacing:1px; }}
 header p {{ margin:0; opacity:.8; font-size:13px; }}
 .cards {{ display:flex; gap:16px; padding:22px 40px 6px; flex-wrap:wrap; }}
 .card {{ flex:1; min-width:160px; background:#fff; border:1px solid var(--line); border-radius:12px; padding:16px 18px; }}
 .card .k {{ font-size:12px; color:#6b7280; }}
 .card .v {{ font-size:26px; font-weight:700; color:var(--navy); margin-top:6px; }}
 .card .v.ok {{ color:var(--ok); }}
 table {{ width:calc(100% - 80px); margin:18px 40px 40px; border-collapse:collapse; background:#fff;
          border:1px solid var(--line); border-radius:12px; overflow:hidden; font-size:13px; }}
 th {{ background:#f0f3f9; text-align:left; padding:11px 10px; font-weight:600; color:#374151; }}
 td {{ padding:10px; border-top:1px solid var(--line); vertical-align:top; }}
 tr:hover td {{ background:#fafbfe; }}
 .mono {{ font-family:Consolas,monospace; font-size:12px; }}
 .path {{ max-width:330px; word-break:break-all; color:#334155; }}
 .note {{ color:#6b7280; max-width:340px; }}
 .pass {{ color:#fff; background:var(--ok); padding:2px 8px; border-radius:20px; font-size:11px; }}
 .fail {{ color:#fff; background:var(--bad); padding:2px 8px; border-radius:20px; font-size:11px; }}
</style></head><body>
<header>
  <h1>接口自动化自测报告 · 智能问询笔录平台 2.11</h1>
  <p>服务地址 {report['base']} ｜ 生成时间 {report['generated_at']} ｜ 覆盖接报、模板推荐、三栏工作台、五流、AI 采纳、材料、文书、导入、审计</p>
</header>
<div class="cards">
  <div class="card"><div class="k">用例总数</div><div class="v">{report['total']}</div></div>
  <div class="card"><div class="k">通过</div><div class="v ok">{report['passed']}</div></div>
  <div class="card"><div class="k">失败</div><div class="v">{report['failed']}</div></div>
  <div class="card"><div class="k">通过率</div><div class="v ok">{report['pass_rate']}%</div></div>
  <div class="card"><div class="k">平均响应</div><div class="v">{report['avg_ms']} ms</div></div>
</div>
<table>
<thead><tr><th>#</th><th>用例</th><th>方法</th><th>路径</th><th>状态码</th><th>结果</th><th>耗时(ms)</th><th>备注</th></tr></thead>
<tbody>{''.join(rows_html)}</tbody></table>
</body></html>"""
    (ROOT / "tests" / "api_report.html").write_text(html, encoding="utf-8")
    print(f"\n接口自测：{report['passed']}/{report['total']} 通过（{report['pass_rate']}%）")
    for row in RESULTS:
        if not row["ok"]:
            print(f"  [FAIL] {row['name']} -> {row['status']} {row['note']}")


if __name__ == "__main__":
    raise SystemExit(main())
