#!/usr/bin/env python3
"""Derive a CAD2LLM annotation plan from an existing export.

The plan is a skeleton for a new project: tag = role, cad2llm metadata,
IFC 2x3 type from the shared vocabulary and (optionally) a template
description. Only values that differ from the export are emitted, so the
result is idempotent and reviewable before `apply`.

Usage:
    python tools/cad2llm_derive.py export.json --out plan.json
    python tools/cad2llm_derive.py export.json --names --descriptions
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import cad2llm_v5 as v5
import cad2llm_vocab as vocab

SCHEMA = "cad2llm/annotate_plan/v1"
IFC_SCHEMA = "IFC 2x3"
GENERIC_NAMES = {"unnamed", "group", "component", "sketchup_model"}
SAFE_NAME = re.compile(r"^[A-Za-z0-9_.-]+$")


def _is_generic(name: str) -> bool:
    clean = (name or "").strip().lower()
    return not clean or clean in GENERIC_NAMES or clean.startswith("component#")


def _desired_name(node: dict, role: str, index: int) -> str:
    current = (node.get("name") or "").strip()
    if current and not _is_generic(current) and SAFE_NAME.match(current):
        return current
    return f"{role}_{index:02d}"


def _description(role: str, zone: str, ifc: str, v: dict) -> str:
    info = vocab.role_info(role, v) or {}
    label = info.get("label_cs") or role
    return f"{label} · zóna {zone} · {ifc}"


def derive_plan(
    doc: dict,
    rules: dict | None = None,
    *,
    vocab_data: dict | None = None,
    names: bool = False,
    descriptions: bool = False,
    overwrite: bool = False,
) -> dict:
    v = vocab_data if vocab_data is not None else vocab.load_vocab(rules)
    operations = []
    unresolved = []
    seen_descriptions = set()

    for index, node in enumerate(doc.get("nodes") or [], start=1):
        pid = node.get("persistent_id")
        if pid is None:
            continue
        cad = ((node.get("metadata") or {}).get("cad2llm")) or {}
        ifc = ((node.get("metadata") or {}).get("ifc")) or {}
        current_role = cad.get("role")
        current_zone = cad.get("zone")
        current_system = cad.get("system")

        role = current_role if current_role in vocab.roles(v) else None
        if role is None:
            role = vocab.tag_role(node.get("tag"), v)
        if role is None:
            role = v5.classify(node.get("name", ""), node.get("spatial"))["role"]
        if role in (None, "unknown") or role not in vocab.roles(v):
            unresolved.append({"pid": pid, "name": node.get("name"), "reason": "role"})
            continue

        info = vocab.role_info(role, v) or {}
        zone = current_zone if current_zone not in (None, "unknown") else None
        if zone is None:
            zone = info.get("zone") or v5.classify(node.get("name", ""))["zone"]
        system = current_system or info.get("system")
        ifc_type = info.get("ifc")

        target = {"persistent_id": pid}

        if names:
            desired = _desired_name(node, role, index)
            if desired != node.get("name"):
                operations.append({"op": "rename", "target": target, "name": desired})

        tag_mapping = vocab.tag_role(node.get("tag"), v)
        if tag_mapping != role and node.get("tag") != role:
            operations.append({"op": "set_tag", "target": target, "tag": role})

        def attr(dict_name: str, key: str, wanted, current):
            if wanted is None:
                return
            if current == wanted:
                return
            if current not in (None, "", "unknown") and not overwrite:
                return
            operations.append(
                {
                    "op": "set_attribute",
                    "target": target,
                    "dict": dict_name,
                    "key": key,
                    "value": wanted,
                }
            )

        attr("cad2llm", "role", role, current_role)
        attr("cad2llm", "zone", zone, current_zone)
        attr("cad2llm", "system", system, current_system)
        attr("ifc", "type", ifc_type, ifc.get("type"))
        attr("ifc", "schema", IFC_SCHEMA, ifc.get("schema"))
        attr("ifc", "source", "definition", ifc.get("source"))

        if descriptions and ifc_type:
            wanted_text = _description(role, zone or "unknown", ifc_type, v)
            current_text = node.get("definition_description")
            if (current_text and not overwrite) or current_text == wanted_text:
                continue
            desc_target = (
                {"kind": "definition", "name": node.get("definition_name")}
                if node.get("definition_name")
                else target
            )
            desc_key = (
                desc_target.get("kind", "entity"),
                desc_target.get("name") or pid,
            )
            if desc_key not in seen_descriptions:
                seen_descriptions.add(desc_key)
                operations.append(
                    {
                        "op": "set_description",
                        "target": desc_target,
                        "description": wanted_text,
                    }
                )

    plan = {
        "schema": SCHEMA,
        "name": "derived from export",
        "note": "Generated by tools/cad2llm_derive.py. Review before apply; metadata-only.",
        "transaction": "single",
        "operations": operations,
    }
    return {
        "plan": plan,
        "summary": _summary(doc, operations, unresolved),
        "unresolved": unresolved,
    }


def _summary(doc: dict, operations: list, unresolved: list) -> dict:
    kinds: dict = {}
    for op in operations:
        kinds[op["op"]] = kinds.get(op["op"], 0) + 1
    return {
        "nodes": len(doc.get("nodes") or []),
        "operations": len(operations),
        "by_op": dict(sorted(kinds.items())),
        "unresolved": len(unresolved),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Derive an annotation plan from a CAD2LLM export"
    )
    parser.add_argument("doc")
    parser.add_argument("--out", default=None)
    parser.add_argument("--rules", default=None)
    parser.add_argument(
        "--vocab", default=None, help="project vocabulary overlay (JSON)"
    )
    parser.add_argument(
        "--names", action="store_true", help="rename generic/unsafe names to role_NN"
    )
    parser.add_argument(
        "--descriptions", action="store_true", help="emit template descriptions"
    )
    parser.add_argument(
        "--overwrite", action="store_true", help="replace existing non-empty values"
    )
    parser.add_argument(
        "--json", action="store_true", help="print the whole result as JSON"
    )
    args = parser.parse_args(argv)

    rules = None
    if args.rules:
        rules = json.loads(Path(args.rules).read_text(encoding="utf-8"))
    try:
        vocab_data = vocab.load_vocab(rules, overlay=args.vocab)
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    doc = json.loads(Path(args.doc).read_text(encoding="utf-8"))
    result = derive_plan(
        doc,
        rules,
        vocab_data=vocab_data,
        names=args.names,
        descriptions=args.descriptions,
        overwrite=args.overwrite,
    )

    if args.out:
        Path(args.out).write_text(
            json.dumps(result["plan"], indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        s = result["summary"]
        print(
            f"OK  nodes={s['nodes']} operations={s['operations']} unresolved={s['unresolved']} "
            f"by_op={s['by_op']}"
        )
        for item in result["unresolved"]:
            print(
                f"    unresolved pid={item['pid']} name={item['name']!r} ({item['reason']})"
            )
        if args.out:
            print(f"    written {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
