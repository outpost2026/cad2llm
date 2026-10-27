"""Deterministic v4/v5 -> LLM handoff document (export_tier cad2llm/slim/v1).

Slim is a **transform**, not an enrich. It preserves topology (``relations``),
summaries (``brief``, ``metrics`` without ``z_table``), node identity + bbox +
semantics, and centralizes definitions outside instances.

Behavior by input schema
------------------------
* v5 (enriched): keeps ``relations``, ``metrics``, ``brief``.
* v4 (raw export): those blocks are absent in the input and therefore absent
  in the output. Run ``cad2llm.py enrich`` before ``slim`` for a full handoff.

Configuration
-------------
* ``geometry_mode``: ``drop`` (default) | ``summary`` | ``keep``.
* ``rotation_mode``: ``quaternion`` (default) | ``euler`` | ``both``.
* ``keep_domain_dicts``: preserve ``energy``, ``construction``, ``opening``,
  ``furniture``, ``space``, ``zone``, ``thermal``, ``safety``.
* ``keep_original_names``: keep ``metadata.cad2llm.original_name`` on nodes.
* ``keep_source_version``: keep ``project_metadata.source.sketchup_version``.

CLI
---
    python tools/cad2llm_slim.py INPUT.json OUTPUT.json
    python tools/cad2llm_slim.py in.json out.json --geometry summary --compact
    python tools/cad2llm_slim.py in.json out.json --keep-domain-dicts --print-stats
    python tools/cad2llm_slim.py - - < in.json > out.json       # stdin -> stdout
    python tools/cad2llm_slim.py --help

Exit codes: 0 = ok, 1 = runtime error, 2 = usage error.
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path
from typing import Any

EXPORT_TIER = "cad2llm/slim/v1"

CAD2LLM_NODE_KEYS = ("role", "zone", "system", "nomenclature", "source", "confidence")
IFC_KEYS = ("type", "schema", "source")
DOMAIN_KEYS = (
    "energy",
    "construction",
    "opening",
    "furniture",
    "space",
    "zone",
    "thermal",
    "safety",
)
NODE_IDENTITY_KEYS = (
    "uid",
    "persistent_id",
    "name",
    "definition_name",
    "entity_type",
    "tag",
    "parent_uid",
)

GEOMETRY_MODES = ("drop", "summary", "keep")
ROTATION_MODES = ("quaternion", "euler", "both")

# Repo root (so tools/cad2llm_slim.py can be run from anywhere).
REPO_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Predicates
# ---------------------------------------------------------------------------


def _quat_identity(quat: dict) -> bool:
    return (
        abs(float(quat.get("w", 1.0)) - 1.0) < 1e-6
        and abs(float(quat.get("x", 0.0))) < 1e-6
        and abs(float(quat.get("y", 0.0))) < 1e-6
        and abs(float(quat.get("z", 0.0))) < 1e-6
    )


def _rot_identity(rot: dict) -> bool:
    return all(abs(float(rot.get(axis, 0.0))) < 1e-6 for axis in ("x", "y", "z"))


def _sorted_dict(d: dict) -> dict:
    return {k: d[k] for k in sorted(d)}


# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------


def slim_metadata(
    meta: dict | None,
    *,
    keep_domain_dicts: bool = False,
    keep_original_names: bool = False,
) -> dict:
    """Whitelist ``cad2llm`` + ``ifc`` keys; optionally keep domain dicts."""
    if not meta:
        return {}

    out: dict[str, Any] = {}

    cad = meta.get("cad2llm")
    if isinstance(cad, dict):
        keys = CAD2LLM_NODE_KEYS + (("original_name",) if keep_original_names else ())
        slim_c = {k: cad[k] for k in keys if k in cad}
        if slim_c:
            out["cad2llm"] = slim_c

    ifc = meta.get("ifc")
    if isinstance(ifc, dict):
        slim_ifc = {k: ifc[k] for k in IFC_KEYS if k in ifc}
        if slim_ifc:
            out["ifc"] = slim_ifc

    if keep_domain_dicts:
        for key in sorted(DOMAIN_KEYS):
            if key not in meta:
                continue
            dom = meta[key]
            if isinstance(dom, dict):
                out[key] = _sorted_dict(copy.deepcopy(dom))
            else:
                out[key] = copy.deepcopy(dom)

    return out


# ---------------------------------------------------------------------------
# Spatial
# ---------------------------------------------------------------------------


def slim_spatial(
    spatial: dict | None,
    *,
    rotation_mode: str = "quaternion",
) -> dict | None:
    """Keep ``bbox_mm``; store exactly one rotation representation (or both)."""
    if not spatial:
        return None
    if rotation_mode not in ROTATION_MODES:
        raise ValueError(f"unknown rotation_mode {rotation_mode!r}")

    out: dict[str, Any] = {}

    bbox = spatial.get("bbox_mm")
    if bbox:
        out["bbox_mm"] = copy.deepcopy(bbox)

    quat = spatial.get("quaternion")
    rot = spatial.get("rotation_deg")
    has_quat = isinstance(quat, dict) and not _quat_identity(quat)
    has_rot = isinstance(rot, dict) and not _rot_identity(rot)

    if rotation_mode == "quaternion":
        if has_quat:
            out["quaternion"] = copy.deepcopy(quat)
        elif has_rot:
            out["rotation_deg"] = copy.deepcopy(rot)
    elif rotation_mode == "euler":
        if has_rot:
            out["rotation_deg"] = copy.deepcopy(rot)
        elif has_quat:
            out["quaternion"] = copy.deepcopy(quat)
    else:  # both
        if has_quat:
            out["quaternion"] = copy.deepcopy(quat)
        if has_rot:
            out["rotation_deg"] = copy.deepcopy(rot)

    return out or None


# ---------------------------------------------------------------------------
# Semantics
# ---------------------------------------------------------------------------


def slim_semantics(sem: dict | None) -> dict | None:
    """Keep role/zone/intent/confidence; reduce evidence to sorted rule_ids."""
    if not sem:
        return None

    out: dict[str, Any] = {}
    for key in ("role", "zone", "intent", "confidence"):
        if key in sem:
            out[key] = sem[key]

    rule_ids = sorted(
        {
            str(item["rule_id"])
            for item in (sem.get("evidence") or [])
            if isinstance(item, dict) and item.get("rule_id")
        }
    )
    if rule_ids:
        out["evidence_rules"] = rule_ids

    return out or None


# ---------------------------------------------------------------------------
# Geometry
# ---------------------------------------------------------------------------


def slim_geometry(geometry: dict | None, mode: str) -> dict | None:
    """``drop`` | ``summary`` (status+counts+hash+volume) | ``keep``."""
    if not geometry or mode == "drop":
        return None
    if mode == "keep":
        return copy.deepcopy(geometry)

    summary: dict[str, Any] = {
        "status": geometry.get("status"),
        "counts": copy.deepcopy(geometry.get("counts") or {}),
    }
    if geometry.get("hash"):
        summary["hash"] = geometry["hash"]
    if geometry.get("volume_mm3") is not None:
        summary["volume_mm3"] = geometry["volume_mm3"]
    return summary


# ---------------------------------------------------------------------------
# Relations / metrics / validation
# ---------------------------------------------------------------------------


def slim_relation(rel: dict) -> dict:
    """Drop ``id`` megastring and ``evidence`` template; keep rule_id."""
    out: dict[str, Any] = {
        "kind": rel["kind"],
        "from": rel["from"],
        "to": rel["to"],
    }
    for key in ("gap_mm", "overlap_mm3", "confidence", "rule_id", "intent"):
        if key in rel and rel[key] is not None:
            out[key] = rel[key]
    return out


def slim_metrics(metrics: dict | None) -> dict | None:
    """Drop ``z_table`` (projection of bbox) and ``clearances`` (duplicated in relations)."""
    if not metrics:
        return None
    drop = {"z_table", "clearances"}
    return {k: copy.deepcopy(v) for k, v in metrics.items() if k not in drop}


def slim_validation(validation: dict | None) -> dict | None:
    if not validation:
        return None
    out: dict[str, Any] = {}
    for key in ("checked_at", "rules_version", "counts", "issues", "suppressions"):
        if key in validation:
            out[key] = copy.deepcopy(validation[key])
    return out or None


# ---------------------------------------------------------------------------
# Project metadata
# ---------------------------------------------------------------------------


def slim_project_metadata(
    meta: dict | None,
    *,
    keep_source_version: bool = False,
) -> dict:
    """Drop GPS, capabilities, absolute model_path, IfcGUIDs."""
    if not meta:
        return {}

    out: dict[str, Any] = {}
    for key in ("name", "units", "up_axis", "export_date"):
        if key in meta:
            out[key] = meta[key]

    source = meta.get("source") or {}
    slim_source: dict[str, Any] = {}
    if source.get("channel"):
        slim_source["channel"] = source["channel"]
    if source.get("file"):
        slim_source["file"] = source["file"]
    if source.get("model_hash"):
        slim_source["model_hash"] = source["model_hash"]
    if keep_source_version and source.get("sketchup_version"):
        slim_source["sketchup_version"] = source["sketchup_version"]
    if slim_source:
        out["source"] = slim_source

    md = meta.get("metadata") or {}
    cad = md.get("cad2llm") if isinstance(md, dict) else None
    if isinstance(cad, dict) and cad:
        out["metadata"] = {"cad2llm": _sorted_dict(copy.deepcopy(cad))}

    return out


# ---------------------------------------------------------------------------
# Definitions
# ---------------------------------------------------------------------------


def build_definitions(
    nodes: list[dict],
    *,
    keep_domain_dicts: bool = False,
) -> list[dict]:
    """Deduplicate per ``definition_name``, sorted by name."""
    by_name: dict[str, dict] = {}
    for node in nodes:
        name = node.get("definition_name")
        if not name or name in by_name:
            continue

        entry: dict[str, Any] = {"name": name}
        desc = node.get("definition_description")
        if desc:
            entry["description"] = desc

        meta = slim_metadata(
            node.get("metadata"),
            keep_domain_dicts=keep_domain_dicts,
            keep_original_names=False,
        )
        if meta:
            entry["metadata"] = meta

        by_name[name] = entry

    return [by_name[k] for k in sorted(by_name)]


# ---------------------------------------------------------------------------
# Node / document
# ---------------------------------------------------------------------------


def slim_node(
    node: dict,
    *,
    geometry_mode: str = "drop",
    rotation_mode: str = "quaternion",
    keep_domain_dicts: bool = False,
    keep_original_names: bool = False,
) -> dict:
    out: dict[str, Any] = {}

    for key in NODE_IDENTITY_KEYS:
        if key in node:
            out[key] = node[key]

    spatial = slim_spatial(node.get("spatial"), rotation_mode=rotation_mode)
    if spatial:
        out["spatial"] = spatial

    sem = slim_semantics(node.get("semantics"))
    if sem:
        out["semantics"] = sem

    meta = slim_metadata(
        node.get("metadata"),
        keep_domain_dicts=keep_domain_dicts,
        keep_original_names=keep_original_names,
    )
    if meta:
        out["metadata"] = meta

    geom = slim_geometry(node.get("geometry"), geometry_mode)
    if geom:
        out["geometry"] = geom

    return out


def slim_document(
    doc: dict,
    *,
    geometry_mode: str = "drop",
    rotation_mode: str = "quaternion",
    keep_domain_dicts: bool = False,
    keep_original_names: bool = False,
    keep_source_version: bool = False,
) -> dict:
    """Return a deep-copied LLM handoff view of a v4 or v5 export."""
    if geometry_mode not in GEOMETRY_MODES:
        raise ValueError(f"unknown geometry_mode {geometry_mode!r}")
    if rotation_mode not in ROTATION_MODES:
        raise ValueError(f"unknown rotation_mode {rotation_mode!r}")

    src_nodes = doc.get("nodes") or []
    definitions = build_definitions(src_nodes, keep_domain_dicts=keep_domain_dicts)
    nodes = [
        slim_node(
            n,
            geometry_mode=geometry_mode,
            rotation_mode=rotation_mode,
            keep_domain_dicts=keep_domain_dicts,
            keep_original_names=keep_original_names,
        )
        for n in src_nodes
    ]

    out: dict[str, Any] = {
        "schema_version": doc.get("schema_version", "4.0"),
        "export_tier": EXPORT_TIER,
        "export_profile": doc.get("export_profile", "spatial"),
        "geometry_mode": geometry_mode,
        "project_metadata": slim_project_metadata(
            doc.get("project_metadata"),
            keep_source_version=keep_source_version,
        ),
        "definitions": definitions,
        "nodes": nodes,
    }

    if doc.get("relations"):
        out["relations"] = [slim_relation(r) for r in doc["relations"]]

    metrics = slim_metrics(doc.get("metrics"))
    if metrics:
        out["metrics"] = metrics

    validation = slim_validation(doc.get("validation"))
    if validation:
        out["validation"] = validation

    if doc.get("brief"):
        out["brief"] = copy.deepcopy(doc["brief"])

    return out


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------


def serialize(doc: dict, *, compact: bool = False) -> str:
    if compact:
        return json.dumps(doc, ensure_ascii=False, separators=(",", ":")) + "\n"
    return json.dumps(doc, indent=2, ensure_ascii=False) + "\n"


def write_json(path: str | Path, doc: dict, *, compact: bool = False) -> None:
    Path(path).write_text(serialize(doc, compact=compact), encoding="utf-8")


# ---------------------------------------------------------------------------
# Diagnostics (for CLI reporting)
# ---------------------------------------------------------------------------


def slim_stats(source: dict, slim: dict) -> dict:
    """Small summary for CLI reporting — not part of the slim schema."""
    src_nodes = source.get("nodes") or []
    unique_defs = {
        n.get("definition_name")
        for n in src_nodes
        if n.get("definition_name")
    }
    return {
        "nodes_in": len(src_nodes),
        "nodes_out": len(slim.get("nodes") or []),
        "definitions": len(slim.get("definitions") or []),
        "unique_definitions_in": len(unique_defs),
        "relations_in": len(source.get("relations") or []),
        "relations_out": len(slim.get("relations") or []),
        "has_metrics": bool(slim.get("metrics")),
        "has_brief": bool(slim.get("brief")),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _resolve(path: str) -> Path:
    """Resolve a path against CWD, falling back to REPO_ROOT if relative."""
    p = Path(path).expanduser()
    if p.is_absolute():
        return p
    if p.exists():
        return p
    candidate = REPO_ROOT / p
    if candidate.exists():
        return candidate
    return p  # let caller fail with a clear message


def _read_doc(path: str) -> dict:
    if path == "-":
        return json.loads(sys.stdin.read())
    real = _resolve(path)
    if not real.exists():
        raise FileNotFoundError(f"input not found: {path} (tried CWD and {REPO_ROOT})")
    return json.loads(real.read_text(encoding="utf-8"))


def _write_doc(path: str, doc: dict, *, compact: bool) -> None:
    if path == "-":
        sys.stdout.write(serialize(doc, compact=compact))
        return
    real = Path(path)
    if not real.is_absolute():
        real = Path.cwd() / real
    real.parent.mkdir(parents=True, exist_ok=True)
    write_json(real, doc, compact=compact)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="cad2llm_slim",
        description=(
            "Deterministic v4/v5 -> LLM handoff (export_tier cad2llm/slim/v1). "
            "Transforms, does not enrich. Run `cad2llm.py enrich` first for "
            "relations/metrics/brief."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  python tools/cad2llm_slim.py in.json out.json\n"
            "  python tools/cad2llm_slim.py in.json out.json --geometry summary --compact\n"
            "  python tools/cad2llm_slim.py in.json out.json --keep-domain-dicts --print-stats\n"
            "  python tools/cad2llm_slim.py - - < in.json > out.json\n"
        ),
    )
    p.add_argument("input", help="input v4/v5 JSON ('-' for stdin)")
    p.add_argument("output", help="output slim JSON ('-' for stdout)")
    p.add_argument(
        "--geometry",
        dest="geometry_mode",
        choices=GEOMETRY_MODES,
        default="drop",
        help="geometry handling (default: drop)",
    )
    p.add_argument(
        "--rotation",
        dest="rotation_mode",
        choices=ROTATION_MODES,
        default="quaternion",
        help="rotation representation (default: quaternion)",
    )
    p.add_argument(
        "--keep-domain-dicts",
        action="store_true",
        help="keep energy/construction/opening/furniture/space/zone/thermal/safety",
    )
    p.add_argument(
        "--keep-original-names",
        action="store_true",
        help="keep metadata.cad2llm.original_name on nodes",
    )
    p.add_argument(
        "--keep-source-version",
        action="store_true",
        help="keep project_metadata.source.sketchup_version",
    )
    p.add_argument(
        "--compact",
        action="store_true",
        help="emit minified JSON (no whitespace)",
    )
    p.add_argument(
        "--print-stats",
        action="store_true",
        help="print slim_stats() to stderr after writing",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="parse + slim, print stats, do not write output",
    )
    return p


def cmd_slim(args: argparse.Namespace) -> int:
    try:
        src = _read_doc(args.input)
    except FileNotFoundError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except json.JSONDecodeError as e:
        print(f"error: invalid JSON in {args.input}: {e}", file=sys.stderr)
        return 2

    try:
        slim = slim_document(
            src,
            geometry_mode=args.geometry_mode,
            rotation_mode=args.rotation_mode,
            keep_domain_dicts=args.keep_domain_dicts,
            keep_original_names=args.keep_original_names,
            keep_source_version=args.keep_source_version,
        )
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    stats = slim_stats(src, slim)

    if args.dry_run:
        print(f"dry-run: would write {args.output}", file=sys.stderr)
    else:
        try:
            _write_doc(args.output, slim, compact=args.compact)
        except OSError as e:
            print(f"error: cannot write {args.output}: {e}", file=sys.stderr)
            return 1

    if args.print_stats or args.dry_run:
        print(
            "stats: "
            f"nodes {stats['nodes_in']}->{stats['nodes_out']}, "
            f"definitions {stats['definitions']}, "
            f"relations {stats['relations_in']}->{stats['relations_out']}, "
            f"metrics={'yes' if stats['has_metrics'] else 'no'}, "
            f"brief={'yes' if stats['has_brief'] else 'no'}",
            file=sys.stderr,
        )

    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    return cmd_slim(args)


if __name__ == "__main__":
    sys.exit(main())