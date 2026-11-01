#!/usr/bin/env python3
"""Tests for the semantic patch -> annotate plan translation.

Run:  python -m unittest discover -s tests -v
"""

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import cad2llm_patch as patch_mod  # noqa: E402


def command(field, value, pid=1, status="auto", target=None):
    return {
        "op": "set_attribute",
        "target": target if target is not None else {"persistent_id": pid},
        "field": field,
        "value": value,
        "status": status,
        "evidence": ["definition_name=wall"],
        "confidence": 0.9,
    }


def patch(*commands):
    return {
        "contract": patch_mod.PATCH_CONTRACT,
        "model": "model.json",
        "model_name": "model",
        "source_sha256": "abc123",
        "registry_version": "1.1.0",
        "commands": list(commands),
    }


class FieldMappingTests(unittest.TestCase):
    def test_role_maps_to_cad2llm_dictionary(self):
        plan = patch_mod.patch_to_plan(patch(command("cad2llm.role", "wall")))
        op = plan["operations"][0]
        self.assertEqual(
            (op["dict"], op["key"], op["value"]), ("cad2llm", "role", "wall")
        )

    def test_ifc_type_maps_to_ifc_dictionary(self):
        plan = patch_mod.patch_to_plan(patch(command("ifc.type", "IfcWall")))
        op = plan["operations"][0]
        self.assertEqual((op["dict"], op["key"]), ("ifc", "type"))

    def test_name_becomes_rename(self):
        plan = patch_mod.patch_to_plan(patch(command("name", "wall_01")))
        self.assertEqual(plan["operations"][0]["op"], "rename")
        self.assertEqual(plan["operations"][0]["name"], "wall_01")

    def test_plan_header(self):
        plan = patch_mod.patch_to_plan(patch(command("cad2llm.role", "wall")))
        self.assertEqual(plan["schema"], "cad2llm/annotate_plan/v1")
        self.assertEqual(plan["transaction"], "single")
        self.assertIn("model", plan["name"])


class StrictnessTests(unittest.TestCase):
    def test_dead_field_is_rejected(self):
        # cad2llm.ifc_type was the old name and is never read by the exporter.
        with self.assertRaises(patch_mod.PatchError):
            patch_mod.patch_to_plan(patch(command("cad2llm.ifc_type", "IfcWall")))

    def test_derived_field_is_rejected(self):
        for field in ("cad2llm.kind", "cad2llm.variant", "exclude_from_bom"):
            with self.subTest(field=field):
                with self.assertRaises(patch_mod.PatchError):
                    patch_mod.patch_to_plan(patch(command(field, "x")))

    def test_pending_review_is_not_written(self):
        with self.assertRaises(patch_mod.PatchError):
            patch_mod.patch_to_plan(
                patch(command("cad2llm.role", "wall", status="pending_review"))
            )

    def test_target_without_persistent_id_is_rejected(self):
        with self.assertRaises(patch_mod.PatchError):
            patch_mod.patch_to_plan(
                patch(command("cad2llm.role", "wall", target={"uid": "1@x"}))
            )

    def test_wrong_contract_is_rejected(self):
        data = patch(command("cad2llm.role", "wall"))
        data["contract"] = "something/else"
        with self.assertRaises(patch_mod.PatchError):
            patch_mod.patch_to_plan(data)

    def test_missing_value_is_rejected(self):
        with self.assertRaises(patch_mod.PatchError):
            patch_mod.patch_to_plan(patch(command("cad2llm.role", "")))

    def test_su_dictionary_is_rejected_by_lint(self):
        plan = {
            "schema": patch_mod.PLAN_SCHEMA,
            "transaction": "single",
            "operations": [
                {
                    "op": "set_attribute",
                    "target": {"persistent_id": 1},
                    "dict": "SU_Wall",
                    "key": "k",
                    "value": "v",
                }
            ],
        }
        self.assertTrue(any("reserved" in p for p in patch_mod.validate_plan(plan)))


