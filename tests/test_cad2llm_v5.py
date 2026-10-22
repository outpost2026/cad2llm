#!/usr/bin/env python3
"""F1 tests: semantics, metrics, brief, determinism, golden master.

Run:  python -m unittest discover -s tests -v
"""

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import cad2llm_v5 as v5  # noqa: E402

EXAMPLE = ROOT / "examples" / "mistnost_2_sk24_v4.json"
GOLDEN = Path(__file__).resolve().parent / "golden" / "mistnost_2_v5.golden.json"
SCHEMA = ROOT / "docs" / "schema" / "cad2llm_v5.schema.json"

ROLES = {
    "wall",
    "window",
    "door",
    "opening",
    "floor",
    "ceiling",
    "roof",
    "insulation",
    "beam",
    "truss",
    "furniture",
    "bed",
    "table",
    "worktop",
    "stove",
    "appliance",
    "battery",
    "inverter",
    "storage",
    "circulation",
    "boundary",
    "void",
    "marker",
    "unknown",
}
ZONES = {
    "entry",
    "sleep",
    "dining",
    "work",
    "energy",
    "tech",
    "storage",
    "structure",
    "envelope",
    "outside",
    "unknown",
    None,
}
INTENTS = {"ARTIFACT", "CONSTRUCTION", "DESIGN_DECISION", "DEFECT", "UNKNOWN"}


