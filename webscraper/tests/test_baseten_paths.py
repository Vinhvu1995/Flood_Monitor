"""Offline tests for Baseten-first paths (mocked HTTP, no keys, no network)."""
import json, os, sys, unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
os.environ["USE_META_LLM"] = "1"
os.environ["BASETEN_API_KEY"] = "test-key-not-real"

import meta_llm
from meta_llm import _parse_chat, extract, enabled
import meta_cost


def _chat(content=None, reasoning=None, usage=None):
    msg = {}
    if content is not None:
        msg["content"] = content
    if reasoning is not None:
        msg["reasoning_content"] = reasoning
    return {"choices": [{"message": msg}],
            "usage": usage or {"prompt_tokens": 100, "completion_tokens": 20}}


GOOD = json.dumps({"flood_event": True,
                   "locations": [{"name": "York", "date": "2026-10-24",
                                  "context": "flooding in York"}]})


class TestBasetenParsing(unittest.TestCase):
    def test_reads_content(self):
        loc, usage = _parse_chat(_chat(content=GOOD))
        self.assertTrue(loc["flood_event"])
        self.assertEqual(usage["prompt_tokens"], 100)

    def test_reads_reasoning_content_when_content_null(self):
        loc, usage = _parse_chat(_chat(content=None, reasoning="thinking..." + GOOD))
        self.assertTrue(loc is not None and loc["flood_event"])

    def test_strips_code_fences(self):
        loc, _ = _parse_chat(_chat(content="```json\n" + GOOD + "\n```"))
        self.assertTrue(loc["flood_event"])

    def test_rejects_non_flood_json(self):
        loc, _ = _parse_chat(_chat(content='{"hello": 1}'))
        self.assertIsNone(loc)

    def test_extract_uses_baseten_first(self):
        calls = []
        real = meta_llm._post_json
        meta_llm._post_json = lambda url, body, key, timeout=90: (
            calls.append((url, body["model"])), _chat(content=GOOD))[1]
        try:
            out = extract("flooding in York", "UK", "2026-10-24", "2026-10-26")
        finally:
            meta_llm._post_json = real
        self.assertTrue(out["flood_event"])
        self.assertIn("muse-spark-open-1026", out["meta_model"])
        self.assertIn("inference.baseten.co", calls[0][0])
        self.assertEqual(out["meta_tokens"], {"in": 100, "out": 20})

    def test_extract_falls_back_when_baseten_down(self):
        os.environ["META_API_KEY"] = "test-key-not-real"
        real = meta_llm._post_json
        def boom(url, body, key, timeout=90):
            if "baseten" in url:
                raise ConnectionError("down")
            return _chat(content=GOOD)
        meta_llm._post_json = boom
        try:
            out = extract("flooding in York", "UK", "2026-10-24", "2026-10-26")
        finally:
            meta_llm._post_json = real
            os.environ.pop("META_API_KEY", None)
        self.assertTrue(out["flood_event"])
        self.assertTrue(out["meta_model"].startswith("model-api:"))

    def test_extract_none_when_no_keys(self):
        os.environ.pop("BASETEN_API_KEY", None)
        os.environ.pop("META_API_KEY", None)
        try:
            self.assertFalse(enabled())
            self.assertIsNone(extract("flooding", "UK", "2026-10-24", "2026-10-26"))
        finally:
            os.environ["BASETEN_API_KEY"] = "test-key-not-real"

    def test_exact_model_constant(self):
        self.assertEqual(meta_llm.BASETEN_MODEL, "meta-models/muse-spark-open-1026")


class TestSpendCap(unittest.TestCase):
    def test_cap_blocks_meta_path(self):
        import tempfile
        tmp = tempfile.mkdtemp() + "/c.jsonl"
        with open(tmp, "w") as f:
            f.write('{"cost": 150.0}\n')
        old = meta_cost.COST_LOG
        meta_cost.COST_LOG = tmp
        os.environ["META_API_KEY"] = "k"
        try:
            self.assertFalse(meta_cost.meta_allowed())
            self.assertIsNone(extract("flooding", "UK", "2026-10-24", "2026-10-26"))
        finally:
            meta_cost.COST_LOG = old
            os.environ.pop("META_API_KEY", None)

    def test_vision_notes_cap_fallback(self):
        import sys as _sys
        _sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                        "..", "..", "traffic-cameras"))
        import tempfile
        tmp = tempfile.mkdtemp() + "/c.jsonl"
        with open(tmp, "w") as f:
            f.write('{"cost": 999.0}\n')
        import meta_cost as _mc
        old = _mc.COST_LOG
        _mc.COST_LOG = tmp
        os.environ["USE_META_VISION"] = "1"
        os.environ["BASETEN_API_KEY"] = "k"
        try:
            import vision as _v
            out = _v.classify_flood(b"\xff" * 30000)
        finally:
            _mc.COST_LOG = old
            os.environ.pop("USE_META_VISION", None)
            os.environ.pop("BASETEN_API_KEY", None)
        self.assertFalse(out["flooded"])
        self.assertIn("cap reached", out["evidence"])


class TestMetaCost(unittest.TestCase):
    def test_record_and_spent(self):
        import tempfile
        tmp = tempfile.mkdtemp() + "/c.jsonl"
        old = meta_cost.COST_LOG
        meta_cost.COST_LOG = tmp
        try:
            e = meta_cost.record("baseten:muse-spark-open-1026",
                                 {"in": 1000000, "out": 100000}, run_id="t")
        finally:
            meta_cost.COST_LOG = old
        self.assertGreater(e["cost"], 0)
        self.assertEqual(meta_cost.spent_since(tmp), e["cost"])
        self.assertEqual(meta_cost.spent_since(tmp + ".missing"), 0.0)

    def test_cost_math(self):
        c = meta_cost.cost_usd("baseten:x", 1_000_000, 0)
        self.assertAlmostEqual(c, 1.25)


if __name__ == "__main__":
    unittest.main()
