#!/usr/bin/env python3
"""Tests for tools/cad2llm_derive.py (plan skeleton generator)."""

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import cad2llm_derive as derive  # noqa: E402


def make_doc():
    return {
        "schema_version": "4.0",
        "nodes": [
            {
                "persistent_id": 1,
                "uid": "1@battery_main",
                "name": "battery_main",
                "definition_name": "battery_main",
                "definition_description": None,
                "tag": "energy_nodes",
                "metadata": {},
            },
            {
                "persistent_id": 2,
                "uid": "2@Unnamed",
                "name": "Unnamed",
                "definition_name": "wall_def",
                "definition_description": None,
                "tag": "walls",
                "metadata": {},
            },
            {
                "persistent_id": 3,
                "uid": "3@blob_xyz",
                "name": "blob_xyz",
                "definition_name": "blob_xyz",
                "definition_description": None,
                "tag": "Layer0",
                "metadata": {},
            },
        ],
    }


def apply_plan(doc, plan):
    by_pid = {n["persistent_id"]: n for n in doc["nodes"]}
    for op in plan["operations"]:
        target = op["target"]
        if "persistent_id" in target:
            node = by_pid[target["persistent_id"]]
        else:
            name = target.get("name")
            node = next(
                (n for n in doc["nodes"] if n.get("definition_name") == name), None
            )
            if node is None:
                continue
        if op["op"] == "set_attribute":
            node.setdefault("metadata", {}).setdefault(op["dict"], {})[op["key"]] = op[
                "value"
            ]
        elif op["op"] == "set_tag":
            node["tag"] = op["tag"]
        elif op["op"] == "rename":
            node["name"] = op["name"]
        elif op["op"] == "set_description":
            for candidate in doc["nodes"]:
                if candidate.get("definition_name") == target.get("name"):
                    candidate["definition_description"] = op["description"]
    return doc


class DeriveTests(unittest.TestCase):
    def test_role_from_tag_and_name(self):
        result = derive.derive_plan(make_doc())
        roles = {}
        for op in result["plan"]["operations"]:
            if (
                op["op"] == "set_attribute"
                and op["dict"] == "cad2llm"
                and op["key"] == "role"
            ):
                roles[op["target"]["persistent_id"]] = op["value"]
        self.assertEqual(roles[1], "battery")
        self.assertEqual(roles[2], "wall")
        self.assertEqual(result["summary"]["unresolved"], 1)
        self.assertEqual(result["unresolved"][0]["pid"], 3)

    def test_ifc_and_system_mapping(self):
        result = derive.derive_plan(make_doc())
        ops = result["plan"]["operations"]
        ifc = {
            (op["target"]["persistent_id"], op["key"]): op["value"]
            for op in ops
            if op["op"] == "set_attribute" and op["dict"] == "ifc"
        }
        self.assertEqual(ifc[(1, "type")], "IfcElectricFlowStorageDevice")
        self.assertEqual(ifc[(2, "type")], "IfcWall")
        self.assertEqual(ifc[(1, "schema")], "IFC 2x3")

    def test_tag_op_only_when_tag_differs(self):
        result = derive.derive_plan(make_doc())
        tag_ops = [op for op in result["plan"]["operations"] if op["op"] == "set_tag"]
        self.assertEqual(tag_ops, [])

    def test_names_flag_renames_generic(self):
        result = derive.derive_plan(make_doc(), names=True)
        renames = {
            op["target"]["persistent_id"]: op["name"]
            for op in result["plan"]["operations"]
            if op["op"] == "rename"
        }
        self.assertEqual(renames, {2: "wall_02"})

    def test_descriptions_template_and_dedupe(self):
        doc = make_doc()
        doc["nodes"].append(
            {
                "persistent_id": 4,
                "uid": "4@wall_second",
                "name": "wall_second",
                "definition_name": "wall_def",
                "definition_description": None,
                "tag": "walls",
                "metadata": {},
            }
        )
        result = derive.derive_plan(doc, descriptions=True)
        desc_ops = [
            op for op in result["plan"]["operations"] if op["op"] == "set_description"
        ]
        self.assertEqual(len(desc_ops), 2)
        wall_desc = next(
            op for op in desc_ops if op["target"].get("name") == "wall_def"
        )
        self.assertIn("zóna envelope", wall_desc["description"])
        self.assertIn("IfcWall", wall_desc["description"])

    def test_existing_description_not_clobbered(self):
        doc = make_doc()
        doc["nodes"][1]["definition_description"] = "Rucni popis"
        result = derive.derive_plan(doc, descriptions=True)
        desc_ops = [
            op for op in result["plan"]["operations"] if op["op"] == "set_description"
        ]
        self.assertTrue(all("Rucni popis" not in op["description"] for op in desc_ops))
        self.assertFalse(any(op["target"].get("name") == "wall_def" for op in desc_ops))

    def test_idempotent_after_apply(self):
        doc = make_doc()
        first = derive.derive_plan(doc, names=True, descriptions=True)
        applied = apply_plan(copy.deepcopy(doc), first["plan"])
        second = derive.derive_plan(applied, names=True, descriptions=True)
        self.assertEqual(second["summary"]["operations"], 0)

    def test_no_geometry_fields(self):
        result = derive.derive_plan(make_doc(), names=True, descriptions=True)
        blob = json.dumps(result["plan"])
        for forbidden in ("bbox", "spatial", "matrix", "size_mm"):
            self.assertNotIn(forbidden, blob)

    def test_cli_writes_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            src = tmp_path / "doc.json"
            out = tmp_path / "plan.json"
            src.write_text(json.dumps(make_doc()), encoding="utf-8")
            proc = subprocess.run(
                [
                    sys.executable,
                    "-X",
                    "utf8",
                    str(ROOT / "tools" / "cad2llm_derive.py"),
                    str(src),
                    "--out",
                    str(out),
                    "--json",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            plan = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(plan["schema"], "cad2llm/annotate_plan/v1")
            self.assertGreater(len(plan["operations"]), 0)


if __name__ == "__main__":
    unittest.main()
