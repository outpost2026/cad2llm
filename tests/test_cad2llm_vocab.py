#!/usr/bin/env python3
"""Tests for the shared CAD2LLM vocabulary and metadata-first semantics."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import cad2llm_core as core  # noqa: E402
import cad2llm_v5 as v5  # noqa: E402
import cad2llm_vocab as vocab  # noqa: E402


def overlay(**overrides):
    data = {
        "schema": vocab.OVERLAY_SCHEMA,
        "project_id": "loft_demo",
        "name": "Loft demo",
        "version": "1.0+loft",
        "extends_version": "1.0",
        "roles": {
            "loft_ladder": {
                "system": "furniture",
                "zone": "sleep",
                "ifc": "IfcStair",
                "label_cs": "žebřík",
            },
            "guardrail": {
                "system": "envelope",
                "zone": "envelope",
                "ifc": "IfcRailing",
                "label_cs": "zábradlí",
            },
        },
        "tag_roles": {"zebrik": "loft_ladder", "madlo": "guardrail"},
        "zones": ["sleep"],
        "deprecated_roles": ["marker"],
    }
    data.update(overrides)
    return data


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

    def test_legacy_tag_aliases(self):
        self.assertEqual(vocab.tag_role("zaklady", self.v), "floor")
        self.assertEqual(vocab.tag_role("foundation", self.v), "floor")
        self.assertEqual(vocab.tag_role("ceilling", self.v), "ceiling")
        self.assertEqual(vocab.tag_role("Walls", self.v), "wall")

    def test_roof_and_chimney_roles(self):
        for role in ("roof", "chimney"):
            self.assertIn(role, vocab.roles(self.v))
        self.assertEqual(vocab.role_info("roof", self.v)["ifc"], "IfcRoof")
        self.assertEqual(vocab.tag_role("chimney", self.v), "chimney")
        self.assertEqual(vocab.tag_role("střecha", self.v), "roof")
        self.assertEqual(vocab.tag_role("komin", self.v), "chimney")

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


class OverlayTests(unittest.TestCase):
    def test_overlay_adds_roles_aliases_and_zones(self):
        merged = vocab.load_vocab(overlay=overlay())
        self.assertEqual(merged["project_id"], "loft_demo")
        self.assertEqual(merged["version"], "1.0+loft")
        self.assertEqual(merged["base_version"], "1.0")
        self.assertEqual(vocab.role_info("loft_ladder", merged)["ifc"], "IfcStair")
        self.assertEqual(vocab.tag_role("ZEBRIK", merged), "loft_ladder")
        self.assertEqual(vocab.tag_role("Madlo", merged), "guardrail")

    def test_core_vocab_is_not_mutated(self):
        core_vocab = vocab.load_vocab()
        role_count = len(vocab.roles(core_vocab))
        vocab.load_vocab(overlay=overlay())
        self.assertEqual(len(vocab.roles(vocab.load_vocab())), role_count)
        self.assertNotIn("loft_ladder", vocab.roles(core_vocab))

    def test_hash_is_stable_and_overlay_sensitive(self):
        base = vocab.load_vocab()
        merged = vocab.load_vocab(overlay=overlay())
        self.assertEqual(vocab.vocab_hash(base), vocab.vocab_hash(base))
        self.assertNotEqual(vocab.vocab_hash(base), vocab.vocab_hash(merged))
        changed = overlay(tag_roles={"zebrik": "guardrail"})
        self.assertNotEqual(
            vocab.vocab_hash(merged),
            vocab.vocab_hash(vocab.load_vocab(overlay=changed)),
        )

    def test_role_redefinition_is_refused(self):
        data = overlay()
        data["roles"] = {
            "wall": {"system": "envelope", "zone": "envelope", "ifc": "IfcWall"}
        }
        with self.assertRaisesRegex(ValueError, "already exists"):
            vocab.load_vocab(overlay=data)

    def test_alias_redefinition_is_refused(self):
        with self.assertRaisesRegex(ValueError, "already maps"):
            vocab.load_vocab(overlay=overlay(tag_roles={"walls": "floor"}))

    def test_unknown_alias_role_is_refused(self):
        with self.assertRaisesRegex(ValueError, "unknown role"):
            vocab.load_vocab(overlay=overlay(tag_roles={"zebrik": "nope"}))

    def test_wrong_base_version_is_refused(self):
        with self.assertRaisesRegex(ValueError, "extends"):
            vocab.load_vocab(overlay=overlay(extends_version="0.9"))

    def test_unknown_overlay_key_is_refused(self):
        with self.assertRaisesRegex(ValueError, "unknown keys"):
            vocab.load_vocab(overlay=overlay(typo="x"))

    def test_deprecated_role_warns_but_stays_known(self):
        merged = vocab.load_vocab(overlay=overlay())
        issues = vocab.check_node(
            node("helper", "marker", "unknown", "IfcAnnotation"), merged
        )
        codes = [i["code"] for i in issues]
        self.assertIn("DEPRECATED_ROLE", codes)
        self.assertNotIn("UNKNOWN_ROLE", codes)

    def test_validate_accepts_overlay_role(self):
        merged = vocab.load_vocab(overlay=overlay())
        doc = {"nodes": [node("ladder", "loft_ladder", "sleep", "IfcStair")]}
        report = core.validate(doc, vocab_data=merged)
        codes = [i["code"] for i in report["issues"]]
        self.assertNotIn("UNKNOWN_ROLE", codes)
        self.assertNotIn("UNKNOWN_ZONE", codes)

    def test_cli_validate_with_overlay(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            src = tmp_path / "doc.json"
            ov = tmp_path / "loft.vocab.json"
            src.write_text(
                json.dumps(
                    {"nodes": [node("ladder", "loft_ladder", "sleep", "IfcStair")]}
                ),
                encoding="utf-8",
            )
            ov.write_text(json.dumps(overlay()), encoding="utf-8")
            proc = subprocess.run(
                [
                    sys.executable,
                    "-X",
                    "utf8",
                    str(ROOT / "tools" / "cad2llm.py"),
                    "validate",
                    str(src),
                    "--vocab",
                    str(ov),
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertNotIn("UNKNOWN_ROLE", proc.stdout)


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
