#!/usr/bin/env python3
"""Tests for the shared CAD2LLM vocabulary and metadata-first semantics."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import cad2llm_core as core  # noqa: E402
import cad2llm_v5 as v5  # noqa: E402
import cad2llm_vocab as vocab  # noqa: E402


def node(name, role, zone, ifc, tag="walls"):
    return {
        "uid": f"1@{name}",
        "name": name,
        "tag": tag,
        "metadata": {
            "cad2llm": {"role": role, "zone": zone},
            "ifc": {"type": ifc},
        },
    }


class VocabTests(unittest.TestCase):
    def setUp(self):
        self.v = vocab.load_vocab()

    def test_vocab_loaded_from_rules_file(self):
        self.assertIn("wall", vocab.roles(self.v))
        self.assertIn("space_boundary", vocab.roles(self.v))
        self.assertIn("storage", vocab.roles(self.v))
        self.assertIn("envelope", vocab.zones(self.v))
        self.assertEqual(vocab.tag_role("walls", self.v), "wall")
        self.assertEqual(vocab.tag_role("energy_nodes", self.v), "battery")

    def test_ifc_mapping_is_2x3_safe(self):
        mapped = {info.get("ifc") for info in vocab.roles(self.v).values()}
        self.assertNotIn("IfcTransformer", mapped)
        self.assertNotIn("IfcSpaceHeater", mapped)
        for ifc in mapped:
            self.assertTrue(ifc and ifc.startswith("Ifc"))

    def test_check_node_unknown_role(self):
        issues = vocab.check_node(node("blob", "blob", "envelope", "IfcWall"), self.v)
        codes = [i["code"] for i in issues]
        self.assertIn("UNKNOWN_ROLE", codes)

    def test_check_node_clean(self):
        issues = vocab.check_node(
            node("wall_west", "wall", "envelope", "IfcWall"), self.v
        )
        self.assertEqual(issues, [])

    def test_check_node_tag_role_mismatch(self):
        issues = vocab.check_node(
            node("wall_west", "insulation", "envelope", "IfcCovering", tag="walls"),
            self.v,
        )
        codes = [i["code"] for i in issues]
        self.assertIn("TAG_ROLE_MISMATCH", codes)

    def test_unknown_zone(self):
        issues = vocab.check_node(node("x", "wall", "attic", "IfcWall"), self.v)
        self.assertIn("UNKNOWN_ZONE", [i["code"] for i in issues])


class ValidateVocabTests(unittest.TestCase):
    def test_validate_reports_vocab_issues(self):
        doc = {"nodes": [node("blob", "blob", "envelope", "IfcWall")]}
        report = core.validate(doc)
        codes = [i["code"] for i in report["issues"]]
        self.assertIn("UNKNOWN_ROLE", codes)


class ClassifyMetadataTests(unittest.TestCase):
    def test_metadata_role_wins_over_name(self):
        semantics = v5.classify(
            "wall_west", None, {"cad2llm": {"role": "insulation", "zone": "envelope"}}
        )
        self.assertEqual(semantics["role"], "insulation")
        self.assertEqual(semantics["zone"], "envelope")
        self.assertEqual(semantics["confidence"], 0.95)

    def test_opening_role_known(self):
        semantics = v5.classify("opening_entry_east")
        self.assertEqual(semantics["role"], "opening")
        self.assertEqual(semantics["zone"], "entry")

    def test_unknown_without_metadata_stays_unknown(self):
        semantics = v5.classify("blob_xyz")
        self.assertEqual(semantics["role"], "unknown")


if __name__ == "__main__":
    unittest.main()
