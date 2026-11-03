#!/usr/bin/env python3
"""Learn vocabulary entries from model evidence (tag -> role).

The shared vocabulary (tools/cad2llm_rules.json) is the single source of truth
for role/zone/system/IFC. When a model uses a tag the vocabulary does not know,
the role is still recoverable from the model context:

    metadata.cad2llm.role  ->  registry definition  ->  registry tag_map  ->  parent

This tool collects that evidence across one or more exports and proposes a
`vocab.tag_roles` entry for every tag that is missing and whose instances agree.
Conflicting tags are reported, never guessed.

Usage:
    python tools/cad2llm_vocab_learn.py export.json            # dry-run
    python tools/cad2llm_vocab_learn.py export.json --apply    # write vocab
    python tools/cad2llm_vocab_learn.py a.json b.json --json

Exit codes: 0 clean, 1 nothing to learn, 2 conflicts, 3 error.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tools" / "pipeline"))

DEFAULT_RULES = ROOT / "tools" / "cad2llm_rules.json"


def load_json(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def dump_json(path, data):
    """Atomic, style-preserving write (indent 2, UTF-8, trailing newline)."""
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    tmp.write_text(text, encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def _role_of(reg, node):
    meta = node.get("metadata") if isinstance(node.get("metadata"), dict) else {}
    cad = meta.get("cad2llm") if isinstance(meta.get("cad2llm"), dict) else {}
    if cad.get("role"):
        return cad["role"], "metadata"
    _, rec = reg.lookup_definition(node.get("definition_name"))
    if rec and rec.get("role"):
        return rec["role"], "definition"
    rec = reg.lookup_tag(node.get("tag"))
    if rec and rec.get("role"):
        return rec["role"], "tag_map"
    return None, None


def collect_evidence(reg, nodes):
    """tag -> {votes: {role: count}, sources: {role: {source: count}}, instances}."""
    by_uid = {n.get("uid"): n for n in nodes}
    stats: dict = defaultdict(
        lambda: {
            "votes": defaultdict(int),
            "sources": defaultdict(lambda: defaultdict(int)),
            "instances": 0,
        }
    )
    for node in nodes:
        tag = (node.get("tag") or "").strip()
        if not tag:
            continue
        stats[tag]["instances"] += 1
        role, source = _role_of(reg, node)
        if role is None:
            parent = by_uid.get(node.get("parent_uid"))
            if parent:
                role, source = _role_of(reg, parent)
                if role:
                    source = "parent:%s" % source
        if role:
            stats[tag]["votes"][role] += 1
            stats[tag]["sources"][role][source] += 1
    return dict(stats)


def plan(tag_stats, vocab):
    """Split evidence into add / conflict / unknown_role / known buckets."""
    tag_roles = vocab.get("tag_roles") or {}
    roles = vocab.get("roles") or {}
    known, add, conflicts, unknown = [], [], [], []
    for tag in sorted(tag_stats):
        entry = tag_stats[tag]
        if (tag in tag_roles) or (tag.lower() in tag_roles):
            known.append(tag)
            continue
        if not entry["votes"]:
            unknown.append(
                {
                    "tag": tag,
                    "instances": entry["instances"],
                    "reason": "no role evidence",
                }
            )
            continue
        if len(entry["votes"]) > 1:
            conflicts.append(
                {
                    "tag": tag,
                    "instances": entry["instances"],
                    "votes": dict(sorted(entry["votes"].items())),
                }
            )
            continue
        role = next(iter(entry["votes"]))
        if role not in roles:
            unknown.append(
                {
                    "tag": tag,
                    "instances": entry["instances"],
                    "reason": "role %r not in vocab.roles" % role,
                }
            )
            continue
        add.append(
            {
                "tag": tag,
                "role": role,
                "instances": entry["instances"],
                "votes": entry["votes"][role],
                "sources": {k: v for k, v in entry["sources"][role].items()},
            }
        )
    return {"add": add, "conflicts": conflicts, "unknown": unknown, "known": known}


def apply_plan(rules_path, result, stamp=None):
    """Write learned tag_roles into cad2llm_rules.json and return the diff."""
    if not result["add"]:
        return {"written": 0, "rules": str(rules_path)}
    data = load_json(rules_path)
    vocab = data.setdefault("vocab", {})
    tag_roles = vocab.setdefault("tag_roles", {})
    learned = vocab.setdefault("learned", {})
    stamp = stamp or _dt.date.today().isoformat()
    for item in result["add"]:
        tag_roles[item["tag"]] = item["role"]
        learned[item["tag"]] = {
            "role": item["role"],
            "instances": item["instances"],
            "evidence": item["sources"],
            "learned_at": stamp,
            "source": "cad2llm_vocab_learn",
        }
    dump_json(rules_path, data)
    return {"written": len(result["add"]), "rules": str(rules_path), "learned": learned}


def _print(result, json_mode):
    if json_mode:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    print(
        "known=%d  add=%d  conflicts=%d  unresolved=%d"
        % (
            len(result["known"]),
            len(result["add"]),
            len(result["conflicts"]),
            len(result["unknown"]),
        )
    )
    for item in result["add"]:
        print(
            "  + %-24s -> %-12s (%d/%d instances, %s)"
            % (
                item["tag"],
                item["role"],
                item["votes"],
                item["instances"],
                ", ".join(sorted(item["sources"])),
            )
        )
    for item in result["conflicts"]:
        print(
            "  ! %-24s conflict: %s"
            % (item["tag"], json.dumps(item["votes"], ensure_ascii=False))
        )
    for item in result["unknown"]:
        print("  ? %-24s %s" % (item["tag"], item["reason"]))


def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass
    ap = argparse.ArgumentParser(description="Learn vocab tag_roles from model exports")
    ap.add_argument("exports", nargs="+")
    ap.add_argument("--rules", default=str(DEFAULT_RULES))
    ap.add_argument(
        "--registry", default=str(ROOT / "tools" / "pipeline" / "registry.json")
    )
    ap.add_argument(
        "--apply", action="store_true", help="write learned tags into the rules file"
    )
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--stamp", default=None, help="fixed learned_at date (YYYY-MM-DD)")
    args = ap.parse_args(argv)

    import gate  # local import: gate is not a package

    reg = gate.Registry(args.registry)
    stats = {}
    for path in args.exports:
        data = load_json(path)
        for tag, entry in collect_evidence(reg, data.get("nodes") or []).items():
            merged = stats.setdefault(
                tag,
                {
                    "votes": defaultdict(int),
                    "sources": defaultdict(lambda: defaultdict(int)),
                    "instances": 0,
                },
            )
            merged["instances"] += entry["instances"]
            for role, count in entry["votes"].items():
                merged["votes"][role] += count
            for role, sources in entry["sources"].items():
                for source, count in sources.items():
                    merged["sources"][role][source] += count

    vocab = load_json(args.rules).get("vocab") or {}
    result = plan(stats, vocab)
    if args.apply:
        result["applied"] = apply_plan(args.rules, result, args.stamp)
    _print(result, args.json)
    if result["conflicts"]:
        return 2
    if not result["add"] and not args.json:
        print("nothing to learn")
    return 0


if __name__ == "__main__":
    sys.exit(main())
