# -*- coding: utf-8 -*-
"""SSE 流式研判自检：打印事件序列、增量数量与最终结果。"""
from __future__ import annotations

import json
import time
import urllib.request
from http.cookiejar import CookieJar

BASE = "http://127.0.0.1:8765"
jar = CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))


def login() -> None:
    req = urllib.request.Request(
        BASE + "/api/auth/login",
        data=json.dumps({"badge_no": "100101", "password": "123456"}).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with opener.open(req, timeout=20):
        pass


def main() -> int:
    login()
    request = urllib.request.Request(
        BASE + "/api/records/RC202611160001/ai/analyze/stream",
        data=b"{}",
        headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
        method="POST")
    started = time.perf_counter()
    counts: dict[str, int] = {}
    first_delta = None
    done = None
    with opener.open(request, timeout=120) as resp:
        current = "message"
        for raw in resp:
            line = raw.decode("utf-8", "ignore").strip()
            if not line:
                continue
            if line.startswith("event:"):
                current = line[6:].strip()
                counts[current] = counts.get(current, 0) + 1
            elif line.startswith("data:"):
                data = json.loads(line[5:].strip())
                if current == "delta" and first_delta is None:
                    first_delta = round((time.perf_counter() - started) * 1000)
                if current == "thought":
                    print(f"  [thought] {data.get('kind')}: {data.get('text')[:60]}")
                if current == "done":
                    done = data
                if current == "error":
                    print("  [error]", data)
    total = round((time.perf_counter() - started) * 1000)
    print("\n事件统计：", counts)
    print(f"首包延迟：{first_delta} ms；总耗时：{total} ms")
    if not done:
        print("[FAIL] 未收到 done 事件")
        return 1
    meta = done.get("meta", {})
    analysis = done.get("analysis", {})
    print(f"引擎：{meta.get('engine')} 模型：{meta.get('model')} tokens：{meta.get('tokens')} "
          f"streamed：{meta.get('streamed')}")
    print("研判摘要：", analysis.get("summary"))
    print("追问：", [f"{f['flow']}.{f['field']}" for f in analysis.get("followups", [])])
    print("建议条数：", len(done.get("advice", [])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
