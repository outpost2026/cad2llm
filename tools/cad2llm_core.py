#!/usr/bin/env python3
"""CAD2LLM core: Collada (.dae) -> schema v4 JSON, validation, summary, diff.

Deterministic, no LLM involved. Schema: docs/SCHEMA_v4.md

Key semantics (learned from SketchUp DAE structure):
  visual_scene node   = instance (its @name is the entity name)
  instance_node ref   = definition (library node @name is definition_name)
  library node is NOT emitted as a separate record (v3 bug: 2x duplication)
"""

from __future__ import annotations

import datetime as _dt
import json
import math
import re
from pathlib import Path

from lxml import etree

from cad2llm_vocab import check_node as vocab_check_node
from cad2llm_vocab import load_vocab

NS = "{http://www.collada.org/2005/11/COLLADASchema}"
SCHEMA_VERSION = "4.0"
MISSING = object()

NOISE_EXACT = {"SketchUp"}
NOISE_PREFIX = ("skp_camera", "instance_", "Camera")

DEFAULT_RULES = {
    "name_pattern": r'^[^\x00-\x1f/\\:*?"<>|]+$',
    "ascii_strict": False,
    "min_size_mm": 1.0,
    "overlap_min_volume_mm3": 1000.0,
    "clearance_rules": [
        {
            "a": r"stove|kamna",
            "b": r"bed|sleep|postel",
            "min_mm": 500,
            "why": "fire safety: radiant heat vs bedding",
        }
    ],
    "required_metadata": {},
}


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _tag(local: str) -> str:
    return NS + local


def _find(el, local):
    return el.find(_tag(local))


def _findall(el, local):
    return el.findall(_tag(local))


def _rnd(v, n=3):
    r = round(float(v), n)
    return 0.0 if r == 0 else r


def _refs(el, local="instance_node"):
    out = []
    for ref in _findall(el, local):
        url = (ref.get("url") or "").lstrip("#")
        if url:
            out.append(url)
    return out


def _asset(root):
    a = _find(root, "asset")
    return a if a is not None else root


def _is_noise(name: str) -> bool:
    if name in NOISE_EXACT:
        return True
    return any(name.startswith(p) for p in NOISE_PREFIX)


def parse_unit(root) -> tuple[float, str]:
    unit_el = _find(_asset(root), "unit")
    if unit_el is None:
        return 25.4, "inch"
    try:
        meter = float(unit_el.get("meter", "1.0"))
    except ValueError:
        meter = 1.0
    return meter * 1000.0, unit_el.get("name", "unknown")


def parse_matrix(text: str) -> list:
    vals = [float(v) for v in text.split()]
    if len(vals) != 16:
        return [[1.0, 0, 0, 0], [0, 1.0, 0, 0], [0, 0, 1.0, 0], [0, 0, 0, 1.0]]
    return [vals[0:4], vals[4:8], vals[8:12], vals[12:16]]


def node_matrix(el) -> list:
    m = _find(el, "matrix")
    if m is not None and m.text and m.text.strip():
        return parse_matrix(m.text.strip())
    return [[1.0, 0, 0, 0], [0, 1.0, 0, 0], [0, 0, 1.0, 0], [0, 0, 0, 1.0]]


def mat_mul(a, b):
    return [
        [sum(a[i][k] * b[k][j] for k in range(4)) for j in range(4)] for i in range(4)
    ]


def mat_apply(m, pt):
    x, y, z = pt
    return (
        m[0][0] * x + m[0][1] * y + m[0][2] * z + m[0][3],
        m[1][0] * x + m[1][1] * y + m[1][2] * z + m[1][3],
        m[2][0] * x + m[2][1] * y + m[2][2] * z + m[2][3],
    )


