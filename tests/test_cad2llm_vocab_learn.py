#!/usr/bin/env python3
"""Tests for tools/cad2llm_vocab_learn.py - the self-growing vocabulary."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tools" / "pipeline"))

import cad2llm_vocab_learn as learn  # noqa: E402
import gate  # noqa: E402


def node(**overrides):
    data = {"uid": "U1", "persistent_id": 1, "tag": None, "metadata": {}}
    data.update(overrides)
    return data


def vocab_stub():
    return {
        "roles": {
            "bed_frame": {
                "system": "furniture",
                "zone": "sleep",
                "ifc": "IfcFurniture",
            },
            "beam": {"system": "structure", "zone": "envelope", "ifc": "IfcBeam"},
        },
        "tag_roles": {"wall": "wall", "beam": "beam"},
    }


class VocabLearnTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.reg = gate.Registry(str(gate.DEFAULT_REGISTRY))

    def test_tag_missing_from_vocab_is_learned(self):
        nodes = [
            node(tag="bed_frame", metadata={"cad2llm": {"role": "bed_frame"}}),
            node(
                uid="U2",
                persistent_id=2,
                tag="bed_frame",
                metadata={"cad2llm": {"role": "bed_frame"}},
            ),
        ]
        stats = learn.collect_evidence(self.reg, nodes)
        result = learn.plan(stats, vocab_stub())
        self.assertEqual(
            [(i["tag"], i["role"], i["instances"]) for i in result["add"]],
            [("bed_frame", "bed_frame", 2)],
        )

    def test_known_tag_is_never_rewritten(self):
        stats = learn.collect_evidence(
            self.reg, [node(tag="beam", metadata={"cad2llm": {"role": "beam"}})]
        )
        result = learn.plan(stats, vocab_stub())
        self.assertEqual(result["add"], [])
        self.assertIn("beam", result["known"])

    def test_conflicting_evidence_is_reported_not_guessed(self):
        nodes = [
            node(tag="mystery", metadata={"cad2llm": {"role": "beam"}}),
            node(
                uid="U2",
                persistent_id=2,
                tag="mystery",
                metadata={"cad2llm": {"role": "bed_frame"}},
            ),
        ]
        stats = learn.collect_evidence(self.reg, nodes)
        result = learn.plan(stats, vocab_stub())
        self.assertEqual(result["add"], [])
        self.assertEqual([c["tag"] for c in result["conflicts"]], ["mystery"])

    def test_apply_writes_tag_roles_and_provenance(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rules.json"
            path.write_text(json.dumps({"vocab": vocab_stub()}), encoding="utf-8")
            result = {
                "add": [
                    {
                        "tag": "bed_frame",
                        "role": "bed_frame",
                        "instances": 2,
                        "votes": 2,
                        "sources": {"metadata": 2},
                    }
                ],
            }
            out = learn.apply_plan(path, result, stamp="2026-11-03")
            self.assertEqual(out["written"], 1)
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(data["vocab"]["tag_roles"]["bed_frame"], "bed_frame")
            self.assertEqual(data["vocab"]["learned"]["bed_frame"]["role"], "bed_frame")
            self.assertEqual(
                data["vocab"]["learned"]["bed_frame"]["learned_at"], "2026-11-03"
            )

    def test_apply_with_nothing_to_learn_keeps_file_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rules.json"
            original = json.dumps({"vocab": vocab_stub()}, indent=2) + "\n"
            path.write_text(original, encoding="utf-8")
            out = learn.apply_plan(path, {"add": []})
            self.assertEqual(out["written"], 0)
            self.assertEqual(path.read_text(encoding="utf-8"), original)


if __name__ == "__main__":
    unittest.main()
