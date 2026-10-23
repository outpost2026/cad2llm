#!/usr/bin/env python3
"""Invariant drift check for CAD2LLM exports.

Compares a baseline export with a current export on selected fields. The
baseline is the contract: anything that differs is reported, including nodes
that appeared or disappeared. Exit code 3 on drift, 0 when clean.

Used by `python tools/cad2llm_bridge.py verify ...` and by CI.
"""

from __future__ import annotations

import json
from pathlib import Path

DEFAULT_FIELDS = (
    "name",
    "tag",
    "definition_name",
    "definition_description",
    "metadata.cad2llm.role",
    "metadata.cad2llm.zone",
    "metadata.ifc.type",
)

_MISSING = object()


def get_field(node: dict, dotted: str):
    """Read a dotted path from a node; returns _MISSING when absent."""
    value = node
    for part in dotted.split("."):
        if not isinstance(value, dict) or part not in value:
            return _MISSING
        value = value[part]
    return value


def compare_invariants(baseline: dict, current: dict, fields=None) -> dict:
    """Compare two v4 documents and return a deterministic drift report."""
    fields = list(fields or DEFAULT_FIELDS)
    base_nodes = {n.get("persistent_id"): n for n in baseline.get("nodes", [])}
    cur_nodes = {n.get("persistent_id"): n for n in current.get("nodes", [])}

    drift = []
    all_pids = sorted(
        set(base_nodes) | set(cur_nodes), key=lambda p: (p is None, str(p))
    )
    for pid in all_pids:
        if pid not in base_nodes:
            cur_node = cur_nodes[pid]
            drift.append(
                {
                    "pid": pid,
                    "name": cur_node.get("name"),
                    "field": None,
                    "status": "added",
                    "expected": None,
                    "actual": None,
                }
            )
            continue
        if pid not in cur_nodes:
            base_node = base_nodes[pid]
            drift.append(
                {
                    "pid": pid,
                    "name": base_node.get("name"),
                    "field": None,
                    "status": "missing",
                    "expected": None,
                    "actual": None,
                }
            )
            continue
        base_node = base_nodes[pid]
        cur_node = cur_nodes[pid]
        for field in fields:
            base_value = get_field(base_node, field)
            cur_value = get_field(cur_node, field)
            base_value = None if base_value is _MISSING else base_value
            cur_value = None if cur_value is _MISSING else cur_value
            if base_value != cur_value:
                drift.append(
                    {
                        "pid": pid,
                        "name": cur_node.get("name") or base_node.get("name"),
                        "field": field,
                        "status": "changed",
                        "expected": base_value,
                        "actual": cur_value,
                    }
                )

    counts = {
        "baseline": len(base_nodes),
        "current": len(cur_nodes),
        "changed": 0,
        "missing": 0,
        "added": 0,
    }
    for item in drift:
        counts[item["status"]] = counts.get(item["status"], 0) + 1

    return {
        "fields": fields,
        "nodes_compared": len(set(base_nodes) & set(cur_nodes)),
        "counts": counts,
        "drift": drift,
        "ok": not drift,
    }


def load_document(path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def parse_fields(raw):
    if not raw:
        return list(DEFAULT_FIELDS)
    return [part.strip() for part in raw.split(",") if part.strip()]