def trs(m, mm: float) -> dict:
    """Position / Euler XYZ (Rz*Ry*Rx) / quaternion / 4x4 in mm."""
    cols = [[m[r][c] for r in range(3)] for c in range(3)]
    sx = math.sqrt(sum(v * v for v in cols[0])) or 1.0
    sy = math.sqrt(sum(v * v for v in cols[1])) or 1.0
    sz = math.sqrt(sum(v * v for v in cols[2])) or 1.0
    r = [[cols[c][row] / (sx, sy, sz)[c] for c in range(3)] for row in range(3)]

    ry = math.asin(max(-1.0, min(1.0, -r[2][0])))
    cy = math.cos(ry)
    if abs(cy) > 1e-9:
        rx = math.atan2(r[2][1], r[2][2])
        rz = math.atan2(r[1][0], r[0][0])
    else:
        rx = math.atan2(-r[1][2], r[1][1])
        rz = 0.0

    tr = math.sqrt(max(0.0, 1.0 + r[0][0] + r[1][1] + r[2][2]))
    if tr > 1e-6:
        qw = 0.5 * tr
        k = 0.5 / tr
        qx = (r[2][1] - r[1][2]) * k
        qy = (r[0][2] - r[2][0]) * k
        qz = (r[1][0] - r[0][1]) * k
    else:  # degenerate / 180 deg
        qw, qx, qy, qz = 0.0, 0.0, 0.0, 1.0
        if r[0][0] > r[1][1] and r[0][0] > r[2][2]:
            qx = math.sqrt(max(0.0, 1 + r[0][0] - r[1][1] - r[2][2])) / 2
            qy = (r[0][1] + r[1][0]) / (4 * qx)
            qz = (r[0][2] + r[2][0]) / (4 * qx)
        elif r[1][1] > r[2][2]:
            qy = math.sqrt(max(0.0, 1 + r[1][1] - r[0][0] - r[2][2])) / 2
            qx = (r[0][1] + r[1][0]) / (4 * qy)
            qz = (r[1][2] + r[2][1]) / (4 * qy)
        else:
            qz = math.sqrt(max(0.0, 1 + r[2][2] - r[0][0] - r[1][1])) / 2
            qx = (r[0][2] + r[2][0]) / (4 * qz)
            qy = (r[1][2] + r[2][1]) / (4 * qz)

    r4 = 3
    matrix_4x4 = [
        [round(m[i][j] * (mm if (j == 3 and i < 3) else 1.0), 4) for j in range(4)]
        for i in range(4)
    ]
    matrix_4x4[3] = [0.0, 0.0, 0.0, 1.0]

    return {
        "origin_mm": {
            "x": round(m[0][3] * mm, 3),
            "y": round(m[1][3] * mm, 3),
            "z": round(m[2][3] * mm, 3),
        },
        "rotation_deg": {
            "x": round(math.degrees(rx), 3),
            "y": round(math.degrees(ry), 3),
            "z": round(math.degrees(rz), 3),
        },
        "quaternion": {
            "w": round(qw, 6),
            "x": round(qx, 6),
            "y": round(qy, 6),
            "z": round(qz, 6),
        },
        "matrix_4x4": matrix_4x4,
        "scale": {"x": round(sx, 6), "y": round(sy, 6), "z": round(sz, 6)},
    }


# --------------------------------------------------------------------------
# geometry
# --------------------------------------------------------------------------
def geometry_vertices(geom_el) -> list:
    mesh = _find(geom_el, "mesh") if geom_el is not None else None
    if mesh is None:
        return []
    verts_el = _find(mesh, "vertices")
    if verts_el is None:
        return []
    src_id = None
    for inp in _findall(verts_el, "input"):
        if inp.get("semantic") == "POSITION":
            src_id = (inp.get("source") or "").lstrip("#")
            break
    if not src_id:
        return []
    for src in _findall(mesh, "source"):
        if src.get("id") == src_id:
            fa = _find(src, "float_array")
            if fa is None or not fa.text:
                return []
            vals = [float(v) for v in fa.text.split()]
            return [tuple(vals[i : i + 3]) for i in range(0, len(vals) - 2, 3)]
    return []


def node_vertices(el, world, lib_geoms) -> list:
    out = []
    for url in _refs(el, "instance_geometry"):
        geom = lib_geoms.get(url)
        if geom is None:
            continue
        for v in geometry_vertices(geom):
            out.append(mat_apply(world, v))
    return out


def _box(verts, mm) -> dict | None:
    if not verts:
        return None
    xs = [v[0] for v in verts]
    ys = [v[1] for v in verts]
    zs = [v[2] for v in verts]
    return {
        "min": {"x": min(xs) * mm, "y": min(ys) * mm, "z": min(zs) * mm},
        "max": {"x": max(xs) * mm, "y": max(ys) * mm, "z": max(zs) * mm},
    }


