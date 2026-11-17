# -*- coding: utf-8 -*-
"""单元测试：模板库、推荐、五流抽取、文书生成、审计链、权限范围、导入解析。

运行：
    python tests/test_units.py -v
（测试使用独立的 storage/test.db，不会污染演示数据库）
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("PRACTICE_DB", str(ROOT / "storage" / "test.db"))
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "scripts"))

import config  # noqa: E402
from core import audit, db, security  # noqa: E402
from services import ai_svc, docx_svc, fiveflow_svc, importer_svc, templates_svc  # noqa: E402
import seed  # noqa: E402
import validate_templates  # noqa: E402


def setUpModule():
    seed.seed_all(force=True)
    print(f"\n[test] 测试库：{config.DB_PATH}")


class TestSecurity(unittest.TestCase):
    def test_password_hash_roundtrip(self):
        encoded = security.hash_password("Abc123456")
        self.assertTrue(encoded.startswith("pbkdf2_sha256$"))
        self.assertTrue(security.verify_password("Abc123456", encoded))
        self.assertFalse(security.verify_password("wrong", encoded))

    def test_org_scope(self):
        police = security.load_user("U002")
        admin = security.load_user("U001")
        self.assertEqual(police["org_scope"], ["O003"])
        self.assertIsNone(admin["org_scope"])
        self.assertEqual(security.org_path("O003"), "江城市公安局 / 江城市公安局城东分局 / 城东分局长虹路派出所")


class TestTemplates(unittest.TestCase):
    def test_catalog_complete(self):
        items = templates_svc.list_templates()
        self.assertEqual(len(items), 20)
        general = [t for t in items if t["is_general"]]
        self.assertEqual(len(general), 1)
        self.assertEqual(general[0]["template_id"], "T00")
        total = sum(t["question_count"] for t in items)
        self.assertGreaterEqual(total, 400, f"模板问题总数过少: {total}")
        self.assertEqual(len(templates_svc.categories()), 20, "19 类案由 + 1 个通用模板分类")

    def test_validator_passes(self):
        errors, stats = validate_templates.validate(ROOT / "data" / "templates")
        self.assertEqual(errors, [], f"模板校验未通过: {errors[:3]}")
        self.assertEqual(stats["files"], 20)

    def test_required_fields_covered_by_general_template(self):
        general = templates_svc.get_template("T00")
        covered = {(q.get("flow"), q.get("field")) for s in general["sections"] for q in s["questions"]}
        missing = []
        for flow in fiveflow_svc.flows():
            for field in flow["fields"]:
                if field["required"] and (flow["key"], field["key"]) not in covered:
                    missing.append(f"{flow['name']}/{field['label']}")
        self.assertEqual(missing, [], f"通用模板未覆盖必填要素: {missing}")

    def test_recommend_investment(self):
        brief = ("受害人在股票交流群中被陌生人添加微信，对方自称券商客服经理，推荐“国盛智投”APP，"
                 "以导师荐股、稳赚不赔为诱饵诱导平台充值，受害人转账80万元后无法提现。")
        recs = templates_svc.recommend(brief, "虚假网络投资理财")
        self.assertTrue(recs)
        self.assertEqual(recs[0]["template_id"], "T03", f"实际推荐: {[r['name'] for r in recs]}")
        self.assertGreater(recs[0]["match_score"], 50)
        self.assertTrue(recs[0]["signal_hits"])

    def test_recommend_brushing(self):
        brief = ("受害人在短视频平台看到兼职刷单广告，添加派单员后被拉入任务群，先做小额任务获得返利，"
                 "后续被要求垫付大额任务，累计转账6.8万元后平台拒绝提现。")
        recs = templates_svc.recommend(brief, "刷单返利")
        self.assertEqual(recs[0]["template_id"], "T05", f"实际推荐: {[r['name'] for r in recs]}")

    def test_merge_outline(self):
        merged = templates_svc.merge_outline(["T03", "T00"])
        self.assertEqual([s["key"] for s in merged["sections"]],
                         ["opening", "contact", "process", "loss", "evidence"])
        texts = [q["text"] for s in merged["sections"] for q in s["questions"]]
        self.assertEqual(len(texts), len(set(texts)), "合并后存在重复问题")
        self.assertTrue(all(q["source_template"] in ("T03", "T00")
                            for s in merged["sections"] for q in s["questions"]))


class TestFiveFlow(unittest.TestCase):
    def test_extract_card_and_mask(self):
        items = [{
            "id": "q1", "section": "loss",
            "question": "对方收款卡号是多少？",
            "answer": "对方卡号是6217000000000005678，开户行是农业银行。",
            "flow": "fund", "field": "counterparty_card",
        }]
        state = fiveflow_svc.analyze(items)
        fund = next(f for f in state["flows"] if f["key"] == "fund")
        card = next(x for x in fund["fields"] if x["key"] == "counterparty_card")
        self.assertEqual(card["status"], "collected")
        self.assertEqual(card["value"], "6217000000000005678")
        self.assertIn("开户行", card["evidence_span"])

    def test_key_gap_when_missing_counterparty(self):
        items = [{
            "id": "q1", "section": "loss",
            "question": "你累计损失多少？",
            "answer": "一共损失80万元。",
            "flow": "fund", "field": "total_loss",
        }]
        state = fiveflow_svc.analyze(items)
        fund = next(f for f in state["flows"] if f["key"] == "fund")
        self.assertEqual(fund["status"], "key_gap")
        gap_labels = {g["label"] for g in fund["gaps"]}
        self.assertIn("对方卡号", gap_labels)
        self.assertIn("对方收款户名", gap_labels)
        self.assertTrue(state["key_gaps"])
        self.assertEqual(state["key_gaps"][0]["flow"], "fund")

    def test_full_fund_collection(self):
        answers = {
            "transfer_time": ("你什么时候转的账？", "10月18日14时06分转的第一笔。"),
            "transfer_amount": ("你转了多少钱？", "第一笔转账30万元。"),
            "pay_channel": ("你通过什么方式转账？", "都是通过手机银行转账的。"),
            "payer_card": ("你转出资金使用哪张卡？", "我用本人名下的工商银行卡，卡号6222020000000001234。"),
            "counterparty_bank": ("对方开户行是哪个银行？", "对方收款账户是建设银行。"),
            "counterparty_card": ("对方卡号是多少？", "对方卡号是6217000000000005678。"),
            "counterparty_name": ("对方户名是什么？", "收款户名：陈某。"),
            "total_loss": ("你一共损失多少钱？", "累计损失80万元。"),
        }
        items = [{"id": f"q{i}", "section": "loss", "question": q, "answer": a,
                  "flow": "fund", "field": f} for i, (f, (q, a)) in enumerate(answers.items())]
        state = fiveflow_svc.analyze(items)
        fund = next(f for f in state["flows"] if f["key"] == "fund")
        self.assertEqual(fund["status"], "collected", f"缺口: {fund['gap_labels']}")
        self.assertEqual(fund["coverage_pct"], 100.0)

    def test_not_involved_requires_manual(self):
        items = [{
            "id": "q1", "section": "process",
            "question": "你被骗的经过是怎样的？",
            "answer": "对方让我下载APP并转账。",
            "flow": None, "field": None,
        }]
        state = fiveflow_svc.analyze(items, excluded_flows=["delivery"])
        delivery = next(f for f in state["flows"] if f["key"] == "delivery")
        self.assertEqual(delivery["status"], "not_involved")
        self.assertEqual(delivery["status_label"], "不涉及")
        self.assertEqual(delivery["radar_value"], 100)

    def test_gap_priority_order(self):
        items = [{
            "id": "q1", "section": "loss",
            "question": "你损失了多少钱？",
            "answer": "损失80万元。",
            "flow": "fund", "field": "total_loss",
        }]
        state = fiveflow_svc.analyze(items)
        flows_of_gaps = [g["flow"] for g in state["gaps"]]
        self.assertEqual(flows_of_gaps[0], "fund", "资金流缺口必须排在首位")
        self.assertEqual(flows_of_gaps[-1], "delivery", "寄递流缺口应排在末位")

    def test_flow_inference_from_question(self):
        flow, field, conf = fiveflow_svc.infer_flow_field("对方的收款卡号是多少？", "loss")
        self.assertEqual((flow, field), ("fund", "counterparty_card"))
        self.assertGreater(conf, 0.6)
        flow, field, _ = fiveflow_svc.infer_flow_field("对方的手机号码是多少？", "process")
        self.assertEqual((flow, field), ("communication", "call_number"))


class TestImporter(unittest.TestCase):
    def test_parse_history_sample(self):
        samples = __import__("json").loads(
            (ROOT / "data" / "demo" / "history_samples.json").read_text(encoding="utf-8"))
        analysis = importer_svc.analyze_text(samples[0]["text"], "历史笔录.docx", "docx")
        self.assertGreaterEqual(analysis["stats"]["total_pairs"], 12)
        self.assertGreaterEqual(analysis["stats"]["answered_pairs"], 10)
        gap_labels = {g["label"] for g in analysis["key_gaps"]}
        self.assertIn("涉案电话号码", gap_labels)
        self.assertIn("涉案网址/域名", gap_labels)
        self.assertTrue(analysis["flow_state"]["completeness"] > 0)

    def test_classify_sections(self):
        self.assertEqual(importer_svc.classify_section("我们是民警，现依法向你询问，你听清楚了吗？", 0), "opening")
        self.assertEqual(importer_svc.classify_section("你累计转账多少钱？对方卡号是多少？", 5), "loss")
        self.assertEqual(importer_svc.classify_section("你是否保存了聊天记录截图？", 8), "evidence")


class TestDocument(unittest.TestCase):
    def _model(self):
        record = db.one("SELECT * FROM record WHERE id = 'RC202611160002'")
        self.assertIsNotNone(record)
        qa_items = db.rows("SELECT * FROM qa_item WHERE record_id = ? AND is_deleted = 0 ORDER BY section, sort_no",
                           (record["id"],))
        flow_state = fiveflow_svc.analyze(qa_items, db.json_load(record["flow_excluded"], []))
        user = {"name": "张伟", "badge_no": "100101",
                "org_path": "江城市公安局 / 江城市公安局城东分局 / 城东分局长虹路派出所"}
        return docx_svc.build_doc_model(record, qa_items, flow_state, user), record, qa_items

    def test_doc_model_filters_empty_answers(self):
        model, record, qa_items = self._model()
        answered = sum(1 for i in qa_items if (i["answer"] or "").strip())
        self.assertEqual(model["meta"]["answered_items"], answered)
        self.assertEqual(model["meta"]["filtered_items"], len(qa_items) - answered)
        for section in model["sections"]:
            for item in section["items"]:
                self.assertTrue(item["answer"].strip(), "正文出现空答案")
        self.assertTrue(model["checks"])

    def test_render_docx_and_reopen(self):
        model, _, _ = self._model()
        path = docx_svc.render_docx(model, Path(config.EXPORT_DIR) / "_unit_test.docx")
        self.assertTrue(path.exists())
        from docx import Document

        doc = Document(str(path))
        texts = [p.text for p in doc.paragraphs if p.text.strip()]
        self.assertEqual(texts[0], model["org_header"])
        self.assertIn("询问笔录", texts)
        ask_count = sum(1 for t in texts if t.startswith("问："))
        answer_count = sum(1 for t in texts if t.startswith("答："))
        self.assertGreater(ask_count, 5)
        self.assertEqual(ask_count, answer_count, "问句与答句数量应一致")
        self.assertTrue(any("被询问人（签名）" in t for t in texts), "缺少签名区")
        red_runs = [r for p in doc.paragraphs for r in p.runs
                    if r.font.color and r.font.color.rgb and str(r.font.color.rgb) == "C00000"]
        self.assertTrue(red_runs, "红头未使用标准红色")


class TestLLM(unittest.TestCase):
    """大模型输出校验层（不发起网络请求，仅验证结构约束与兜底逻辑）。"""

    def _flow_state(self):
        items = [{
            "id": "q1", "section": "loss",
            "question": "你累计损失多少？",
            "answer": "一共损失80万元。",
            "flow": "fund", "field": "total_loss",
        }]
        return fiveflow_svc.analyze(items)

    def test_normalize_filters_unknown_fields(self):
        state = self._flow_state()
        raw = {
            "summary": "研判为虚假投资理财",
            "type_judgement": {"category": "虚假网络投资理财", "confidence": 88, "basis": ["依据一", "依据二"]},
            "investigation_guides": [
                {"title": "紧急止付", "content": "对涉案账户开展紧急止付"},
                {"title": "调证", "content": "调取涉案账户开户信息"},
                {"title": "勘验", "content": "对涉案APP开展电子数据勘验"},
            ],
            "followups": [
                {"flow": "fund", "field": "counterparty_card",
                 "question": "对方收款卡号是多少？", "why": "止付需要"},
                {"flow": "fund", "field": "not_exist_field",
                 "question": "这是一个不存在的字段问题？", "why": "非法字段"},
                {"flow": "communication", "field": "call_number",
                 "question": "对方来电号码是多少？", "why": "通信流缺口"},
            ],
        }
        analysis = ai_svc._normalize_analysis(raw, state)
        fields = [(f["flow"], f["field"]) for f in analysis["followups"]]
        self.assertIn(("fund", "counterparty_card"), fields)
        self.assertNotIn(("fund", "not_exist_field"), fields, "不存在的字段必须被丢弃")
        self.assertIn(("communication", "call_number"), fields, "真实存在的缺口字段应被保留")
        self.assertEqual(analysis["judgement"]["confidence"], 88.0)
        self.assertEqual(len(analysis["guides"]), 3)

    def test_normalize_question_punctuation(self):
        state = self._flow_state()
        raw = {
            "summary": "测试",
            "type_judgement": {"category": "虚假网络投资理财", "confidence": 60, "basis": ["b"]},
            "investigation_guides": [{"title": "t", "content": "c"}],
            "followups": [{"flow": "fund", "field": "counterparty_name",
                           "question": "对方收款账户户名是什么。", "why": "核实身份"}],
        }
        analysis = ai_svc._normalize_analysis(raw, state)
        question = analysis["followups"][0]["question"]
        self.assertTrue(question.endswith("？"), question)
        self.assertNotIn("。？", question)

    def test_extract_json_handles_code_fence(self):
        text = '```json\n{"summary": "ok"}\n```'
        self.assertEqual(ai_svc._extract_json(text), {"summary": "ok"})

    def test_partial_thoughts_extraction(self):
        extractor = ai_svc.PartialThoughts()
        first = extractor.feed('{"summary":"受害人被诱导充值80万元", "type_judgement":{"category":"虚假网络投资理财"')
        kinds = {item["kind"]: item["text"] for item in first}
        self.assertEqual(kinds.get("summary"), "受害人被诱导充值80万元")
        self.assertEqual(kinds.get("category"), "虚假网络投资理财")
        second = extractor.feed('}, "followups":[{"flow":"fund","field":"counterparty_card",'
                                '"question":"对方收款卡号是多少？","why":"止付需要"}]}')
        kinds2 = {item["kind"]: item["text"] for item in second}
        self.assertEqual(kinds2.get("question"), "对方收款卡号是多少？")
        self.assertEqual(kinds2.get("why"), "止付需要")
        self.assertEqual(extractor.feed('}]}'), [], "已输出片段不应重复")

    def test_fallback_analysis_shape(self):
        state = self._flow_state()
        analysis = ai_svc._fallback_analysis({"case_category": "虚假网络投资理财"}, state, [], "测试回退")
        self.assertEqual(analysis["engine"], "rule_engine")
        self.assertTrue(analysis["guides"])
        self.assertTrue(analysis["followups"])
        self.assertTrue(all(f["question"].endswith(("？", "?")) for f in analysis["followups"]))


class TestHardening(unittest.TestCase):
    """安全/性能加固回归（对应审查报告 S1/S2/S6/P10）。"""

    def test_password_policy(self):
        with self.assertRaises(ValueError):
            security.validate_password("123456")
        with self.assertRaises(ValueError):
            security.validate_password("short")
        with self.assertRaises(ValueError):
            security.validate_password("aaaaaaaa")
        security.validate_password("Abc12345!")

    def test_base_url_validation(self):
        with self.assertRaises(ValueError):
            ai_svc._normalize_base_url("deepseek", "https://evil.example.com")
        self.assertEqual(ai_svc._normalize_base_url("deepseek", ""), "https://api.deepseek.com")
        with self.assertRaises(ValueError):
            ai_svc._normalize_base_url("openai_compatible", "http://127.0.0.1:11434")
        with self.assertRaises(ValueError):
            ai_svc._normalize_base_url("openai_compatible", "https://api.example.com")

    def test_regex_validation(self):
        with self.assertRaises(ValueError):
            fiveflow_svc._validate_pattern(r"(a+)+$")
        with self.assertRaises(ValueError):
            fiveflow_svc._validate_pattern("[")
        with self.assertRaises(ValueError):
            fiveflow_svc._validate_pattern("x" * 201)
        fiveflow_svc._validate_pattern(r"1[3-9]\d{9}")

    def test_audit_chain_concurrent(self):
        import threading

        audit.reset_cache()
        user = {"id": "U002", "badge_no": "100101", "org_id": "O003"}
        errors: list[Exception] = []

        def worker(index: int):
            try:
                for seq in range(8):
                    audit.write(user, "test.concurrent", "record", f"R{index}-{seq}",
                                {"i": index, "j": seq})
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])
        result = audit.verify_chain(full=True)
        self.assertTrue(result["ok"], result)

    def test_login_failure_lockout(self):
        from modules import auth_api

        key = ("unit-lock", "10.0.0.1")
        auth_api._clear_failures(key)
        try:
            for _ in range(4):
                auth_api._record_failure(key)
                self.assertEqual(auth_api._lock_remaining(key), 0, "未达阈值不应锁定，也不能丢计数")
            auth_api._record_failure(key)
            self.assertGreater(auth_api._lock_remaining(key), 0, "达到阈值后应锁定")
        finally:
            auth_api._clear_failures(key)

    def test_client_ip_trusted_proxy(self):
        from app import resolve_client_ip
        import config as app_config

        original = app_config.TRUSTED_PROXIES
        try:
            app_config.TRUSTED_PROXIES = set()
            self.assertEqual(resolve_client_ip("10.0.0.5", {"x-forwarded-for": "1.2.3.4"}), "10.0.0.5")
            app_config.TRUSTED_PROXIES = {"10.0.0.5"}
            self.assertEqual(resolve_client_ip("10.0.0.5", {"x-forwarded-for": "1.2.3.4, 8.8.8.8"}), "8.8.8.8")
        finally:
            app_config.TRUSTED_PROXIES = original


class TestSeedData(unittest.TestCase):
    def test_main_record_has_key_gap(self):
        record = db.one("SELECT * FROM record WHERE id = 'RC202611160001'")
        items = db.rows("SELECT * FROM qa_item WHERE record_id = ? AND is_deleted = 0", (record["id"],))
        state = fiveflow_svc.analyze(items, db.json_load(record["flow_excluded"], []))
        fund = next(f for f in state["flows"] if f["key"] == "fund")
        self.assertEqual(fund["status"], "key_gap")
        self.assertIn("对方卡号", fund["gap_labels"])
        delivery = next(f for f in state["flows"] if f["key"] == "delivery")
        self.assertEqual(delivery["status"], "not_involved")
        self.assertGreater(state["completeness"], 30)
        self.assertEqual(len(items), len([i for i in items if True]))

    def test_completed_record_is_complete(self):
        record = db.one("SELECT * FROM record WHERE id = 'RC202611160002'")
        items = db.rows("SELECT * FROM qa_item WHERE record_id = ? AND is_deleted = 0", (record["id"],))
        state = fiveflow_svc.analyze(items, db.json_load(record["flow_excluded"], []))
        self.assertEqual(state["key_gaps"], [], f"完成态笔录仍有缺口: {state['key_gaps']}")
        self.assertEqual(record["stage"], "completed")

    def test_audit_chain(self):
        result = audit.verify_chain()
        self.assertTrue(result["ok"], f"审计哈希链断裂于 {result['broken_at']}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
