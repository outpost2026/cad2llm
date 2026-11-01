#!/usr/bin/env python3
"""Tests for tools/pipeline/gate.py - the write path.

The gate used to read its role names from registry.json and write whatever it
found; the read path (v5, validate) knew a different set. These tests pin the
conformance: everything the gate proposes must exist in tools/cad2llm_rules.json.
"""

import sys
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


class GateSelftestTests(unittest.TestCase):
    def test_selftest_passes(self):
        self.assertEqual(gate.selftest(), 0)


if __name__ == "__main__":
    unittest.main()