def _union(a, b):
    if not a:
        return b
    if not b:
        return a
    return {
        "min": {ax: min(a["min"][ax], b["min"][ax]) for ax in "xyz"},
        "max": {ax: max(a["max"][ax], b["max"][ax]) for ax in "xyz"},
    }


def _spatial(trs_data, box) -> dict:
    mn, mx = box["min"], box["max"]
    return {
        **trs_data,
        "bbox_mm": {
            "min": {ax: _rnd(mn[ax]) for ax in "xyz"},
            "max": {ax: _rnd(mx[ax]) for ax in "xyz"},
        },
        "size_mm": {ax: _rnd(mx[ax] - mn[ax]) for ax in "xyz"},
        "center_mm": {ax: _rnd((mx[ax] + mn[ax]) / 2) for ax in "xyz"},
    }


# --------------------------------------------------------------------------
# converter
# --------------------------------------------------------------------------
class _Ctx:
    def __init__(self, mm, lib_nodes, lib_geoms):
        self.mm = mm
        self.lib_nodes = lib_nodes
        self.lib_geoms = lib_geoms
        self.nodes = []
        self.issues = []
        self.index = 0

    def issue(self, code, severity, message, uid=None):
        self.issues.append(
            {"code": code, "severity": severity, "uid": uid, "message": message}
        )


def index_library(root) -> dict:
    out = {}
    lib = _find(root, "library_nodes")
    if lib is None:
        return out

    def rec(parent):
        for n in _findall(parent, "node"):
            nid = n.get("id")
            if nid:
                out[nid] = n
            rec(n)

    rec(lib)
    return out


def index_geometries(root) -> dict:
    out = {}
    lib = _find(root, "library_geometries")
    if lib is not None:
        for g in _findall(lib, "geometry"):
            gid = g.get("id")
            if gid:
                out[gid] = g
    return out


def _walk(node_el, parent_matrix, path, parent_uid, ctx: _Ctx):
    name = (node_el.get("name") or node_el.get("id") or "unnamed").strip()
    world = mat_mul(parent_matrix, node_matrix(node_el))

    if _is_noise(name):
        for child in _findall(node_el, "node"):
            _walk(child, world, path, parent_uid, ctx)
        for ref_id in _refs(node_el):
            ref = ctx.lib_nodes.get(ref_id)
            if ref is not None:
                _walk(ref, world, path, parent_uid, ctx)
            else:
                ctx.issue(
                    "MISSING_DEFINITION_REF",
                    "warn",
                    f"instance_node #{ref_id} not found",
                    None,
                )
        return None

    ctx.index += 1
    uid = node_el.get("id") or f"{parent_uid or 'root'}~{ctx.index}"
    ref_ids = _refs(node_el)
    definition_name = None
    for ref_id in ref_ids:
        ref = ctx.lib_nodes.get(ref_id)
        if ref is None:
            ctx.issue(
                "MISSING_DEFINITION_REF",
                "warn",
                f"instance_node #{ref_id} not found",
                uid,
            )
            continue
        if definition_name is None:
            definition_name = (ref.get("name") or ref.get("id") or "").strip() or None

    verts = node_vertices(node_el, world, ctx.lib_geoms)
    pending = []
    for ref_id in ref_ids:
        ref = ctx.lib_nodes.get(ref_id)
        if ref is None:
            continue
        verts += node_vertices(ref, world, ctx.lib_geoms)
        for child in _findall(ref, "node"):
            pending.append(child)
    for child in _findall(node_el, "node"):
        pending.append(child)

    trs_data = trs(world, ctx.mm)
    record = {
        "uid": uid,
        "persistent_id": None,
        "name": name,
        "definition_name": definition_name,
        "entity_type": "component" if ref_ids else ("group" if pending else "node"),
        "tag": None,
        "hierarchy_path": path + [name],
        "parent_uid": parent_uid,
        "spatial": {},
        "metadata": {},
    }

    own = _box(verts, ctx.mm)
    record["spatial"] = _spatial(trs_data, own) if own else trs_data
    if not own:
        ctx.issue(
            "MISSING_GEOMETRY",
            "info",
            "node has no own geometry (container / zone placeholder)",
            uid,
        )

    ctx.nodes.append(record)
    union = own
    for child in pending:
        cb = _walk(child, world, path + [name], uid, ctx)
        if cb:
            union = _union(union, cb)

    # container bbox = union of descendants (parity with SketchUp definition.bounds)
    if union and union != own:
        record["spatial"] = _spatial(trs_data, union)
        for issue in ctx.issues:
            if issue["code"] == "MISSING_GEOMETRY" and issue["uid"] == uid:
                ctx.issues.remove(issue)
                break
    return union


