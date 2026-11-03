#!/usr/bin/env python3
"""One command for the metadata loop: export -> gate -> plan -> apply -> verify.

The individual tools (bridge, gate, derive, patch) stay available, but the
repetitive path is a single call:

    python tools/cad2llm_metadata.py run                    # dry-run (default)
    python tools/cad2llm_metadata.py run --apply            # write + verify
    python tools/cad2llm_metadata.py run --learn --apply    # also grow the vocab
    python tools/cad2llm_metadata.py plan export.json       # offline plan only

Design notes:
  * the export is done once (compact spatial by default); a second export only
    happens after a write, for verification
  * the gate patch is a diff, so a clean model costs zero operations
  * values come from the shared vocabulary; nothing is invented here
  * without --apply nothing is written (safe default)

Exit codes: 0 clean, 1 warnings left, 2 blocking/errors, 3 bridge/IO error.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tools" / "pipeline"))

import cad2llm_patch as patch_mod  # noqa: E402
import cad2llm_vocab_learn as vocab_learn  # noqa: E402
import gate  # noqa: E402


def _load(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _registry():
    return gate.Registry(str(gate.DEFAULT_REGISTRY))


def _node_meta(node):
    meta = node.get("metadata") if isinstance(node.get("metadata"), dict) else {}
    return (
        meta.get("cad2llm") if isinstance(meta.get("cad2llm"), dict) else {},
        meta.get("ifc") if isinstance(meta.get("ifc"), dict) else {},
    )


def _current(node, field):
    cad, ifc = _node_meta(node)
    if field == "name":
        return node.get("name")
    dictionary, _, key = field.partition(".")
    return (cad if dictionary == "cad2llm" else ifc).get(key)


def _export_path(profile, compact):
    """Default export location: next to the model, same naming as the GUI."""
    from cad2llm_bridge import BridgeClient

    info = BridgeClient().info()
    model = Path(info["path"])
    suffix = "_geometry" if profile == "geometry" else ""
    suffix += "_compact" if compact else ""
    return model.with_name(model.stem + "_cad2llm_v4" + suffix + ".json")


def _gate(path, reg):
    return gate.run_gate(str(path), reg)


def _plan(res):
    patch, pending = gate.build_patch(res)
    if not patch["commands"]:
        return None, patch, pending
    try:
        plan = patch_mod.patch_to_plan(patch, name="cad2llm metadata diff")
    except patch_mod.EmptyPatch:
        return None, patch, pending
    return plan, patch, pending


def _print_plan(res, plan, patch, pending):
    nodes = {n.get("persistent_id"): n for n in res["_nodes"]}
    ops = plan["operations"] if plan else []
    renames = [o for o in ops if o["op"] == "rename"]
    print(
        "plan  : %d op (%d rename, %d attr)  pending=%d"
        % (len(ops), len(renames), len(ops) - len(renames), len(pending))
    )
    by_pid = {}
    for op in ops:
        pid = op["target"]["persistent_id"]
        row = by_pid.setdefault(pid, {"name": None, "attrs": []})
        if op["op"] == "rename":
            row["name"] = op["name"]
        else:
            row["attrs"].append((op["dict"] + "." + op["key"], op["value"]))
    for pid, row in sorted(by_pid.items()):
        node = nodes.get(pid) or {}
        parts = []
        if row["name"]:
            parts.append("name: %s -> %s" % (node.get("name"), row["name"]))
        for key, value in row["attrs"]:
            parts.append("%s: %s -> %s" % (key, _current(node, key) or "-", value))
        print("        pid=%s %-28s %s" % (pid, node.get("name"), "; ".join(parts)))
    for item in pending:
        fields = ", ".join("%s=%s" % (k, v) for k, v in item["fields"].items())
        print(
            "        PENDING pid=%s %s (%s)"
            % (item["target"].get("persistent_id"), item["question"], fields)
        )


def _summary(res):
    counts = res.get("counts") or {}
    blocking = counts.get("blocking", 0)
    print(
        "gate  : %-5s blocking=%d warning=%d info=%d  input=%s"
        % (
            res["verdict"],
            blocking,
            counts.get("warning", 0),
            counts.get("info", 0),
            res["input_sha256"],
        )
    )
    return blocking


def cmd_plan(args):
    reg = _registry()
    res = _gate(args.export, reg)
    plan, patch, pending = _plan(res)
    _summary(res)
    _print_plan(res, plan, patch, pending)
    if args.out and plan:
        Path(args.out).write_text(
            json.dumps(plan, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        print("wrote : %s" % args.out)
    return 2 if res["verdict"] == "BLOCK" else 0


def cmd_run(args):
    from cad2llm_bridge import BridgeClient

    client = BridgeClient()
    info = client.info()
    print(
        "model : %s  (%d top-level entities)"
        % (info["title"], info["top_level_entities"])
    )

    export_path = (
        Path(args.export) if args.export else _export_path(args.profile, not args.full)
    )
    if not args.export:
        client.export(export_path, profile=args.profile, compact=not args.full)
        print(
            "export: %s (%s%s, %s B)"
            % (
                export_path.name,
                args.profile,
                "" if args.full else " compact",
                export_path.stat().st_size,
            )
        )

    if args.learn:
        reg = _registry()
        stats = vocab_learn.collect_evidence(reg, _load(export_path).get("nodes") or [])
        learned = vocab_learn.plan(
            stats, vocab_learn.load_json(vocab_learn.DEFAULT_RULES).get("vocab") or {}
        )
        if learned["add"]:
            vocab_learn.apply_plan(vocab_learn.DEFAULT_RULES, learned)
            print(
                "vocab : learned %d tag(s): %s"
                % (
                    len(learned["add"]),
                    ", ".join("%s->%s" % (i["tag"], i["role"]) for i in learned["add"]),
                )
            )
        if learned["conflicts"]:
            print(
                "vocab : %d conflict(s), kept for review: %s"
                % (
                    len(learned["conflicts"]),
                    ", ".join(c["tag"] for c in learned["conflicts"]),
                )
            )

    reg = _registry()
    before = _gate(export_path, reg)
    blocking = _summary(before)
    plan, patch, pending = _plan(before)
    _print_plan(before, plan, patch, pending)

    if not plan:
        print("apply : nothing to do")
        return blocking and 2 or 0
    if not args.apply:
        print("apply : dry-run only (use --apply to write)")
        return blocking and 2 or 0

    dry = client.apply(plan, dry_run=True)
    if dry["summary"]["errors"]:
        print("apply : dry-run errors=%d, aborting" % dry["summary"]["errors"])
        return 2
    real = client.apply(plan, dry_run=False)
    s = real["summary"]
    print(
        "apply : applied=%d errors=%d unchanged=%d  (one undo step)"
        % (s["applied"], s["errors"], s["unchanged"])
    )
    if s["errors"]:
        return 2

    audit = client.tag_audit()
    failed_defs = [
        r
        for r in audit.get("definitions_detail") or []
        if r.get("status") == "mismatch"
    ]
    print(
        "audit : instances %d/%d  definitions %d/%d"
        % (
            audit.get("mismatch_instances", 0),
            audit.get("instances", 0),
            len(failed_defs),
            audit.get("definitions", 0),
        )
    )

    client.export(export_path, profile=args.profile, compact=not args.full)
    after = _gate(export_path, reg)
    after_blocking = _summary(after)
    before_sem = sorted(
        i["code"] for i in before["issues"] if i["code"].startswith("SEM_")
    )
    after_sem = sorted(
        i["code"] for i in after["issues"] if i["code"].startswith("SEM_")
    )
    print(
        "sem   : %d -> %d issues (%s)"
        % (
            len(before_sem),
            len(after_sem),
            ", ".join(sorted(set(before_sem) - set(after_sem))) or "no change",
        )
    )
    return 2 if after_blocking else (1 if after["verdict"] == "WARN" else 0)


def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass
    ap = argparse.ArgumentParser(
        description="CAD2LLM metadata loop (export -> gate -> plan -> apply -> verify)"
    )
    sub = ap.add_subparsers(dest="command", required=True)

    p_plan = sub.add_parser(
        "plan", help="offline: gate an export and print the diff plan"
    )
    p_plan.add_argument("export")
    p_plan.add_argument(
        "-o", "--out", default=None, help="write the annotate plan here"
    )
    p_plan.set_defaults(func=cmd_plan)

    p_run = sub.add_parser("run", help="live loop against the open SketchUp model")
    p_run.add_argument(
        "--export", default=None, help="reuse an existing export instead of exporting"
    )
    p_run.add_argument(
        "--profile",
        choices=["spatial", "geometry"],
        default="geometry",
        help="geometry carries the mesh and is the honest verdict; spatial is faster but reports FRAME_UNDECIDABLE",
    )
    p_run.add_argument("--full", action="store_true", help="skip --compact on export")
    p_run.add_argument(
        "--apply", action="store_true", help="write into the model (default: dry-run)"
    )
    p_run.add_argument(
        "--learn",
        action="store_true",
        help="grow vocab.tag_roles from this model first",
    )
    p_run.set_defaults(func=cmd_run)

    args = ap.parse_args(argv)
    try:
        return args.func(args)
    except Exception as exc:  # bridge/IO problems must be readable
        print("error : %s: %s" % (type(exc).__name__, exc))
        return 3


if __name__ == "__main__":
    sys.exit(main())
