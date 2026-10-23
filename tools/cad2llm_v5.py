#!/usr/bin/env python3
"""CAD2LLM v5 (F1): deterministic semantics + normalized validation + brief.

Input:  v4 JSON (tools/cad2llm.py convert output)
Output: v5 document fragment per docs/schema/cad2llm_v5.schema.json

F1 scope: facts + semantics + brief. Relations, free_space, rules v2 and ops
are F2-F4 and stay empty here on purpose. No LLM, no randomness.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import unicodedata
from pathlib import Path

import cad2llm_vocab as vocab

SCHEMA_VERSION = "5.0"
BRIEF_MAX_CHARS = 4000
TOP_FINDINGS_MAX = 5

STRUCTURAL = {"wall", "insulation", "floor", "ceiling", "roof", "beam", "truss"}

# Order matters: first match wins. Structural tokens come before openings,
# because wall names may contain "okna" (openings for windows).
ROLE_RULES = [
    ("wall", r"\b(stena|steny|wall|zed|zdi)\b", "CLASS_B", 0.9),
    ("insulation", r"\b(izolace|izolacni|insulation)\b", "CLASS_B", 0.95),
    ("floor", r"\b(podlaha|podlahy|floor)\b", "CLASS_B", 0.9),
    ("ceiling", r"\b(strop|stropy|ceiling)\b", "CLASS_B", 0.9),
    ("roof", r"\b(strecha|střecha|roof|krov)\b", "CLASS_B", 0.9),
    ("chimney", r"\b(chimney|komin|komín|flue)\b", "CLASS_C", 0.8),
    ("beam", r"\b(nosnik|nosniky|beam|truss|sloup)\b", "CLASS_B", 0.85),
    ("window", r"\b(okno|okna|oken|windows?|window)\b", "CLASS_B", 0.9),
    ("door", r"\b(dvere|dveri|door|doors)\b", "CLASS_B", 0.9),
    ("stove", r"\b(kamna|stove|pec)\b", "CLASS_B", 0.9),
    ("bed", r"\b(postel|bed|matrace)\b", "CLASS_B", 0.9),
    ("worktop", r"\b(worktop|pracovni deska|deska|pult)\b", "CLASS_B", 0.85),
    ("table", r"\b(stul|table|desk)\b", "CLASS_B", 0.85),
    ("furniture", r"\b(zidle|chair|kreslo)\b", "CLASS_B", 0.85),
    ("storage", r"\b(skrin|shelf|rack|uloz|storage)\b", "CLASS_B", 0.85),
    ("battery", r"\b(lfp|battery|baterie|baterka)\b", "CLASS_C", 0.8),
    ("inverter", r"\b(inverter|menic|mppt|victron)\b", "CLASS_C", 0.85),
    ("appliance", r"\b(lednice|fridge|spotrebic|appliance)\b", "CLASS_C", 0.8),
    ("opening", r"\b(opening|otvor|otvory|prostup)\b", "CLASS_B", 0.85),
    ("void", r"\b(void|otvor|entry point|pruchod)\b", "CLASS_B", 0.85),
    ("marker", r"\b(marker|helper|pomocny)\b", "CLASS_C", 0.6),
    ("boundary", r"\b(boundary|hranice|pudorys)\b", "CLASS_C", 0.7),
]

ZONE_RULES = [
    ("sleep", r"\b(sleep|sleeping|spanek|spani|loznice|bed|postel)\b"),
    ("dining", r"\b(dining|dinning|jidelna|jidelni|jidlo)\b"),
    ("work", r"\b(work|prac|kuchyn|kitchen|desk)\b"),
    ("entry", r"\b(entry|vstup|vstupni|pruchod)\b"),
    ("energy", r"\b(energy|lfp|battery|baterie|tech)\b"),
]

ROLE_ZONE_FALLBACK = {
    "bed": "sleep",
    "worktop": "work",
    "table": "dining",
    "battery": "energy",
    "inverter": "tech",
    "appliance": "tech",
    "storage": "storage",
    "void": "entry",
    "opening": "entry",
    "stove": "sleep",
    "roof": "envelope",
    "chimney": "envelope",
    "marker": "unknown",
}

# Secondary evidence: SketchUp tags (GUI channel) when the name carries no role.
TAG_ROLE_RULES = [
    ("wall", r"\b(walls?|stena|steny)\b"),
    ("insulation", r"\b(insulation|izolace)\b"),
    ("floor", r"\bfloor\b"),
    ("ceiling", r"\b(ceiling|strop)\b"),
    ("window", r"\b(windows?|okna?)\b"),
    ("door", r"\b(doors?|dvere)\b"),
    ("bed", r"\bbed\b"),
    ("table", r"\btable\b"),
    ("void", r"\b(entrance|void|entry)\b"),
    ("boundary", r"\b(boundary|pudorys|room\d*)\b"),
]

TAG_ZONE_RULES = [
    ("energy", r"\benergy\b"),
    ("envelope", r"\b(walls?|insulation|floor|ceiling|construction)\b"),
    ("sleep", r"\bbed\b"),
    ("dining", r"\btable\b"),
    ("work", r"\b(shelves?|shelf|worktop)\b"),
    ("entry", r"\b(entrance|entry|void)\b"),
]

# A nameless child may inherit its parent's role when the parent is a known
# design object (16th LFP cell named "Unnamed" inside a battery pack).
PARENT_INHERIT_ROLES = {
    "battery",
    "inverter",
    "worktop",
    "table",
    "bed",
    "stove",
    "appliance",
    "storage",
    "furniture",
}

ISSUE_META = {
    "ZERO_SIZE": ("R_GEOM_ZERO_THICKNESS", "CLASS_A", 0.99, "ARTIFACT"),
    "MISSING_BBOX": ("R_GEOM_MISSING_BBOX", "CLASS_A", 0.99, "UNKNOWN"),
    "DUPLICATE_POSITION": ("R_DUP_POSITION", "CLASS_A", 0.95, "DEFECT"),
    "NAME_PATTERN": ("R_NAME_PATTERN", "CLASS_B", 0.8, "UNKNOWN"),
    "NAME_ASCII": ("R_NAME_ASCII", "CLASS_B", 0.8, "UNKNOWN"),
    "CLEARANCE_VIOLATION": ("R_CLEARANCE_MIN", "CLASS_B", 0.8, "DEFECT"),
    "MISSING_GEOMETRY": ("R_GEOM_EMPTY_NODE", "CLASS_A", 0.99, "ARTIFACT"),
    "MISSING_DEFINITION_REF": ("R_GEOM_MISSING_DEF", "CLASS_A", 0.99, "UNKNOWN"),
    "LEGACY_SCHEMA": ("R_MIGRATION_LEGACY", "CLASS_C", 0.7, "ARTIFACT"),
}
ISSUE_DEFAULT = ("R_VALIDATION", "CLASS_C", 0.5, "UNKNOWN")

SEVERITY_MAP = {"error": "CRITICAL", "warn": "WARNING", "info": "INFO"}


def _norm(text: str) -> str:
    decomposed = unicodedata.normalize("NFD", (text or "").lower())
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return re.sub(r"[_\-]+", " ", stripped)


def _match(rule_re: str, name: str) -> str | None:
    m = re.search(rule_re, name)
    return m.group(0) if m else None


def _intent_for(role: str) -> str:
    if role == "unknown":
        return "UNKNOWN"
    if role in {"void", "marker", "boundary"}:
        return "ARTIFACT"
    if role in STRUCTURAL or role in {"window", "door", "opening"}:
        return "CONSTRUCTION"
    return "DESIGN_DECISION"


def _tag_lookup(rules: list, tag: str) -> tuple[str, str] | None:
    norm = _norm(tag)
    for candidate, pattern in rules:
        m = re.search(pattern, norm)
        if m:
            return candidate, m.group(0)
    return None


_VOCAB_CACHE: dict | None = None


def _vocab_tag_role(tag: str) -> str | None:
    """Shared vocabulary (rules.json) is the primary tag->role source."""
    global _VOCAB_CACHE
    if _VOCAB_CACHE is None:
        try:
            _VOCAB_CACHE = vocab.load_vocab()
        except Exception:  # noqa: BLE001 - rules file is optional at runtime
            _VOCAB_CACHE = {}
    return vocab.tag_role(tag, _VOCAB_CACHE)


def _apply_semantic_fallbacks(nodes: list) -> None:
    """Tag + parent-role fallback for nodes whose name gave no role/zone."""
    by_uid = {n.get("uid"): n for n in nodes}
    for node in nodes:
        s = node.get("semantics") or {}
        role = s.get("role", "unknown")
        zone = s.get("zone")
        tag = node.get("tag") or ""
        size = (node.get("spatial") or {}).get("size_mm") or {}
        degenerate = bool(size) and any(float(size.get(ax, 0)) < 1.0 for ax in "xyz")
        evidence = s.setdefault("evidence", [])
        role_changed = False

        if role == "unknown":
            parent = by_uid.get(node.get("parent_uid"))
            parent_role = ((parent or {}).get("semantics") or {}).get("role")
            if parent_role in PARENT_INHERIT_ROLES:
                role = parent_role
                role_changed = True
                s["confidence"] = max(float(s.get("confidence", 0.0)), 0.6)
                evidence.append(
                    {
                        "rule_id": "R_PARENT_ROLE_INHERIT",
                        "rule_class": "CLASS_C",
                        "support_n": 1,
                        "detail": f"parent {parent.get('uid')} role '{parent_role}'",
                    }
                )

        if role == "unknown":
            vocab_role = _vocab_tag_role(tag)
            found = (
                (vocab_role, tag) if vocab_role else _tag_lookup(TAG_ROLE_RULES, tag)
            )
            if found:
                role, token = found
                role_changed = True
                s["confidence"] = max(float(s.get("confidence", 0.0)), 0.7)
                evidence.append(
                    {
                        "rule_id": f"R_TAG_ROLE_{role.upper()}",
                        "rule_class": "CLASS_C",
                        "support_n": 1,
                        "detail": f"tag token '{token}'",
                    }
                )

        if zone in (None, "unknown"):
            found_zone = _tag_lookup(TAG_ZONE_RULES, tag)
            if found_zone:
                zone, token = found_zone
                evidence.append(
                    {
                        "rule_id": f"R_TAG_ZONE_{zone.upper()}",
                        "rule_class": "CLASS_C",
                        "support_n": 1,
                        "detail": f"tag token '{token}'",
                    }
                )
        if zone in (None, "unknown"):
            zone = ROLE_ZONE_FALLBACK.get(role, "unknown")

        s["role"] = role
        s["zone"] = zone
        if role_changed:
            s["intent"] = "ARTIFACT" if degenerate else _intent_for(role)


def classify(
    name: str, spatial: dict | None = None, metadata: dict | None = None
) -> dict:
    """Deterministic role/zone/intent for one node.

    Authored metadata (cad2llm.role/zone) wins over name heuristics: the plan
    already encodes the human decision. Unknown stays UNKNOWN.
    """
    norm = _norm(name)
    evidence = []

    role, rule_class, confidence = "unknown", "CLASS_C", 0.0
    best_pos = None
    for candidate, pattern, cls, conf in ROLE_RULES:
        m = re.search(pattern, norm)
        if not m:
            continue
        if best_pos is not None and m.start() >= best_pos:
            continue
        best_pos = m.start()
        role, rule_class, confidence = candidate, cls, conf
        evidence = [
            {
                "rule_id": f"R_NAME_ROLE_{candidate.upper()}",
                "rule_class": cls,
                "support_n": 1,
                "detail": f"name token '{m.group(0)}' at {m.start()}",
            }
        ]

    zone = None
    if role in STRUCTURAL:
        zone = "envelope"
    else:
        for candidate, pattern in ZONE_RULES:
            token = _match(pattern, norm)
            if token:
                zone = candidate
                evidence.append(
                    {
                        "rule_id": f"R_NAME_ZONE_{candidate.upper()}",
                        "rule_class": "CLASS_B",
                        "support_n": 1,
                        "detail": f"zone token '{token}'",
                    }
                )
                break
        if zone is None:
            zone = ROLE_ZONE_FALLBACK.get(role, "unknown")

    cad = (metadata or {}).get("cad2llm") if metadata else None
    cad = cad or {}
    if cad.get("role"):
        role = cad["role"]
        rule_class = "CLASS_A"
        confidence = max(confidence, 0.95)
        evidence.append(
            {
                "rule_id": "R_METADATA_ROLE",
                "rule_class": "CLASS_A",
                "support_n": 1,
                "detail": f"metadata role '{role}'",
            }
        )
    if cad.get("zone") and cad["zone"] != "unknown":
        zone = cad["zone"]
        evidence.append(
            {
                "rule_id": "R_METADATA_ZONE",
                "rule_class": "CLASS_A",
                "support_n": 1,
                "detail": f"metadata zone '{zone}'",
            }
        )
    elif zone in (None, "unknown"):
        zone = ROLE_ZONE_FALLBACK.get(role, "unknown")

    size = (spatial or {}).get("size_mm") or {}
    degenerate = bool(size) and any(float(size.get(ax, 0)) < 1.0 for ax in "xyz")

    if role == "unknown":
        intent = "UNKNOWN"
    elif role in {"void", "marker", "boundary"}:
        intent = "ARTIFACT"
    elif role in STRUCTURAL or role in {"window", "door", "opening"}:
        intent = "CONSTRUCTION"
    else:
        intent = "DESIGN_DECISION"

    if degenerate:
        intent = "ARTIFACT"
        evidence.append(
            {
                "rule_id": "R_GEOM_ZERO_THICKNESS",
                "rule_class": "CLASS_A",
                "support_n": 1,
                "detail": f"degenerate size {size}",
            }
        )
        confidence = max(confidence, 0.9)

    return {
        "role": role,
        "zone": zone,
        "intent": intent,
        "confidence": round(confidence, 2),
        "evidence": evidence,
    }


def _bbox(node: dict) -> dict | None:
    return (node.get("spatial") or {}).get("bbox_mm")


def _union(boxes: list) -> dict | None:
    if not boxes:
        return None
    return {
        "min": {ax: min(b["min"][ax] for b in boxes) for ax in "xyz"},
        "max": {ax: max(b["max"][ax] for b in boxes) for ax in "xyz"},
    }


def _lookup_uid(nodes: list, name: str) -> str | None:
    for n in nodes:
        if n.get("name") == name:
            return n.get("uid")
    return None


# --------------------------------------------------------------------------
# F2: relations
# --------------------------------------------------------------------------
OPENING_ROLES = {"window", "door", "opening"}
ARTIFACT_ROLES = {"void", "boundary", "marker"}
EXCLUDE_OCCUPANCY = {
    "floor",
    "ceiling",
    "roof",
    "insulation",
    "boundary",
    "void",
    "marker",
}


def _gap(box_a: dict, box_b: dict) -> float:
    d2 = 0.0
    for ax in "xyz":
        d = max(
            0.0,
            box_b["min"][ax] - box_a["max"][ax],
            box_a["min"][ax] - box_b["max"][ax],
        )
        d2 += d * d
    return math.sqrt(d2)


def _overlap_len(a0, a1, b0, b1) -> float:
    return max(0.0, min(a1, b1) - max(a0, b0))


def _volume(box: dict) -> float:
    return (
        max(0.0, box["max"]["x"] - box["min"]["x"])
        * max(0.0, box["max"]["y"] - box["min"]["y"])
        * max(0.0, box["max"]["z"] - box["min"]["z"])
    )


def _inside(inner: dict, outer: dict, tol: float = 1.0) -> bool:
    return all(
        inner["min"][ax] >= outer["min"][ax] - tol
        and inner["max"][ax] <= outer["max"][ax] + tol
        for ax in "xyz"
    )


def _ancestor(a: dict, b: dict) -> bool:
    pa = a.get("hierarchy_path") or []
    pb = b.get("hierarchy_path") or []
    return pa[: len(pb)] == pb or pb[: len(pa)] == pa


def _role(n: dict) -> str | None:
    return (n.get("semantics") or {}).get("role")


def _intent(n: dict) -> str | None:
    return (n.get("semantics") or {}).get("intent")


def _plane_overlap(a: dict, b: dict, axes: str) -> float:
    return _overlap_len(
        a["min"][axes[0]], a["max"][axes[0]], b["min"][axes[0]], b["max"][axes[0]]
    ) * _overlap_len(
        a["min"][axes[1]], a["max"][axes[1]], b["min"][axes[1]], b["max"][axes[1]]
    )


def build_relations(nodes: list, clearances: list) -> list:
    solids = [n for n in nodes if _bbox(n)]
    by_uid = {n["uid"]: n for n in solids}
    rels: list = []
    seen: set = set()
    opening_pairs: set = set()

    def add(kind, frm, to, gap, conf, intent, rule, detail, overlap=None):
        key = (kind, frm, to)
        if key in seen:
            return
        seen.add(key)
        rel = {
            "id": f"rel-{frm}-{to}-{kind}",
            "kind": kind,
            "from": frm,
            "to": to,
            "gap_mm": None if gap is None else round(gap, 1),
            "overlap_mm3": None if overlap is None else round(overlap, 1),
            "confidence": conf,
            "rule_id": rule,
            "intent": intent,
            "evidence": {"detail": detail},
        }
        rels.append(rel)

    # opening_in: thin slab inside/next to an envelope plane (wall, insulation)
    for op in solids:
        if _role(op) not in OPENING_ROLES:
            continue
        ob = _bbox(op)
        size = (op.get("spatial") or {}).get("size_mm") or {}
        thin_axis = min("xyz", key=lambda ax: size.get(ax, 0))
        if size.get(thin_axis, 0) > 50.0:
            continue
        plane = "".join(ax for ax in "xyz" if ax != thin_axis)
        best = None
        for env in solids:
            if _role(env) not in STRUCTURAL or env is op or _ancestor(op, env):
                continue
            eb = _bbox(env)
            g = _gap(ob, eb)
            area = _plane_overlap(ob, eb, plane)
            if g > 150.0 or area <= 0.0:
                continue
            score = (area, -g)
            if best is None or score > best[0]:
                best = (score, env, g)
        if best is not None:
            _, env, g = best
            opening_pairs.add(frozenset((op["uid"], env["uid"])))
            add(
                "opening_in",
                op["uid"],
                env["uid"],
                g,
                0.9,
                "CONSTRUCTION",
                "R_OPENING_IN_ENVELOPE",
                f"{op['uid']} plane {plane} in envelope {env['uid']}",
            )

    contained = {
        n["uid"]
        for n in solids
        if any(
            _role(m) not in ARTIFACT_ROLES
            and m is not n
            and _volume(_bbox(m)) > _volume(_bbox(n))
            and _inside(_bbox(n), _bbox(m))
            for m in solids
        )
    }

    uids = sorted(by_uid)
    containments: list = []
    for i, ua in enumerate(uids):
        for ub in uids[i + 1 :]:
            a, b = by_uid[ua], by_uid[ub]
            ba, bb = _bbox(a), _bbox(b)
            ra, rb = _role(a), _role(b)
            pair = frozenset((ua, ub))
            g = _gap(ba, bb)
            ancestor = _ancestor(a, b)

            if pair not in opening_pairs and not ancestor:
                structural_a, structural_b = ra in STRUCTURAL, rb in STRUCTURAL
                if structural_a != structural_b:
                    structural = a if structural_a else b
                    other = b if structural_a else a
                    if (
                        _role(structural) in {"wall", "beam"}
                        and _role(other) not in ARTIFACT_ROLES | {"battery"}
                        and g <= 0.5
                    ):
                        add(
                            "contact",
                            structural["uid"],
                            other["uid"],
                            g,
                            0.99,
                            "CONSTRUCTION"
                            if _intent(other) == "CONSTRUCTION"
                            else "DESIGN_DECISION",
                            "R_CONTACT_BBOX",
                            f"bbox touch gap {round(g, 1)} mm",
                        )

                overlap_xy = _plane_overlap(ba, bb, "xy")
                if overlap_xy > 0.0:
                    for lower, upper in ((a, b), (b, a)):
                        if _role(upper) in STRUCTURAL or _role(upper) in ARTIFACT_ROLES:
                            continue
                        if lower["uid"] in contained or upper["uid"] in contained:
                            continue
                        dz = _bbox(upper)["min"]["z"] - _bbox(lower)["max"]["z"]
                        if 0.0 <= dz <= 1.0:
                            add(
                                "support",
                                lower["uid"],
                                upper["uid"],
                                0.0,
                                0.95,
                                "CONSTRUCTION",
                                "R_SUPPORT_STACK",
                                f"z stack: {lower['uid']} top = {upper['uid']} bottom",
                            )

                if (
                    30.0 <= g <= 250.0
                    and ra not in ARTIFACT_ROLES | OPENING_ROLES
                    and rb not in ARTIFACT_ROLES | OPENING_ROLES
                    and not (ra in STRUCTURAL and rb in STRUCTURAL)
                    and not (ra == "battery" and rb == "battery")
                    and not (
                        _intent(a) == "CONSTRUCTION" and _intent(b) == "CONSTRUCTION"
                    )
                ):
                    add(
                        "adjacency",
                        ua,
                        ub,
                        g,
                        0.9,
                        "DESIGN_DECISION",
                        "R_ADJACENCY_GAP",
                        f"gap {round(g, 1)} mm in 30..250 mm band",
                    )

            if pair not in opening_pairs:
                va, vb = _volume(ba), _volume(bb)
                if va > 0 and vb > 0:
                    inner, outer = (a, b) if va < vb else (b, a)
                    if (
                        _role(inner) not in STRUCTURAL | ARTIFACT_ROLES
                        and _role(outer) not in STRUCTURAL | ARTIFACT_ROLES
                        and _inside(_bbox(inner), _bbox(outer))
                        and _volume(_bbox(inner)) / _volume(_bbox(outer)) <= 0.95
                    ):
                        containments.append((inner, outer))

    groups: dict = {}
    for inner, outer in containments:
        groups.setdefault((outer["uid"], _role(inner)), []).append(inner["uid"])
    for (outer_uid, label), members in sorted(groups.items()):
        members.sort()
        rep = by_uid[members[0]]
        count = len(members)
        add(
            "containment",
            members[0],
            outer_uid,
            _gap(_bbox(rep), _bbox(by_uid[outer_uid])),
            0.9,
            "CONSTRUCTION" if _intent(rep) == "CONSTRUCTION" else "DESIGN_DECISION",
            "R_CONTAINMENT_BBOX",
            f"{count} x {label} inside {outer_uid}",
        )

    for c in clearances:
        if c.get("gap_mm") is None:
            continue
        add(
            "proximity",
            c["a"],
            c["b"],
            float(c["gap_mm"]),
            0.95,
            "DESIGN_DECISION",
            c.get("rule_id", "R_CLEARANCE_MIN"),
            f"clearance rule: min {c.get('min_mm')} mm, ok={c.get('ok')}",
        )

    rels.sort(key=lambda r: (r["kind"], r["from"], r["to"]))
    return rels


def normalize_validation(doc: dict) -> dict:
    nodes = doc.get("nodes", [])
    src = doc.get("validation") or {}
    issues = []
    for raw in src.get("issues") or []:
        code = raw.get("code", "UNKNOWN")
        rule_id, rule_class, conf, intent = ISSUE_META.get(code, ISSUE_DEFAULT)
        uid = raw.get("uid")
        issues.append(
            {
                "code": code,
                "severity": SEVERITY_MAP.get(raw.get("severity"), "INFO"),
                "rule_id": rule_id,
                "rule_class": rule_class,
                "confidence": conf,
                "message": raw.get("message", ""),
                "intent": intent,
                "traceable_context": {"uids": [uid] if uid else []},
            }
        )

    counts = src.get("counts") or {}
    norm_counts = {
        "nodes": int(counts.get("nodes", len(nodes))),
        "with_bbox": int(counts.get("with_bbox", sum(1 for n in nodes if _bbox(n)))),
        "errors": int(counts.get("errors", 0)),
        "warnings": int(counts.get("warnings", 0)),
        "critical": int(counts.get("errors", 0)),
        "info": sum(1 for i in issues if i["severity"] == "INFO"),
        "suppressed": 0,
    }

    z_table = []
    for row in src.get("z_table") or []:
        z_table.append(
            {
                "uid": row["uid"],
                "name": row["name"],
                "z_min": row["z_min"],
                "z_max": row["z_max"],
                "height": row["height"],
            }
        )

    clearances = []
    for c in src.get("clearances") or []:
        a_uid = _lookup_uid(nodes, c.get("a_name"))
        b_uid = _lookup_uid(nodes, c.get("b_name"))
        if not a_uid or not b_uid:
            continue
        clearances.append(
            {
                "a": a_uid,
                "b": b_uid,
                "gap_mm": c.get("gap_mm"),
                "min_mm": c.get("min_mm"),
                "ok": bool(c.get("ok")),
                "rule_id": "R_CLEARANCE_MIN",
            }
        )

    return {
        "checked_at": src.get("checked_at"),
        "rules_version": "2.0",
        "counts": norm_counts,
        "issues": issues,
        "suppressions": [],
        "_clearances": clearances,
        "_z_table": z_table,
    }


def _free_space(nodes: list, grid: float = 100.0) -> dict:
    role = lambda n: (n.get("semantics") or {}).get("role")  # noqa: E731
    intent = lambda n: (n.get("semantics") or {}).get("intent")  # noqa: E731

    floor_box = _union([_bbox(n) for n in nodes if role(n) == "floor" and _bbox(n)])
    if floor_box is None:
        floor_box = _union(
            [_bbox(n) for n in nodes if _bbox(n) and role(n) not in EXCLUDE_OCCUPANCY]
        )
    if floor_box is None:
        return {
            "grid_mm": grid,
            "cells_total": 0,
            "cells_free": 0,
            "free_ratio": 0.0,
            "occupied_area_m2": 0.0,
            "passages": [],
        }

    x0, y0 = floor_box["min"]["x"], floor_box["min"]["y"]
    dx = floor_box["max"]["x"] - x0
    dy = floor_box["max"]["y"] - y0
    nx = max(1, math.ceil(dx / grid))
    ny = max(1, math.ceil(dy / grid))

    obstacles = []
    for n in nodes:
        b = _bbox(n)
        if not b or role(n) in EXCLUDE_OCCUPANCY or intent(n) == "ARTIFACT":
            continue
        obstacles.append((b["min"]["x"], b["max"]["x"], b["min"]["y"], b["max"]["y"]))

    occupied = 0
    for i in range(nx):
        cx = x0 + (i + 0.5) * grid
        for j in range(ny):
            cy = y0 + (j + 0.5) * grid
            for mnx, mxx, mny, mxy in obstacles:
                if mnx <= cx <= mxx and mny <= cy <= mxy:
                    occupied += 1
                    break

    total = nx * ny
    free = total - occupied
    return {
        "grid_mm": grid,
        "cells_total": total,
        "cells_free": free,
        "free_ratio": round(free / total, 3),
        "occupied_area_m2": round(occupied * (grid / 1000.0) ** 2, 2),
    }


def _volumes(nodes: list) -> dict:
    by_zone: dict = {}
    for n in nodes:
        if (n.get("semantics") or {}).get("intent") == "ARTIFACT" or not _bbox(n):
            continue
        zone = (n.get("semantics") or {}).get("zone") or "unknown"
        by_zone[zone] = round(
            by_zone.get(zone, 0.0) + _volume(_bbox(n)) / 1_000_000_000.0, 3
        )
    return {"by_zone": by_zone, "note": "bbox sum; containers include children"}


def build_metrics(doc: dict, validation: dict) -> dict:
    nodes = doc.get("nodes", [])
    by_role = {}
    for n in nodes:
        role = (n.get("semantics") or {}).get("role")
        if _bbox(n):
            by_role.setdefault(role, []).append(n)

    env_roles = {"wall", "insulation", "floor", "ceiling", "roof"}
    env = _union(
        [_bbox(n) for n in nodes if (n.get("semantics") or {}).get("role") in env_roles]
    ) or _union([_bbox(n) for n in nodes if _bbox(n)])

    floor_box = _union([_bbox(n) for n in by_role.get("floor", [])])
    ceiling_box = _union([_bbox(n) for n in by_role.get("ceiling", [])])

    if env:
        envelope = {ax: round(env["max"][ax] - env["min"][ax], 1) for ax in "xyz"}
    else:
        envelope = {"x": 0.0, "y": 0.0, "z": 0.0}

    if floor_box:
        floor_area = round(
            (floor_box["max"]["x"] - floor_box["min"]["x"])
            * (floor_box["max"]["y"] - floor_box["min"]["y"])
            / 1_000_000.0,
            2,
        )
    else:
        floor_area = round(envelope["x"] * envelope["y"] / 1_000_000.0, 2)

    if ceiling_box and floor_box:
        clear_height = round(ceiling_box["min"]["z"] - floor_box["max"]["z"], 1)
    elif ceiling_box:
        clear_height = round(ceiling_box["min"]["z"], 1)
    else:
        wall_tops = [_bbox(n)["max"]["z"] for n in by_role.get("wall", [])]
        clear_height = round(min(wall_tops), 1) if wall_tops else envelope["z"]

    clearances = validation.pop("_clearances")
    fs = _free_space(nodes)
    by_uid = {n["uid"]: n for n in nodes}
    passages = []
    for c in clearances:
        if c.get("gap_mm") is None:
            continue
        ra = (by_uid[c["a"]]["semantics"] or {}).get("role")
        rb = (by_uid[c["b"]]["semantics"] or {}).get("role")
        passages.append(
            {
                "name": f"{ra}-{rb}",
                "width_mm": c["gap_mm"],
                "between": [c["a"], c["b"]],
            }
        )
    passages.sort(key=lambda p: (p["width_mm"], p["name"]))
    fs["passages"] = passages

    return {
        "basis": "full",
        "method": "envelope = bbox union of wall/insulation/floor/ceiling/roof; "
        "floor_area = footprint of role=floor; "
        "clear_height = ceiling z_min - floor z_max; "
        "free_space = 100 mm raster over role=floor, any solid except floor/ceiling/"
        "roof/insulation blocks a cell; passages = rule-covered clearance pairs",
        "floor_area_m2": floor_area,
        "clear_height_mm": clear_height,
        "envelope_mm": envelope,
        "z_table": validation.pop("_z_table"),
        "free_space": fs,
        "volumes_m3": _volumes(nodes),
        "clearances": clearances,
        "energy": {
            "kwh_nominal": None,
            "peak_load_kw": None,
            "notes": "DAE channel: no attribute dictionaries, not derivable",
        },
    }


def _fmt(v) -> str:
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return str(v)


def build_brief(doc: dict, metrics: dict, relations: list | None = None) -> dict:
    md = doc.get("project_metadata") or {}
    caps = md.get("capabilities") or {}
    v = doc.get("validation") or {}
    counts = v.get("counts") or {}
    env = metrics.get("envelope_mm") or {}
    fs = metrics.get("free_space") or {}
    missing = [
        k
        for k in ("tags", "metadata_dictionaries", "persistent_ids", "gps")
        if not caps.get(k)
    ]

    lines = [f"# Brief: {md.get('name', '?')}", ""]
    lines.append(
        f"- Envelope {_fmt(env.get('x', 0))} x {_fmt(env.get('y', 0))} mm, "
        f"height {_fmt(env.get('z', 0))} mm; footprint {metrics.get('floor_area_m2', 0)} m2, "
        f"clear height {_fmt(metrics.get('clear_height_mm', 0))} mm."
    )
    lines.append(
        f"- Channel: {((md.get('source') or {}).get('channel') or '?')}. "
        f"Nodes: {counts.get('nodes', 0)} (bbox {counts.get('with_bbox', 0)}); "
        f"errors {counts.get('errors', 0)}, warnings {counts.get('warnings', 0)}."
    )
    if missing:
        lines.append(f"- Unavailable in this channel: {', '.join(missing)}.")
    if fs.get("cells_total"):
        lines.append(
            f"- Free floor: {fs.get('cells_free')} of {fs.get('cells_total')} cells "
            f"({fs.get('free_ratio', 0):.0%}) at {_fmt(fs.get('grid_mm', 0))} mm grid."
        )
        passages = fs.get("passages") or []
        if passages:
            p = passages[0]
            lines.append(
                f"- Narrowest passage: {_fmt(p['width_mm'])} mm ({p['name']})."
            )
    if relations:
        kinds: dict = {}
        for r in relations:
            kinds[r["kind"]] = kinds.get(r["kind"], 0) + 1
        summary = ", ".join(f"{k} {kinds[k]}" for k in sorted(kinds))
        lines.append(f"- Topology: {len(relations)} relations ({summary}).")

    clearances = metrics.get("clearances") or []
    if clearances:
        lines.append("")
        lines.append("## Clearances")
        for c in clearances:
            verdict = "OK" if c.get("ok") else "FAIL"
            lines.append(
                f"- {c['a']} / {c['b']}: gap {_fmt(c.get('gap_mm', 0))} mm "
                f"(min {_fmt(c.get('min_mm', 0))} mm) - {verdict}"
            )

    findings = [
        i for i in v.get("issues") or [] if i.get("severity") in {"CRITICAL", "WARNING"}
    ]
    lines.append("")
    lines.append(f"## Findings: {len(findings)}")
    for i in findings:
        uid = (i.get("traceable_context") or {}).get("uids") or []
        where = f" [{uid[0]}]" if uid else ""
        lines.append(f"- [{i['severity']}] {i['code']}{where} - {i['message']}")
    if not findings:
        lines.append("- No critical or warning issues.")

    markdown = "\n".join(lines) + "\n"
    if len(markdown) > BRIEF_MAX_CHARS:
        markdown = markdown[: BRIEF_MAX_CHARS - 4].rstrip() + "\n...\n"

    top = findings[:TOP_FINDINGS_MAX]
    return {
        "version": "1.0",
        "markdown": markdown,
        "facts": {
            "dimensions_mm": {
                "x": env.get("x", 0),
                "y": env.get("y", 0),
                "z": env.get("z", 0),
            },
            "floor_area_m2": metrics.get("floor_area_m2", 0),
            "clear_height_mm": metrics.get("clear_height_mm", 0),
            "free_ratio": fs.get("free_ratio"),
            "narrowest_passage_mm": (
                fs.get("passages")[0]["width_mm"] if fs.get("passages") else None
            ),
            "relations": len(relations or []),
            "nodes": counts.get("nodes", 0),
            "with_bbox": counts.get("with_bbox", 0),
            "errors": counts.get("errors", 0),
            "warnings": counts.get("warnings", 0),
            "channel": (md.get("source") or {}).get("channel"),
            "missing_capabilities": missing,
        },
        "top_findings": [
            {
                "code": i["code"],
                "severity": i["severity"],
                "uid": ((i.get("traceable_context") or {}).get("uids") or [None])[0],
                "message": i["message"],
            }
            for i in top
        ],
    }


def file_hash(path: str | Path) -> str:
    digest = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    return f"sha256:{digest}"


def _project_rules() -> dict:
    path = Path(__file__).with_name("cad2llm_rules.json")
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def has_validation(doc: dict) -> bool:
    """True when the v4 document already carries usable validate --out output."""
    v = doc.get("validation") or {}
    return (
        bool(v.get("counts"))
        and isinstance(v.get("z_table"), list)
        and isinstance(v.get("clearances"), list)
    )


def enrich(doc: dict, source_hash: str | None = None, basis: str = "full") -> dict:
    """v4 document -> v5 document (deterministic, deep-copies input)."""
    out = copy.deepcopy(doc)
    out["schema_version"] = SCHEMA_VERSION

    meta = out.setdefault("project_metadata", {})
    meta.pop("unit_source", None)
    source = meta.setdefault("source", {})
    if source_hash:
        source["model_hash"] = source_hash
    meta.setdefault("global_origin_gps", None)

    if not has_validation(out):
        import cad2llm_core

        out["validation"] = cad2llm_core.validate(out, _project_rules())
        # Keep enrich deterministic: no wall clock in the auto-validation path.
        out["validation"]["checked_at"] = (
            meta.get("export_date") or "1970-01-01T00:00:00Z"
        )

    for node in out.get("nodes", []):
        node["semantics"] = classify(
            node.get("name", ""), node.get("spatial"), node.get("metadata")
        )
        node.setdefault("metadata", {})

    _apply_semantic_fallbacks(out.get("nodes", []))

    out["relations"] = []
    validation = normalize_validation(out)
    out["validation"] = validation
    out["relations"] = build_relations(out["nodes"], validation["_clearances"])
    out["metrics"] = build_metrics(out, validation)
    out["metrics"]["basis"] = basis
    out["brief"] = build_brief(out, out["metrics"], out["relations"])
    return out


def write_json(path: str | Path, data: dict) -> None:
    Path(path).write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
