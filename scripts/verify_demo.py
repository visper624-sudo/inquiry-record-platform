# -*- coding: utf-8 -*-
"""演示数据与关键链路自检（打印摘要，便于核对截图前状态）。"""
from __future__ import annotations

import json
import sys
import urllib.parse
import urllib.request
from http.cookiejar import CookieJar
from pathlib import Path

BASE = "http://127.0.0.1:8765"
jar = CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))


def call(method, path, payload=None):
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    with opener.open(req, timeout=20) as resp:
        return json.loads(resp.read().decode())


call("POST", "/api/auth/login", {"badge_no": "100101", "password": "123456"})

check = call("GET", "/api/records/RC202611160004/templates")
print("== RC004 模板推荐 ==")
for item in check["data"]["recommendations"]:
    print(f"  {item['rank']}. {item['name']}  匹配度 {item['match_score']}%  命中 {len(item['signal_hits'])} 个信号词")
print("  判定:", check["data"]["judgement"]["category"], check["data"]["judgement"]["confidence"], "%")

workbench = call("GET", "/api/records/RC202611160001/workbench")
print("\n== RC001 工作台 ==")
print("  进度:", workbench["data"]["progress"], " 完整度:", workbench["data"]["flow"]["completeness"], "%")
for flow in workbench["data"]["flow"]["flows"]:
    print(f"  {flow['name']}: {flow['status_label']} {flow['collected_count']}/{flow['required_total']} 缺口 {len(flow['gaps'])}")
print("  待采纳建议:", sum(1 for a in workbench["data"]["advice"] if a["status"] == "pending"),
      " 追问:", sum(1 for a in workbench["data"]["advice"] if a["kind"] == "followup"))

doc = call("GET", "/api/records/RC202611160002/document")
print("\n== RC002 文书 ==")
print("  红头:", doc["data"]["document"]["org_header"])
print("  询问时间:", doc["data"]["document"]["case"]["inquiry_start"], "至", doc["data"]["document"]["case"]["inquiry_end"])
print("  已作答:", doc["data"]["document"]["meta"]["answered_items"], " 过滤:", doc["data"]["document"]["meta"]["filtered_items"])
print("  自检:", [(c["item"], c["passed"]) for c in doc["data"]["document"]["checks"]])

text = json.loads((Path(__file__).resolve().parent.parent / "data" / "demo" / "history_samples.json")
                  .read_text(encoding="utf-8"))[0]["text"]
parsed = call("POST", "/api/import/parse", {"text": text, "filename": "样例1.docx"})
print("\n== 历史笔录解析 ==")
print("  问答对:", parsed["data"]["stats"]["total_pairs"], " 章节分布:", parsed["data"]["stats"]["section_distribution"])
print("  重点缺口:", [f"{g['flow']}·{g['label']}" for g in parsed["data"]["key_gaps"]][:6])
print("  完整度:", parsed["data"]["flow_state"]["completeness"], "%")