class OrderingTests(unittest.TestCase):
    def test_duplicate_field_keeps_last_value(self):
        plan = patch_mod.patch_to_plan(
            patch(
                command("cad2llm.role", "wall"),
                command("cad2llm.role", "beam"),
            )
        )
        self.assertEqual(len(plan["operations"]), 1)
        self.assertEqual(plan["operations"][0]["value"], "beam")

    def test_distinct_targets_are_kept(self):
        plan = patch_mod.patch_to_plan(
            patch(
                command("cad2llm.role", "wall", pid=1),
                command("cad2llm.role", "beam", pid=2),
            )
        )
        self.assertEqual(len(plan["operations"]), 2)

    def test_target_is_reduced_to_persistent_id(self):
        # A path snapshot goes stale as soon as the same plan renames an
        # ancestor, so only the pid travels to the bridge.
        target = {
            "persistent_id": 5,
            "hierarchy_path": ["zone", "wall_01"],
            "name": "wall_01",
            "uid": "5@wall_01",
        }
        plan = patch_mod.patch_to_plan(
            patch(command("cad2llm.role", "wall", target=target))
        )
        self.assertEqual(plan["operations"][0]["target"], {"persistent_id": 5})


class FileTests(unittest.TestCase):
    def test_empty_patch_is_skip_not_failure(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "empty.patch.json"
            path.write_text(json.dumps(patch()), encoding="utf-8")
            with self.assertRaises(patch_mod.EmptyPatch):
                patch_mod.plan_for_file(path)

    def test_emitted_plan_passes_lint(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "one.patch.json"
            path.write_text(
                json.dumps(patch(command("cad2llm.role", "wall"))), encoding="utf-8"
            )
            plan = patch_mod.plan_for_file(path)
            self.assertEqual(patch_mod.validate_plan(plan), [])


class SchemaTests(unittest.TestCase):
    def setUp(self):
        try:
            from jsonschema import Draft202012Validator  # noqa: F401
        except ImportError:
            self.skipTest("jsonschema not installed")
        from jsonschema import Draft202012Validator

        self.validator = Draft202012Validator(patch_mod.plan_schema())

    def assert_valid(self, plan):
        errors = [
            f"{list(e.path)}: {e.message}" for e in self.validator.iter_errors(plan)
        ]
        self.assertEqual(errors, [])

    def test_emitted_plan_matches_schema(self):
        plan = patch_mod.patch_to_plan(
            patch(
                command("cad2llm.role", "wall"),
                command("cad2llm.zone", "sleep"),
                command("ifc.type", "IfcWall"),
                command("name", "wall_01"),
            )
        )
        self.assert_valid(plan)

    def test_docs_plans_match_schema(self):
        plans = patch_mod.SCHEMA_PATH.parents[2] / "docs" / "plans"
        checked = 0
        for path in sorted(plans.glob("*.json")):
            doc = json.loads(path.read_text(encoding="utf-8"))
            if doc.get("schema") != patch_mod.PLAN_SCHEMA:
                continue  # vocabulary overlay example, not a plan
            with self.subTest(plan=path.name):
                self.assert_valid(doc)
            checked += 1
        self.assertGreater(checked, 0)

    def test_ops_come_from_schema(self):
        enum = patch_mod.plan_schema()["$defs"]["operation"]["properties"]["op"]["enum"]
        self.assertEqual(list(patch_mod.ops()), enum)

    def test_unknown_op_key_is_rejected_by_lint(self):
        plan = patch_mod.patch_to_plan(patch(command("cad2llm.role", "wall")))
        plan["operations"][0]["confidence"] = 0.9
        self.assertTrue(any("unknown keys" in p for p in patch_mod.validate_plan(plan)))

    def test_wrong_value_type_is_caught_by_schema(self):
        plan = patch_mod.patch_to_plan(patch(command("cad2llm.role", "wall")))
        plan["operations"][0]["value"] = {"nested": "object"}
        self.assertNotEqual(patch_mod.validate_plan(plan), [])


if __name__ == "__main__":
    unittest.main()
