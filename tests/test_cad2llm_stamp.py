#!/usr/bin/env python3
"""Tests for tools/cad2llm_stamp.py (vocabulary stamp plan)."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import cad2llm_stamp as stamp  # noqa: E402
import cad2llm_vocab as vocab  # noqa: E402


def loft_overlay():
    return {
        "schema": vocab.OVERLAY_SCHEMA,
        "project_id": "loft_demo",
        "version": "1.0+loft",
        "extends_version": "1.0",
        "roles": {
            "loft_ladder": {
                "system": "furniture",
                "zone": "sleep",
                "ifc": "IfcStair",
                "label_cs": "žebřík",
            }
        },
        "tag_roles": {"zebrik": "loft_ladder"},
    }


def selected_vocab():
    return vocab.load_vocab(overlay=loft_overlay())


def doc(metadata=None, name="loft_demo"):
    return {
        "schema_version": "4.0",
        "project_metadata": {"name": name, "metadata": {"cad2llm": metadata or {}}},
        "nodes": [],
    }


def stamped_plan(doc_in, v):
    return stamp.build_stamp_plan(doc_in, v)


class StampTests(unittest.TestCase):
    def test_plan_has_four_model_ops(self):
        v = selected_vocab()
        result = stamped_plan(doc(), v)
        ops = result["plan"]["operations"]
        self.assertEqual(len(ops), 4)
        self.assertTrue(all(op["target"] == {"kind": "model"} for op in ops))
        self.assertTrue(all(op["dict"] == "cad2llm" for op in ops))
        keys = [op["key"] for op in ops]
        self.assertEqual(
            keys, ["vocab_hash", "vocab_base_version", "vocab_version", "project_id"]
        )
        values = {op["key"]: op["value"] for op in ops}
        self.assertEqual(values["project_id"], "loft_demo")
        self.assertEqual(values["vocab_version"], "1.0+loft")
        self.assertEqual(values["vocab_base_version"], "1.0")
        self.assertEqual(values["vocab_hash"], vocab.vocab_hash(v))

    def test_plan_is_idempotent(self):
        v = selected_vocab()
        first = stamped_plan(doc(), v)
        applied = {op["key"]: op["value"] for op in first["plan"]["operations"]}
        second = stamped_plan(doc(applied), v)
        self.assertEqual(second["plan"]["operations"], [])
        self.assertTrue(second["summary"]["already_stamped"])

    def test_project_id_override_and_name_fallback(self):
        v = selected_vocab()
        result = stamp.build_stamp_plan(doc(), v, project_id="other")
        self.assertEqual(result["summary"]["project_id"], "other")
        core = vocab.load_vocab()
        fallback = stamp.build_stamp_plan(doc(name="from_name"), core)
        self.assertEqual(fallback["summary"]["project_id"], "from_name")

    def test_missing_project_id_is_refused(self):
        v = vocab.load_vocab()
        with self.assertRaisesRegex(ValueError, "project_id"):
            stamp.build_stamp_plan(doc(name=""), v)

    def test_cli_writes_plan_then_skips_when_stamped(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            src = tmp_path / "doc.json"
            overlay_path = tmp_path / "loft.vocab.json"
            out = tmp_path / "stamp.json"
            src.write_text(json.dumps(doc()), encoding="utf-8")
            overlay_path.write_text(json.dumps(loft_overlay()), encoding="utf-8")

            cmd = [
                sys.executable,
                "-X",
                "utf8",
                str(ROOT / "tools" / "cad2llm_stamp.py"),
                str(src),
                "--vocab",
                str(overlay_path),
                "--out",
                str(out),
            ]
            proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(proc.returncode, 0, proc.stderr)
            plan = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(plan["schema"], "cad2llm/annotate_plan/v1")

            applied = {op["key"]: op["value"] for op in plan["operations"]}
            src.write_text(json.dumps(doc(applied)), encoding="utf-8")
            out2 = tmp_path / "stamp2.json"
            proc2 = subprocess.run(
                cmd[:-1] + [str(out2)],
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            self.assertEqual(proc2.returncode, 0, proc2.stderr)
            self.assertIn("already stamped", proc2.stdout)
            self.assertFalse(out2.exists())


if __name__ == "__main__":
    unittest.main()