def parse_dae(path: str) -> dict:
    src = Path(path)
    root = etree.parse(str(src)).getroot()
    mm, unit_name = parse_unit(root)
    up_axis_el = _find(_asset(root), "up_axis")
    up_axis = (
        up_axis_el.text.strip()
        if up_axis_el is not None and up_axis_el.text
        else "Y_UP"
    )

    vis_url = ""
    scene_el = _find(root, "scene")
    if scene_el is not None:
        ivs = _find(scene_el, "instance_visual_scene")
        if ivs is not None:
            vis_url = (ivs.get("url") or "").lstrip("#")

    vis = None
    lib_vis = _find(root, "library_visual_scenes")
    if lib_vis is not None:
        scenes = _findall(lib_vis, "visual_scene")
        for s in scenes:
            if s.get("id") == vis_url:
                vis = s
                break
        if vis is None and scenes:
            vis = scenes[0]

    ctx = _Ctx(mm, index_library(root), index_geometries(root))
    identity = [[1.0, 0, 0, 0], [0, 1.0, 0, 0], [0, 0, 1.0, 0], [0, 0, 0, 1.0]]
    if vis is not None:
        for top in _findall(vis, "node"):
            _walk(top, identity, [], None, ctx)

    doc = {
        "schema_version": SCHEMA_VERSION,
        "project_metadata": {
            "name": src.stem,
            "export_date": _dt.datetime.now(_dt.timezone.utc)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z"),
            "units": "mm",
            "up_axis": up_axis,
            "unit_source": {"native": unit_name, "mm_per_unit": mm},
            "global_origin_gps": None,
            "source": {
                "channel": "dae",
                "file": src.name,
                "path": str(src),
                "size_bytes": src.stat().st_size,
                "mtime_utc": _dt.datetime.fromtimestamp(
                    src.stat().st_mtime, _dt.timezone.utc
                )
                .replace(microsecond=0)
                .isoformat()
                .replace("+00:00", "Z"),
            },
            "capabilities": {
                "hierarchy": True,
                "bbox_mm": True,
                "rotation": True,
                "instance_definition": True,
                "tags": False,
                "metadata_dictionaries": False,
                "persistent_ids": False,
                "gps": False,
                "note": "DAE channel: SketchUp replaces spaces with underscores in names, "
                "does not export tags, attribute dictionaries or persistent IDs.",
            },
        },
        "nodes": ctx.nodes,
        "validation": {"issues": ctx.issues},
    }
    return doc


# --------------------------------------------------------------------------
# schema-agnostic loading (v3 legacy support: meters -> mm)
# --------------------------------------------------------------------------
def load_any(path: str) -> dict:
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(doc, list):  # selected_export legacy
        doc = {
            "schema_version": "2.0",
            "nodes": doc,
            "project_metadata": {"units": "inch"},
        }
    if "schema_version" in doc:
        return doc

    units = (doc.get("project_metadata") or {}).get("units", "meters")
    factor = 1000.0 if units == "meters" else 1.0
    nodes = []
    for n in doc.get("nodes", []):
        s = n.get("spatial_data_m") or n.get("spatial") or {}
        origin = s.get("origin_xyz") or s.get("center") or [0, 0, 0]
        center = s.get("center_xyz") or origin
        size = s.get("size_xyz") or s.get("size") or [0, 0, 0]

        def mmv(v):
            return round(float(v) * factor, 3)

        o = {"x": mmv(origin[0]), "y": mmv(origin[1]), "z": mmv(origin[2])}
        c = {"x": mmv(center[0]), "y": mmv(center[1]), "z": mmv(center[2])}
        sz = {"x": mmv(size[0]), "y": mmv(size[1]), "z": mmv(size[2])}
        nodes.append(
            {
                "uid": n.get("uid") or f"{n.get('id')}",
                "persistent_id": n.get("id"),
                "name": n.get("instance_name") or n.get("name") or "unnamed",
                "definition_name": n.get("definition_name") or n.get("definition"),
                "entity_type": n.get("entity_type") or n.get("type") or "unknown",
                "tag": n.get("tag"),
                "hierarchy_path": n.get("hierarchy_path") or [],
                "parent_uid": n.get("parent_id"),
                "spatial": {
                    "origin_mm": o,
                    "center_mm": c,
                    "size_mm": sz,
                    "bbox_mm": {
                        "min": {
                            "x": round(c["x"] - sz["x"] / 2, 3),
                            "y": round(c["y"] - sz["y"] / 2, 3),
                            "z": round(c["z"] - sz["z"] / 2, 3),
                        },
                        "max": {
                            "x": round(c["x"] + sz["x"] / 2, 3),
                            "y": round(c["y"] + sz["y"] / 2, 3),
                            "z": round(c["z"] + sz["z"] / 2, 3),
                        },
                    },
                },
                "metadata": n.get("metadata") or {},
            }
        )
    doc["schema_version"] = "3.0(converted)"
    doc["nodes"] = nodes
    doc["validation"] = {
        "issues": [
            {
                "code": "LEGACY_SCHEMA",
                "severity": "info",
                "message": f"converted from legacy schema with units={units}; "
                "bbox derived from center+-size/2 (approximation)",
            }
        ]
    }
    return doc


