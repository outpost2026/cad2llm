#!/usr/bin/env python3
"""Deterministic tests for the CAD2LLM core (no SketchUp, no LLM).

Run:  python -m unittest discover -s tests -v
"""

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import cad2llm_core as core  # noqa: E402

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "mini.dae"


class TestDaeConverter(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.doc = core.parse_dae(str(FIXTURE))
        cls.by_name = {}
        for n in cls.doc["nodes"]:
            cls.by_name.setdefault(n["name"], []).append(n)

    def test_noise_camera_skipped(self):
        names = [n["name"] for n in self.doc["nodes"]]
        self.assertFalse(any(n.startswith("skp_camera") for n in names))

    def test_no_definition_duplication(self):
        names = [n["name"] for n in self.doc["nodes"]]
        self.assertEqual(
            sorted(names), sorted(["Box_A", "Rot_Z90", "Battery", "cell", "cell"])
        )
        self.assertNotIn("box_definition", names)
        self.assertNotIn("battery_definition", names)

    def test_units_inch_to_mm(self):
        box = self.by_name["Box_A"][0]
        self.assertEqual(box["spatial"]["origin_mm"], {"x": 25.4, "y": 50.8, "z": 76.2})
        self.assertEqual(
            box["spatial"]["size_mm"], {"x": 254.0, "y": 508.0, "z": 762.0}
        )
        self.assertEqual(box["spatial"]["bbox_mm"]["min"]["z"], 76.2)

    def test_definition_name_kept(self):
        box = self.by_name["Box_A"][0]
        self.assertEqual(box["definition_name"], "box_definition")
        self.assertEqual(box["entity_type"], "component")

    def test_rotation_z90(self):
        rot = self.by_name["Rot_Z90"][0]
        self.assertAlmostEqual(rot["spatial"]["rotation_deg"]["z"], 90.0, places=3)
        self.assertAlmostEqual(rot["spatial"]["size_mm"]["x"], 508.0, places=3)
        self.assertAlmostEqual(rot["spatial"]["size_mm"]["y"], 254.0, places=3)

    def test_matrix_translation_in_mm(self):
        box = self.by_name["Box_A"][0]
        m = box["spatial"]["matrix_4x4"]
        self.assertEqual([m[0][3], m[1][3], m[2][3]], [25.4, 50.8, 76.2])
        self.assertEqual(m[3], [0.0, 0.0, 0.0, 1.0])

    def test_hierarchy_and_container_bbox(self):
        cells = self.by_name["cell"]
        battery = self.by_name["Battery"][0]
        self.assertEqual(len(cells), 2)
        for c in cells:
            self.assertEqual(c["parent_uid"], battery["uid"])
            self.assertEqual(c["hierarchy_path"], ["Battery", "cell"])
        bb = battery["spatial"]["bbox_mm"]
        self.assertEqual(bb["min"]["x"], 0.0)
        self.assertAlmostEqual(bb["max"]["x"], 11 * 25.4, places=3)  # 6+5 in

    def test_unique_uids(self):
        uids = [n["uid"] for n in self.doc["nodes"]]
        self.assertEqual(len(uids), len(set(uids)))

    def test_validation_clean(self):
        v = core.validate(self.doc)
        self.assertEqual(v["counts"]["errors"], 0)
        self.assertEqual(v["counts"]["with_bbox"], 5)
        self.assertFalse([i for i in v["issues"] if i["code"] == "DUPLICATE_POSITION"])


class TestValidation(unittest.TestCase):
    def _doc(self, nodes, caps=None):
        return {
            "schema_version": "4.0",
            "project_metadata": {"units": "mm", "capabilities": caps or {}},
            "nodes": nodes,
        }

    @staticmethod
    def _node(uid, name, x, y, z, sx=100, sy=100, sz=100, path=None):
        return {
            "uid": uid,
            "name": name,
            "definition_name": None,
            "entity_type": "group",
            "tag": "Layer0",
            "hierarchy_path": path or [name],
            "parent_uid": None,
            "spatial": {
                "origin_mm": {"x": x, "y": y, "z": z},
                "bbox_mm": {
                    "min": {"x": x, "y": y, "z": z},
                    "max": {"x": x + sx, "y": y + sy, "z": z + sz},
                },
                "size_mm": {"x": sx, "y": sy, "z": sz},
                "center_mm": {"x": x + sx / 2, "y": y + sy / 2, "z": z + sz / 2},
            },
            "metadata": {},
        }

    def test_duplicate_position_is_error(self):
        v = core.validate(
            self._doc(
                [self._node("a", "Bed", 0, 0, 0), self._node("b", "Bed", 0, 0, 0)]
            )
        )
        self.assertEqual(v["counts"]["errors"], 1)
        self.assertEqual([i["code"] for i in v["issues"]], ["DUPLICATE_POSITION"])

    def test_overlap_skips_ancestors(self):
        parent = self._node("p", "Battery", 0, 0, 0, 500, 500, 500, path=["Battery"])
        child = self._node(
            "c", "cell", 10, 10, 10, 50, 50, 50, path=["Battery", "cell"]
        )
        v = core.validate(self._doc([parent, child]))
        self.assertEqual(v["overlaps"], [])

    def test_overlap_detected_for_siblings(self):
        a = self._node("a", "Desk", 0, 0, 0, 500, 500, 100)
        b = self._node("b", "Chair", 100, 100, 0, 200, 200, 100)
        v = core.validate(self._doc([a, b]))
        self.assertEqual(len(v["overlaps"]), 1)
        self.assertGreater(v["overlaps"][0]["overlap_mm3"], 0)

    def test_clearance_rule_fires(self):
        stove = self._node("s", "stove_main", 0, 0, 0, 400, 300, 600)
        bed = self._node("b", "bed_v1", 600, 0, 0, 2000, 900, 180)
        v = core.validate(self._doc([stove, bed]))
        gaps = [c for c in v["clearances"] if "stove" in c["a_name"]]
        self.assertTrue(gaps and not gaps[0]["ok"])
        self.assertAlmostEqual(gaps[0]["gap_mm"], 200.0, places=1)

    def test_missing_bbox_warns(self):
        n = self._node("x", "Ghost", 0, 0, 0)
        n["spatial"].pop("bbox_mm")
        v = core.validate(self._doc([n]))
        self.assertEqual([i["code"] for i in v["issues"]], ["MISSING_BBOX"])


class TestLegacyLoading(unittest.TestCase):
    def test_v3_meters_converted_to_mm(self):
        path = ROOT / "examples" / "outpost_cad2llm.json"
        if not path.exists():
            self.skipTest("example missing")
        doc = core.load_any(str(path))
        self.assertEqual(len(doc["nodes"]), 46)
        worktop = next(n for n in doc["nodes"] if "Worktop" in n["name"])
        self.assertAlmostEqual(worktop["spatial"]["origin_mm"]["x"], 1520.0, places=1)
        self.assertAlmostEqual(worktop["spatial"]["size_mm"]["x"], 2260.0, places=1)


if __name__ == "__main__":
    unittest.main()
