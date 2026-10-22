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
import re
import unicodedata
from pathlib import Path

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
    "marker": "unknown",
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


def classify(name: str, spatial: dict | None = None) -> dict:
    """Deterministic role/zone/intent for one node. Unknown stays UNKNOWN."""
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

    return {
        "basis": "full",
        "method": "envelope = bbox union of wall/insulation/floor/ceiling/roof; "
        "floor_area = footprint of role=floor; "
        "clear_height = ceiling z_min - floor z_max",
        "floor_area_m2": floor_area,
        "clear_height_mm": clear_height,
        "envelope_mm": envelope,
        "z_table": validation.pop("_z_table"),
        "clearances": validation.pop("_clearances"),
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


def build_brief(doc: dict, metrics: dict) -> dict:
    md = doc.get("project_metadata") or {}
    caps = md.get("capabilities") or {}
    v = doc.get("validation") or {}
    counts = v.get("counts") or {}
    env = metrics.get("envelope_mm") or {}
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

    for node in out.get("nodes", []):
        node["semantics"] = classify(node.get("name", ""), node.get("spatial"))
        node.setdefault("metadata", {})

    out["relations"] = []
    validation = normalize_validation(out)
    out["validation"] = validation
    out["metrics"] = build_metrics(out, validation)
    out["metrics"]["basis"] = basis
    out["brief"] = build_brief(out, out["metrics"])
    return out


def write_json(path: str | Path, data: dict) -> None:
    Path(path).write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
