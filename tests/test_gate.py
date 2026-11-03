#!/usr/bin/env python3
"""Tests for tools/pipeline/gate.py - the write path.

The gate used to read its role names from registry.json and write whatever it
found; the read path (v5, validate) knew a different set. These tests pin the
conformance: everything the gate proposes must exist in tools/cad2llm_rules.json.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tools" / "pipeline"))

import cad2llm_vocab as vocab  # noqa: E402
import gate  # noqa: E402


def node(**overrides):
    data = {
        "uid": "ID1",
        "persistent_id": 101,
        "name": "wall_inner_south",
        "definition_name": "wall_inner",
        "tag": "Walls",
        "spatial": {"size_mm": {"x": 4500.0, "y": 24.0, "z": 1990.0}},
        "metadata": {},
    }
    data.update(overrides)
    return data


def with_role(role, **overrides):
    return node(metadata={"cad2llm": {"role": role}}, **overrides)


class GateVocabularyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.reg = gate.Registry(str(gate.DEFAULT_REGISTRY))
        cls.vocab = vocab.load_vocab()

    def codes(self, issues):
        return [c for c, _, _ in issues]

    def test_registry_roles_and_systems_are_declared(self):
        roles, systems = set(), set()
        for section in (self.reg.definitions, self.reg.tag_map, self.reg.meta_types):
            for rec in section.values():
                if rec.get("role"):
                    roles.add(rec["role"])
                if rec.get("system"):
                    systems.add(rec["system"])
        roles.update(self.reg.reference_roles)
        self.assertEqual(sorted(roles - self.reg.vocab_roles), [])
        self.assertEqual(sorted(systems - self.reg.vocab_systems), [])

    def test_zone_is_derived_from_role(self):
        proposal, issues = gate.classify_semantics(self.reg, with_role("column"))
        self.assertEqual(proposal["zone"], "envelope")
        self.assertNotIn("SEM_ZONE_MISSING", self.codes(issues))

    def test_explicit_zone_wins(self):
        proposal, _ = gate.classify_semantics(
            self.reg,
            node(metadata={"cad2llm": {"role": "column", "zone": "work"}}),
        )
        self.assertEqual(proposal["zone"], "work")

    def test_role_outside_vocabulary_is_blocking(self):
        proposal, issues = gate.classify_semantics(self.reg, with_role("nonsense"))
        self.assertIn("SEM_VOCAB_ROLE_DRIFT", self.codes(issues))
        blocking = [c for c, sev, _ in issues if sev == gate.SEV_BLOCK]
        self.assertIn("SEM_VOCAB_ROLE_DRIFT", blocking)

    def test_system_outside_vocabulary_is_blocking(self):
        proposal, issues = gate.classify_semantics(
            self.reg, node(metadata={"cad2llm": {"role": "wall", "system": "hvac"}})
        )
        self.assertIn("SEM_VOCAB_SYSTEM_DRIFT", self.codes(issues))

    def test_zone_outside_vocabulary_is_blocking(self):
        _, issues = gate.classify_semantics(
            self.reg, node(metadata={"cad2llm": {"role": "wall", "zone": "attic"}})
        )
        self.assertIn("SEM_VOCAB_ZONE_DRIFT", self.codes(issues))

    def test_malformed_ifc_is_blocking(self):
        _, issues = gate.classify_semantics(
            self.reg,
            node(metadata={"cad2llm": {"role": "wall"}, "ifc": {"type": "Wall"}}),
        )
        self.assertIn("SEM_IFC_MALFORMED", self.codes(issues))

    def test_undeclared_but_valid_ifc_is_info(self):
        _, issues = gate.classify_semantics(
            self.reg,
            node(
                metadata={
                    "cad2llm": {"role": "wall"},
                    "ifc": {"type": "IfcTransformer"},
                }
            ),
        )
        sev = {c: s for c, s, _ in issues}
        self.assertEqual(sev.get("SEM_IFC_UNDECLARED"), gate.SEV_INFO)
        self.assertNotIn("SEM_IFC_MALFORMED", sev)


class GatePatchTests(unittest.TestCase):
    def setUp(self):
        self.reg = gate.Registry(str(gate.DEFAULT_REGISTRY))

    def result(self, node_data, proposal):
        return {
            "model": "x.json",
            "model_name": "x",
            "input_sha256": "0" * 16,
            "registry_version": self.reg.version,
            "proposals": [proposal],
            "_nodes": [node_data],
        }

    def test_patch_carries_zone(self):
        data = with_role("column")
        proposal, _ = gate.classify_semantics(self.reg, data)
        patch, pending = gate.build_patch(self.result(data, proposal))
        fields = {c["field"]: c["value"] for c in patch["commands"]}
        self.assertEqual(fields.get("cad2llm.zone"), "envelope")
        self.assertEqual(pending, [])

    def test_patch_skips_unknown_zone(self):
        data = with_role("junk_component")
        proposal, _ = gate.classify_semantics(self.reg, data)
        patch, _ = gate.build_patch(self.result(data, proposal))
        fields = {c["field"] for c in patch["commands"]}
        self.assertNotIn("cad2llm.zone", fields)

    def test_missing_persistent_id_is_not_a_duplicate(self):
        issues = []
        gate.check_duplicates(
            [{"persistent_id": None}, {"persistent_id": None}], issues
        )
        self.assertEqual(issues, [])

    def test_real_duplicate_is_blocking(self):
        issues = []
        gate.check_duplicates([{"persistent_id": 7}, {"persistent_id": 7}], issues)
        self.assertEqual([c for c, _, _, _ in issues], ["DATA_DUPLICATE_PID"])
        self.assertEqual(issues[0][2], "pid:7")


class GateFrameContractTests(unittest.TestCase):
    """B2 contract: matrix_4x4 is local->world, bbox_mm/geometry are world.

    Two consequences get pinned here: a rotated node whose mesh already matches
    the stored box is a contract statement (INFO), not a defect, and a
    container's bbox is the union of children while geometry covers direct
    entities only - those two scopes must never be compared.
    """

    @classmethod
    def setUpClass(cls):
        cls.reg = gate.Registry(str(gate.DEFAULT_REGISTRY))

    @staticmethod
    def frame(mesh=(100.0, 50.0, 10.0), **overrides):
        def xyz(v):
            return {"x": v[0], "y": v[1], "z": v[2]}

        size = [100.0, 50.0, 10.0]
        data = {
            "uid": "ID1",
            "parent_uid": None,
            "persistent_id": 1,
            "name": "rotated_beam",
            "definition_name": "beam",
            "metadata": {},
            "geometry": {"scope": "direct", "vertices": [[0.0, 0.0, 0.0], list(mesh)]},
            "spatial": {
                "size_mm": xyz(size),
                "center_mm": xyz([50.0, 25.0, 5.0]),
                "bbox_mm": {"min": xyz([0.0, 0.0, 0.0]), "max": xyz(size)},
                "rotation_deg": {"x": 0.0, "y": 0.0, "z": 90.0},
                "matrix_4x4": [
                    [0.0, -1.0, 0.0, 0.0],
                    [1.0, 0.0, 0.0, 0.0],
                    [0.0, 0.0, 1.0, 0.0],
                    [0.0, 0.0, 0.0, 1.0],
                ],
            },
        }
        data.update(overrides)
        return data

    @staticmethod
    def severities(issues):
        return {code: sev for code, sev, _ in issues}

    def test_rotation_vs_geometry_is_informational(self):
        issues = gate.analyze_frame(self.reg, self.frame())
        sev = self.severities(issues)
        self.assertEqual(sev.get("FRAME_ROTATION_VS_GEOMETRY"), gate.SEV_INFO)
        self.assertEqual([c for c, s, _ in issues if s == gate.SEV_BLOCK], [])

    def test_unworldly_stored_bbox_is_still_blocking(self):
        # mesh = aabb(rot, size) -> the stored box is the unrotated one
        issues = gate.analyze_frame(self.reg, self.frame(mesh=(50.0, 100.0, 10.0)))
        sev = self.severities(issues)
        self.assertEqual(sev.get("FRAME_BBOX_NOT_AABB"), gate.SEV_BLOCK)

    def test_mesh_vs_bbox_blocks_for_a_plain_node(self):
        issues = gate.analyze_frame(self.reg, self.frame(mesh=(125.0, 50.0, 10.0)))
        sev = self.severities(issues)
        self.assertEqual(sev.get("FRAME_MESH_VS_BBOX"), gate.SEV_BLOCK)

    def test_container_never_compares_mesh_with_union_bbox(self):
        issues = gate.analyze_frame(
            self.reg, self.frame(mesh=(125.0, 50.0, 10.0)), has_children=True
        )
        sev = self.severities(issues)
        self.assertNotIn("FRAME_MESH_VS_BBOX", sev)
        self.assertNotIn("FRAME_BBOX_NOT_AABB", sev)
        self.assertEqual(sev.get("FRAME_UNDECIDABLE"), gate.SEV_WARN)
        self.assertEqual([c for c, s, _ in issues if s == gate.SEV_BLOCK], [])

    def test_run_gate_marks_containers_from_parent_uid(self):
        parent = self.frame(uid="P1", mesh=(125.0, 50.0, 10.0))
        child = self.frame(uid="C1", parent_uid="P1", mesh=(125.0, 50.0, 10.0))
        data = {"export_profile": "geometry", "nodes": [parent, child]}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "model.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            res = gate.run_gate(str(path), self.reg)
        found = [(i["code"], i["entity"]) for i in res["issues"]]
        # P1 is a container (child points at it): union bbox, no mesh verdict
        self.assertIn(("FRAME_UNDECIDABLE", "P1"), found)
        self.assertNotIn(("FRAME_MESH_VS_BBOX", "P1"), found)
        # C1 is a leaf: its own bbox is comparable with its own geometry
        self.assertIn(("FRAME_MESH_VS_BBOX", "C1"), found)


class GateSelftestTests(unittest.TestCase):
    def test_selftest_passes(self):
        self.assertEqual(gate.selftest(), 0)


if __name__ == "__main__":
    unittest.main()