# --------------------------------------------------------------------------
# validation
# --------------------------------------------------------------------------
def _bbox(n):
    return (n.get("spatial") or {}).get("bbox_mm")


def _ancestor(a, b) -> bool:
    pa, pb = a.get("hierarchy_path") or [], b.get("hierarchy_path") or []
    return pa[: len(pb)] == pb or pb[: len(pa)] == pa


def _gap(a, b) -> float:
    ba, bb = _bbox(a), _bbox(b)
    if not ba or not bb:
        return math.inf
    d2 = 0.0
    for ax in "xyz":
        lo, hi = ba["min"][ax], ba["max"][ax]
        lo2, hi2 = bb["min"][ax], bb["max"][ax]
        d = max(0.0, lo2 - hi, lo - hi2)
        d2 += d * d
    return math.sqrt(d2)


def _overlap_volume(a, b) -> float:
    ba, bb = _bbox(a), _bbox(b)
    if not ba or not bb:
        return 0.0
    v = 1.0
    for ax in "xyz":
        lo = max(ba["min"][ax], bb["min"][ax])
        hi = min(ba["max"][ax], bb["max"][ax])
        if hi <= lo:
            return 0.0
        v *= hi - lo
    return v


def validate(
    doc: dict, rules: dict | None = None, vocab_data: dict | None = None
) -> dict:
    rules = {**DEFAULT_RULES, **(rules or {})}
    nodes = doc.get("nodes", [])
    issues = []
    caps = (doc.get("project_metadata") or {}).get("capabilities") or {}
    min_size = float(rules["min_size_mm"])
    ovl_min = float(rules["overlap_min_volume_mm3"])

    with_bbox = [n for n in nodes if _bbox(n)]
    vocab = vocab_data if vocab_data is not None else load_vocab(rules)
    for n in nodes:
        s = n.get("spatial") or {}
        for vocab_issue in vocab_check_node(n, vocab):
            issues.append({**vocab_issue, "uid": n.get("uid")})
        if not _bbox(n):
            issues.append(
                {
                    "code": "MISSING_BBOX",
                    "severity": "warn",
                    "uid": n.get("uid"),
                    "message": f"{n.get('name')}: no bounding box",
                }
            )
            continue
        size = s.get("size_mm") or {}
        if any(float(size.get(k, 0)) < min_size for k in "xyz"):
            issues.append(
                {
                    "code": "ZERO_SIZE",
                    "severity": "info",
                    "uid": n.get("uid"),
                    "message": f"{n.get('name')}: degenerate size {size}",
                }
            )
        if not caps.get("tags", False) and not n.get("tag"):
            pass
        name = n.get("name") or ""
        if not re.match(rules["name_pattern"], name):
            issues.append(
                {
                    "code": "NAME_PATTERN",
                    "severity": "warn",
                    "uid": n.get("uid"),
                    "message": f"name '{name}' outside pattern {rules['name_pattern']}",
                }
            )
        if rules.get("ascii_strict") and not name.isascii():
            issues.append(
                {
                    "code": "NAME_ASCII",
                    "severity": "warn",
                    "uid": n.get("uid"),
                    "message": f"non-ASCII name '{name}'",
                }
            )

    # duplicate positions / names among siblings
    seen = {}
    for n in nodes:
        o = (n.get("spatial") or {}).get("origin_mm")
        if not o:
            continue
        key = (n.get("name"), round(o["x"], 1), round(o["y"], 1), round(o["z"], 1))
        if key in seen:
            issues.append(
                {
                    "code": "DUPLICATE_POSITION",
                    "severity": "error",
                    "uid": n.get("uid"),
                    "message": f"'{key[0]}' at same position as uid {seen[key]}",
                }
            )
        else:
            seen[key] = n.get("uid")

    overlaps = []
    for i, a in enumerate(with_bbox):
        for b in with_bbox[i + 1 :]:
            if _ancestor(a, b):
                continue
            vol = _overlap_volume(a, b)
            if vol >= ovl_min:
                overlaps.append(
                    {
                        "a": a.get("uid"),
                        "b": b.get("uid"),
                        "a_name": a.get("name"),
                        "b_name": b.get("name"),
                        "overlap_mm3": round(vol, 1),
                    }
                )

    clearances = []
    for rule in rules.get("clearance_rules", []):
        ra, rb = re.compile(rule["a"], re.I), re.compile(rule["b"], re.I)
        for a in with_bbox:
            if not ra.search(a.get("name") or ""):
                continue
            for b in with_bbox:
                if a is b or not rb.search(b.get("name") or ""):
                    continue
                g = _gap(a, b)
                clearances.append(
                    {
                        "a_name": a.get("name"),
                        "b_name": b.get("name"),
                        "gap_mm": round(g, 1) if g != math.inf else None,
                        "min_mm": rule["min_mm"],
                        "why": rule.get("why", ""),
                        "ok": g >= rule["min_mm"],
                    }
                )
                if g < rule["min_mm"]:
                    issues.append(
                        {
                            "code": "CLEARANCE_VIOLATION",
                            "severity": "warn",
                            "uid": a.get("uid"),
                            "message": f"{a.get('name')} vs {b.get('name')}: gap {round(g, 1)} mm < {rule['min_mm']} mm ({rule.get('why', '')})",
                        }
                    )

    z_table = sorted(
        (
            {
                "uid": n.get("uid"),
                "name": n.get("name"),
                "z_min": _bbox(n)["min"]["z"],
                "z_max": _bbox(n)["max"]["z"],
                "height": round(_bbox(n)["max"]["z"] - _bbox(n)["min"]["z"], 1),
            }
            for n in with_bbox
        ),
        key=lambda r: r["z_min"],
    )

    severity_rank = {"error": 0, "warn": 1, "info": 2}
    issues.sort(key=lambda i: severity_rank.get(i.get("severity"), 3))

    return {
        "checked_at": _dt.datetime.now(_dt.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
        "rules": rules,
        "counts": {
            "nodes": len(nodes),
            "with_bbox": len(with_bbox),
            "errors": sum(1 for i in issues if i.get("severity") == "error"),
            "warnings": sum(1 for i in issues if i.get("severity") == "warn"),
        },
        "capabilities": caps,
        "issues": issues,
        "overlaps": overlaps,
        "clearances": clearances,
        "z_table": z_table,
    }


def summarize(doc: dict, max_rows: int = 60) -> str:
    md = doc.get("project_metadata") or {}
    src = md.get("source") or {}
    v = doc.get("validation") or {}
    c = v.get("counts") or {}
    lines = []
    lines.append(f"# CAD2LLM summary — {md.get('name', '?')}")
    lines.append("")
    lines.append(
        f"- schema: `{doc.get('schema_version')}` | units: `{md.get('units')}` | up: `{md.get('up_axis')}`"
    )
    lines.append(
        f"- source: `{src.get('channel', '?')}` ← {src.get('file', '?')} ({src.get('size_bytes', '?')} B, {src.get('mtime_utc', '?')})"
    )
    caps = md.get("capabilities") or {}
    missing = [k for k, ok in caps.items() if ok is False]
    if missing:
        lines.append(f"- unavailable in this channel: {', '.join(missing)}")
    lines.append(
        f"- nodes: **{c.get('nodes', len(doc.get('nodes', [])))}** | with bbox: {c.get('with_bbox', '?')} | errors: {c.get('errors', '?')} | warnings: {c.get('warnings', '?')}"
    )
    lines.append("")

    zt = v.get("z_table") or []
    if zt:
        lines.append("## Vertical table (z_min ascending)")
        lines.append("")
        lines.append("| name | z_min | z_max | h |")
        lines.append("|---|---:|---:|---:|")
        for r in zt[:max_rows]:
            lines.append(
                f"| {r['name']} | {r['z_min']} | {r['z_max']} | {r['height']} |"
            )
        if len(zt) > max_rows:
            lines.append(f"| … {len(zt) - max_rows} more | | | |")
        lines.append("")

    ov = v.get("overlaps") or []
    lines.append(f"## Overlaps: {len(ov)}")
    for o in ov[:15]:
        lines.append(f"- `{o['a_name']}` × `{o['b_name']}` — {o['overlap_mm3']} mm³")
    if len(ov) > 15:
        lines.append(f"- … {len(ov) - 15} more")
    lines.append("")

    cl = v.get("clearances") or []
    if cl:
        lines.append("## Clearance rules")
        lines.append("")
        lines.append("| a | b | gap mm | min mm | ok |")
        lines.append("|---|---|---:|---:|:--:|")
        for r in cl:
            lines.append(
                f"| {r['a_name']} | {r['b_name']} | {r['gap_mm']} | {r['min_mm']} | {'OK' if r['ok'] else 'FAIL'} |"
            )
        lines.append("")

    iss = v.get("issues") or []
    if iss:
        lines.append("## Issues")
        for i in iss[:25]:
            lines.append(
                f"- **{i.get('severity')}** `{i.get('code')}` — {i.get('message')}"
            )
        if len(iss) > 25:
            lines.append(f"- … {len(iss) - 25} more")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------
# diff
# --------------------------------------------------------------------------
def _norm_name(n: dict) -> str:
    """DAE rewrites '_', '#', ',' and spaces, so names are compared loosely."""
    name = (n.get("name") or "").lower()
    name = re.sub(r"[\s_#,.]+", " ", name)
    return re.sub(r"\s+", " ", name).strip()


def _centre(n: dict):
    c = (n.get("spatial") or {}).get("center_mm")
    if not c:
        return None
    return (float(c["x"]), float(c["y"]), float(c["z"]))


def _dist(p, q) -> float:
    if p is None or q is None:
        return math.inf
    return math.sqrt(sum((p[i] - q[i]) ** 2 for i in range(3)))


def diff(a: dict, b: dict, tol_mm: float = 500.0) -> dict:
    """Pair nodes by normalized name + nearest bbox centre (channel-agnostic)."""
    left = a.get("nodes", [])
    right = b.get("nodes", [])
    used: set[int] = set()
    pairs = []
    for na in left:
        name, ca = _norm_name(na), _centre(na)
        best, best_d = None, math.inf
        for i, nb in enumerate(right):
            if i in used or _norm_name(nb) != name:
                continue
            d = _dist(ca, _centre(nb))
            if d < best_d:
                best, best_d = i, d
        if best is not None and best_d <= tol_mm:
            used.add(best)
            pairs.append((na, right[best]))

    changed = []
    for na, nb in pairs:
        sa, sb = na.get("spatial") or {}, nb.get("spatial") or {}
        delta = {}
        for f in ("origin_mm", "size_mm", "center_mm"):
            if sa.get(f) and sb.get(f):
                d = {ax: round(float(sb[f][ax]) - float(sa[f][ax]), 1) for ax in "xyz"}
                if any(abs(v) >= 1.0 for v in d.values()):
                    delta[f] = d
        if delta:
            changed.append(
                {"a": na.get("name"), "b": nb.get("name"), "delta_mm": delta}
            )

    matched_left = {id(na) for na, _ in pairs}
    return {
        "only_in_a": [
            {"name": n.get("name"), "center_mm": _centre(n)}
            for n in left
            if id(n) not in matched_left
        ],
        "only_in_b": [
            {"name": n.get("name"), "center_mm": _centre(n)}
            for i, n in enumerate(right)
            if i not in used
        ],
        "spatial_changes": changed,
        "counts": {"a": len(left), "b": len(right), "matched": len(pairs)},
    }
