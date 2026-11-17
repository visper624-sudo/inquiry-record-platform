# -*- coding: utf-8 -*-
"""大模型链路自检：配置状态 → 深度研判 → 建议落库 → 采纳写回问答。"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from http.cookiejar import CookieJar

BASE = "http://127.0.0.1:8765"
jar = CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))


def call(method: str, path: str, payload=None) -> dict:
    data = json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None
    headers = {"Content-Type": "application/json"} if data else {}
    req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with opener.open(req, timeout=90) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        return {"ok": False, "http": exc.code, "body": exc.read().decode()[:400]}


def main() -> int:
    call("POST", "/api/auth/login", {"badge_no": "100101", "password": "123456"})

    status = call("GET", "/api/settings/llm")["data"]
    print("== 引擎状态 ==")
    print("  启用：", status["config"]["enabled"], " 提供方：", status["config"]["provider"],
          " 模型：", status["config"]["model"])
    print("  密钥：", status["key_configured"], status["key_source"])
    print("  当前引擎：", status["active_engine"], "→", status["engine_label"])

    test = call("POST", "/api/settings/llm/test", {})
    if not test.get("ok"):
        admin_jar = CookieJar()
        admin = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(admin_jar))
        req = urllib.request.Request(
            BASE + "/api/auth/login",
            data=json.dumps({"badge_no": "100001", "password": "123456"}).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        with admin.open(req, timeout=20):
            pass
        req = urllib.request.Request(BASE + "/api/settings/llm/test", data=b"{}",
                                     headers={"Content-Type": "application/json"}, method="POST")
        with admin.open(req, timeout=60) as resp:
            test = json.loads(resp.read().decode())
    print("  连通性：", test.get("data", {}).get("reachable"), " 延迟：",
          test.get("data", {}).get("latency_ms"), "ms  示例：", test.get("data", {}).get("sample"))

    result = call("POST", "/api/records/RC202611160001/ai/analyze", {})
    if not result.get("ok"):
        print("[FAIL] 深度研判接口：", result)
        return 1
    data = result["data"]
    meta = data["meta"]
    analysis = data["analysis"]
    print("\n== 深度研判 ==")
    print(f"  引擎：{meta.get('engine')}  模型：{meta.get('model')}  耗时：{meta.get('latency_ms')}ms  "
          f"tokens：{meta.get('tokens')}  回退：{meta.get('fallback')}")
    print("  摘要：", analysis["summary"])
    print("  类型：", analysis["judgement"]["category"], analysis["judgement"]["confidence"], "%")
    for basis in analysis["judgement"]["basis"]:
        print("    ·", basis)
    print("  侦查指引：")
    for guide in analysis["guides"]:
        print(f"    - {guide['title']}：{guide['content']}")
    print("  追问建议：")
    for follow in analysis["followups"]:
        print(f"    - [{follow['flow']}.{follow['field']}] {follow['question']}  （理由：{follow.get('why')}）")

    llm_advice = [a for a in data["advice"] if a["engine"] == "llm"]
    print(f"\n  落库建议：总计 {len(data['advice'])} 条，其中大模型 {len(llm_advice)} 条")
    for item in data["advice"]:
        print(f"    · [{item['engine']}/{item['kind']}] {item['title']}")
    duplicate_fields = {}
    for item in data["advice"]:
        if item["kind"] == "followup":
            key = (item["payload"].get("flow"), item["payload"].get("field"))
            duplicate_fields[key] = duplicate_fields.get(key, 0) + 1
    dup = {k: v for k, v in duplicate_fields.items() if v > 1}
    print("  同一缺口重复卡片：", dup or "无")
    print("  统计：", data["advice_stats"])

    workbench = call("GET", "/api/records/RC202611160001/workbench")["data"]
    pending_llm = [a for a in workbench["advice"] if a["engine"] == "llm" and a["status"] == "pending"]
    print("  工作台中大模型待采纳：", len(pending_llm))

    follow = next((a for a in pending_llm if a["kind"] == "followup"), None)
    if follow:
        adopted = call("POST", f"/api/records/RC202611160001/advice/{follow['id']}/adopt", {})
        if adopted.get("ok"):
            qa = adopted["data"]["qa"]
            print("\n== 采纳写回 ==")
            print(f"  问答来源：{qa['source']}  章节：{qa.get('flow')}/{qa.get('field')}")
            print(f"  问题：{qa['question'][:80]}")
            # 清理：删除采纳产生的问答，避免污染演示数据
            call("DELETE", f"/api/records/RC202611160001/qa/{qa['id']}")
            print("  （已清理测试问答）")
        else:
            print("[FAIL] 采纳失败：", adopted)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
