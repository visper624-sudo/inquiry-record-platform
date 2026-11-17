# -*- coding: utf-8 -*-
"""运行单元测试并生成可视化 HTML 报告（用于截图与验收材料）。

    python tests/run_tests.py
产出：
    tests/unit_report.html
    screenshots/unit_test_report.json
"""
from __future__ import annotations

import io
import json
import sys
import time
import unittest
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "backend"))

import test_units  # noqa: E402


class CollectingResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.records: list[dict] = []
        self._started = 0.0

    def startTest(self, test):
        self._started = time.perf_counter()
        super().startTest(test)

    def _record(self, test, status, message=""):
        self.records.append({
            "name": test.id().split(".", 1)[-1],
            "module": type(test).__name__,
            "doc": (test.shortDescription() or "").strip(),
            "status": status,
            "ms": round((time.perf_counter() - self._started) * 1000, 1),
            "message": message,
        })

    def addSuccess(self, test):
        super().addSuccess(test)
        self._record(test, "pass")

    def addFailure(self, test, err):
        super().addFailure(test, err)
        self._record(test, "fail", self._exc_info_to_string(err, test).splitlines()[-1][:240])

    def addError(self, test, err):
        super().addError(test, err)
        self._record(test, "error", self._exc_info_to_string(err, test).splitlines()[-1][:240])

    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        self._record(test, "skip", reason)


def build_html(records: list[dict], duration: float, db_path: str) -> str:
    passed = sum(1 for r in records if r["status"] == "pass")
    failed = sum(1 for r in records if r["status"] in ("fail", "error"))
    rows = []
    for idx, row in enumerate(records, start=1):
        badge = {
            "pass": "<span class='pass'>PASS</span>",
            "fail": "<span class='fail'>FAIL</span>",
            "error": "<span class='fail'>ERROR</span>",
            "skip": "<span class='skip'>SKIP</span>",
        }[row["status"]]
        rows.append(
            f"<tr><td>{idx}</td><td class='mono'>{row['module']}</td>"
            f"<td>{row['name']}</td><td>{badge}</td><td class='mono'>{row['ms']}</td>"
            f"<td class='note'>{row['message']}</td></tr>"
        )
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>单元测试报告 · 智能问询笔录平台</title>
<style>
 :root {{ --navy:#0b2a5b; --line:#e3e8f0; --ok:#0f9d58; --bad:#d93025; }}
 * {{ box-sizing:border-box; }}
 body {{ margin:0; background:#f4f6fa; font-family:"Microsoft YaHei",system-ui,sans-serif; color:#1f2937; }}
 header {{ background:linear-gradient(135deg,#071e42,#17427f); color:#fff; padding:28px 40px; }}
 header h1 {{ margin:0 0 6px; font-size:22px; letter-spacing:1px; }}
 header p {{ margin:0; opacity:.82; font-size:13px; }}
 .cards {{ display:flex; gap:16px; padding:22px 40px 6px; flex-wrap:wrap; }}
 .card {{ flex:1; min-width:150px; background:#fff; border:1px solid var(--line); border-radius:12px; padding:16px 18px; }}
 .card .k {{ font-size:12px; color:#6b7280; }}
 .card .v {{ font-size:26px; font-weight:700; color:var(--navy); margin-top:6px; }}
 .v.ok {{ color:var(--ok); }}
 table {{ width:calc(100% - 80px); margin:18px 40px 40px; border-collapse:collapse; background:#fff;
          border:1px solid var(--line); border-radius:12px; overflow:hidden; font-size:13px; }}
 th {{ background:#f0f3f9; text-align:left; padding:11px 10px; font-weight:600; color:#374151; }}
 td {{ padding:10px; border-top:1px solid var(--line); }}
 .mono {{ font-family:Consolas,monospace; font-size:12px; color:#334155; }}
 .note {{ color:#6b7280; }}
 .pass {{ color:#fff; background:var(--ok); padding:2px 8px; border-radius:20px; font-size:11px; }}
 .fail {{ color:#fff; background:var(--bad); padding:2px 8px; border-radius:20px; font-size:11px; }}
 .skip {{ color:#fff; background:#9aa3b2; padding:2px 8px; border-radius:20px; font-size:11px; }}
</style></head><body>
<header>
  <h1>单元测试报告 · 智能问询笔录平台 2.11</h1>
  <p>覆盖：模板库与校验 · 推荐算法 · 多模板合并 · 五流抽取与缺口判定 · 要素推断 · 文书模型与 DOCX 渲染 ·
     历史笔录解析 · 审计哈希链 · 组织数据权限</p>
</header>
<div class="cards">
  <div class="card"><div class="k">用例总数</div><div class="v">{len(records)}</div></div>
  <div class="card"><div class="k">通过</div><div class="v ok">{passed}</div></div>
  <div class="card"><div class="k">失败</div><div class="v">{failed}</div></div>
  <div class="card"><div class="k">通过率</div><div class="v ok">{round(passed / max(1, len(records)) * 100, 1)}%</div></div>
  <div class="card"><div class="k">总耗时</div><div class="v">{duration:.2f}s</div></div>
</div>
<table>
<thead><tr><th>#</th><th>测试类</th><th>用例</th><th>结果</th><th>耗时(ms)</th><th>信息</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table>
<p style="padding:0 40px 30px;color:#6b7280;font-size:12px">测试数据库：{db_path} ｜ 生成时间：{datetime.now():%Y-%m-%d %H:%M:%S}</p>
</body></html>"""


def main() -> int:
    stream = io.StringIO()
    suite = unittest.TestLoader().loadTestsFromModule(test_units)
    started = time.perf_counter()
    runner = unittest.TextTestRunner(stream=stream, verbosity=2, resultclass=CollectingResult)
    result = runner.run(suite)
    duration = time.perf_counter() - started
    records = result.records
    passed = sum(1 for r in records if r["status"] == "pass")
    failed = len(records) - passed

    (ROOT / "tests" / "unit_report.html").write_text(
        build_html(records, duration, "storage/test.db"), encoding="utf-8")
    (ROOT / "screenshots").mkdir(parents=True, exist_ok=True)
    (ROOT / "screenshots" / "unit_test_report.json").write_text(
        json.dumps({"total": len(records), "passed": passed, "failed": failed,
                    "duration_sec": round(duration, 2), "records": records},
                   ensure_ascii=False, indent=2), encoding="utf-8")

    for row in records:
        mark = {"pass": "ok", "fail": "FAIL", "error": "ERROR", "skip": "skip"}[row["status"]]
        print(f"  [{mark:5s}] {row['module']}.{row['name']}  ({row['ms']} ms) {row['message']}")
    print(f"\n单元测试：{passed}/{len(records)} 通过，耗时 {duration:.2f}s")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