class TestV5Semantics(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.doc = json.loads(EXAMPLE.read_text(encoding="utf-8"))
        cls.out = v5.enrich(cls.doc)
        cls.nodes = {n["uid"]: n for n in cls.out["nodes"]}

    def _sem(self, uid):
        return self.nodes[uid]["semantics"]

    def test_every_node_has_semantics(self):
        self.assertEqual(len(self.out["nodes"]), 46)
        for n in self.out["nodes"]:
            s = n["semantics"]
            self.assertIn(s["role"], ROLES)
            self.assertIn(s["zone"], ZONES)
            self.assertIn(s["intent"], INTENTS)
            self.assertGreaterEqual(s["confidence"], 0.0)
            self.assertLessEqual(s["confidence"], 1.0)

    def test_no_unknown_roles_on_real_model(self):
        unknown = [
            n["uid"] for n in self.out["nodes"] if n["semantics"]["role"] == "unknown"
        ]
        self.assertEqual(unknown, [])

    def test_known_uids(self):
        cases = {
            "ID15": ("bed", "sleep", "DESIGN_DECISION"),
            "ID27": ("stove", "sleep", "DESIGN_DECISION"),
            "ID3": ("worktop", "work", "DESIGN_DECISION"),
            "ID53": ("battery", "energy", "DESIGN_DECISION"),
            "ID123": ("wall", "envelope", "CONSTRUCTION"),
            "ID283": ("insulation", "envelope", "CONSTRUCTION"),
            "ID171": ("window", "sleep", "CONSTRUCTION"),
            "ID191": ("window", "dining", "CONSTRUCTION"),
            "ID207": ("beam", "envelope", "CONSTRUCTION"),
            "ID215": ("void", "entry", "ARTIFACT"),
            "ID327": ("boundary", "unknown", "ARTIFACT"),
        }
        for uid, expected in cases.items():
            s = self._sem(uid)
            self.assertEqual((s["role"], s["zone"], s["intent"]), expected, uid)

    def test_evidence_present_for_known(self):
        for n in self.out["nodes"]:
            s = n["semantics"]
            if s["role"] != "unknown":
                self.assertTrue(s["evidence"], n["uid"])
                self.assertEqual(s["evidence"][0]["rule_class"][:6], "CLASS_")

    def test_fail_safe_unknown(self):
        s = v5.classify("Xyzzy_42", {"size_mm": {"x": 10, "y": 10, "z": 10}})
        self.assertEqual(s["role"], "unknown")
        self.assertEqual(s["zone"], "unknown")
        self.assertEqual(s["intent"], "UNKNOWN")
        self.assertEqual(s["confidence"], 0.0)

    def test_degenerate_marker_is_artifact(self):
        s = v5.classify("Widget", {"size_mm": {"x": 10, "y": 0, "z": 10}})
        self.assertEqual(s["intent"], "ARTIFACT")
        self.assertIn("R_GEOM_ZERO_THICKNESS", [e["rule_id"] for e in s["evidence"]])


class TestV5MetricsAndBrief(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.out = v5.enrich(json.loads(EXAMPLE.read_text(encoding="utf-8")))

    def test_envelope_and_clear_height(self):
        m = self.out["metrics"]
        self.assertAlmostEqual(m["envelope_mm"]["x"], 4688.0, places=1)
        self.assertAlmostEqual(m["clear_height_mm"], 1990.0, places=1)
        self.assertAlmostEqual(m["floor_area_m2"], 9.31, places=2)

    def test_clearance_uses_uids(self):
        cl = self.out["metrics"]["clearances"]
        self.assertEqual(len(cl), 1)
        self.assertEqual({cl[0]["a"], cl[0]["b"]}, {"ID27", "ID15"})
        self.assertAlmostEqual(cl[0]["gap_mm"], 642.4, places=1)
        self.assertTrue(cl[0]["ok"])

    def test_brief_limits(self):
        b = self.out["brief"]
        self.assertEqual(b["version"], "1.0")
        self.assertLessEqual(len(b["markdown"]), 4000)
        self.assertLessEqual(len(b["top_findings"]), 5)
        self.assertEqual(b["facts"]["nodes"], 46)
        self.assertEqual(b["facts"]["channel"], "dae")

    def test_info_only_means_no_findings(self):
        self.assertEqual(self.out["brief"]["top_findings"], [])
        self.assertEqual(
            [i["severity"] for i in self.out["validation"]["issues"]], ["INFO", "INFO"]
        )
        self.assertEqual(self.out["validation"]["counts"]["suppressed"], 0)

    def test_relations_empty_in_f1(self):
        self.assertEqual(self.out["relations"], [])


class TestV5Determinism(unittest.TestCase):
    def test_enrich_twice_is_identical(self):
        doc = json.loads(EXAMPLE.read_text(encoding="utf-8"))
        a = json.dumps(v5.enrich(doc), sort_keys=True)
        b = json.dumps(v5.enrich(doc), sort_keys=True)
        self.assertEqual(a, b)

    def test_input_not_mutated(self):
        doc = json.loads(EXAMPLE.read_text(encoding="utf-8"))
        before = json.dumps(doc, sort_keys=True)
        v5.enrich(doc)
        self.assertEqual(before, json.dumps(doc, sort_keys=True))


class TestV5Golden(unittest.TestCase):
    def test_golden_master(self):
        golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
        out = v5.enrich(json.loads(EXAMPLE.read_text(encoding="utf-8")))
        semantics = {
            n["uid"]: {
                "role": n["semantics"]["role"],
                "zone": n["semantics"]["zone"],
                "intent": n["semantics"]["intent"],
                "confidence": n["semantics"]["confidence"],
            }
            for n in out["nodes"]
        }
        self.assertEqual(semantics, golden["semantics"])
        self.assertEqual(out["brief"], golden["brief"])
        self.assertEqual(
            {
                "envelope_mm": out["metrics"]["envelope_mm"],
                "floor_area_m2": out["metrics"]["floor_area_m2"],
                "clear_height_mm": out["metrics"]["clear_height_mm"],
                "clearances": out["metrics"]["clearances"],
            },
            golden["metrics"],
        )
        self.assertEqual(out["validation"]["counts"], golden["validation"]["counts"])


class TestV5Schema(unittest.TestCase):
    def test_document_matches_schema(self):
        try:
            from jsonschema import Draft202012Validator
        except ImportError:
            self.skipTest("jsonschema not installed")
        schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
        out = v5.enrich(json.loads(EXAMPLE.read_text(encoding="utf-8")))
        errors = list(Draft202012Validator(schema).iter_errors(out))
        self.assertEqual([f"{list(e.path)}: {e.message}" for e in errors], [])


if __name__ == "__main__":
    unittest.main()
