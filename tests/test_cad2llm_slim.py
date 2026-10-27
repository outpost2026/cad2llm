#!/usr/bin/env python3
"""Tests for cad2llm_slim (LLM handoff tier)."""

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import cad2llm_slim as slim  # noqa: E402

V5_EXAMPLE = ROOT / "examples" / "mistnost_2_v7_v5_gui.json"


class TestSlimHandoff(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = json.loads(V5_EXAMPLE.read_text(encoding="utf-8"))
        cls.doc = slim.slim_document(cls.raw)
        cls.raw_bytes = len(V5_EXAMPLE.read_bytes())
        cls.slim_bytes = len(slim.serialize(cls.doc).encode("utf-8"))

    def test_export_tier(self):
        self.assertEqual(self.doc["export_tier"], slim.EXPORT_TIER)

    def test_smaller_than_full_v5(self):
        self.assertLess(self.slim_bytes, self.raw_bytes * 0.75)

    def test_no_z_table(self):
        self.assertNotIn("z_table", self.doc.get("metrics") or {})

    def test_definitions_deduplicate_descriptions(self):
        defs = self.doc["definitions"]
        self.assertGreater(len(defs), 0)
        names = [d["name"] for d in defs]
        self.assertEqual(len(names), len(set(names)))

    def test_nodes_drop_matrix_and_hierarchy(self):
        for node in self.doc["nodes"]:
            sp = node.get("spatial") or {}
            self.assertNotIn("matrix_4x4", sp)
            self.assertNotIn("size_mm", sp)
            self.assertNotIn("center_mm", sp)
            self.assertNotIn("hierarchy_path", node)
            self.assertNotIn("definition_description", node)

    def test_relations_have_no_id_or_evidence(self):
        for rel in self.doc.get("relations") or []:
            self.assertNotIn("id", rel)
            self.assertNotIn("evidence", rel)
            self.assertIn("kind", rel)
            self.assertIn("from", rel)
            self.assertIn("to", rel)

    def test_semantics_evidence_rules(self):
        sem = self.doc["nodes"][0].get("semantics")
        if sem and "evidence_rules" in sem:
            self.assertIsInstance(sem["evidence_rules"], list)

    def test_deterministic(self):
        again = slim.slim_document(self.raw)
        self.assertEqual(
            slim.serialize(again, compact=True),
            slim.serialize(self.doc, compact=True),
        )

    def test_geometry_summary_mode(self):
        geo_doc = json.loads(
            (ROOT / "examples" / "mistnost_2_v7_v4_gui.json").read_text(encoding="utf-8")
        )
        geo_doc["nodes"][0]["geometry"] = {
            "status": "ok",
            "counts": {"vertices": 8, "faces": 6},
            "hash": "abc123",
            "volume_mm3": 1.0,
            "vertices": [[0, 0, 0]],
        }
        out = slim.slim_document(geo_doc, geometry_mode="summary")
        g = out["nodes"][0]["geometry"]
        self.assertNotIn("vertices", g)
        self.assertEqual(g["hash"], "abc123")


if __name__ == "__main__":
    unittest.main()
