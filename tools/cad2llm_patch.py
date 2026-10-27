#!/usr/bin/env python3
"""Semantic patch -> annotate plan, and optional push to the running SketchUp.

The gate (cad2llm_pipeline/gate.py) produces ``<model>.patch.json`` with the
contract ``cad2llm/semantic-patch/v1``: one command per field, carrying the
evidence and the confidence that produced it. The bridge accepts only
``cad2llm/annotate_plan/v1`` (see docs/schema/annotate_plan.schema.json).

This module is the only translation point between the two, and it is strict on
purpose: a field the exporter does not read is an error, not a warning. That is
what keeps the write path from silently producing dead metadata.

Field map (dict, key) is exactly what Exporter#metadata_of dumps into
``node["metadata"][dict][key]``:

    cad2llm.role   -> metadata.cad2llm.role
    cad2llm.system -> metadata.cad2llm.system
    cad2llm.zone   -> metadata.cad2llm.zone
    ifc.type       -> metadata.ifc.type
    name           -> entity name (rename op)

Usage:

    python tools/cad2llm_patch.py check reports/*.patch.json
    python tools/cad2llm_patch.py plan  reports/x.patch.json -o plan.json
    python tools/cad2llm_patch.py apply reports/x.patch.json --dry-run
    python tools/cad2llm_patch.py apply reports/x.patch.json --report applied.json
    python tools/cad2llm_patch.py --selftest

Exit codes: 0 ok, 1 contract error, 2 bridge not reachable, 3 bridge refused.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PLAN_SCHEMA = "cad2llm/annotate_plan/v1"
PATCH_CONTRACT = "cad2llm/semantic-patch/v1"
MAX_OPS = 2000

# field -> (attribute dictionary, key)
FIELD_MAP = {
    "cad2llm.role": ("cad2llm", "role"),
    "cad2llm.system": ("cad2llm", "system"),
    "cad2llm.zone": ("cad2llm", "zone"),
    "ifc.type": ("ifc", "type"),
}
NAME_FIELD = "name"
TARGET_KEYS = (
    "kind",
    "persistent_id",
    "hierarchy_path",
    "name",
    "tag",
    "definition_name",
)
STATUSES = ("auto", "approved")


class PatchError(ValueError):
    """Patch does not map onto the annotate plan contract."""


class EmptyPatch(PatchError):
    """Patch has no applyable commands (everything waits for a human)."""


def _target(command: dict, where: str) -> dict:
    """Only the persistent_id travels to the bridge.

    hierarchy_path and name in the patch are a snapshot of the export. The
    moment one op in the same plan renames a parent, those values go stale and
    the Ruby resolver would veto a perfectly good persistent_id match
    (NO_MATCH_AFTER_FILTER). persistent_id is unique per entity, so nothing else
    is needed. The richer target stays in patch.json for humans and audit.
    """
    raw = command.get("target")
    if not isinstance(raw, dict):
        raise PatchError(f"{where}: target must be an object")
    pid = raw.get("persistent_id")
    if not isinstance(pid, int):
        raise PatchError(f"{where}: target.persistent_id must be an integer")
    target = {"persistent_id": pid}
    if raw.get("kind"):
        target["kind"] = raw["kind"]
    return target


def _scalar(value, where: str):
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, (int, float)):
        return value
    raise PatchError(f"{where}: value must be a JSON scalar")


def patch_to_plan(
    patch: dict, name: str | None = None, transaction: str = "single"
) -> dict:
    """Translate one semantic patch into an annotate plan.

    Raises PatchError on anything the bridge could not apply deterministically.
    """
    if not isinstance(patch, dict):
        raise PatchError("patch must be an object")
    if patch.get("contract") != PATCH_CONTRACT:
        raise PatchError(f"unsupported contract {patch.get('contract')!r}")

    commands = patch.get("commands")
    if not isinstance(commands, list):
        raise PatchError("patch.commands must be an array")

    ops: list[dict] = []
    seen: dict[tuple, int] = {}
    for index, command in enumerate(commands):
        where = f"command {index}"
        if not isinstance(command, dict):
            raise PatchError(f"{where}: must be an object")

        status = command.get("status")
        if status not in STATUSES:
            raise PatchError(
                f"{where}: status {status!r} is not applyable (auto|approved)"
            )

        field = command.get("field")
        target = _target(command, where)
        value = _scalar(command.get("value"), where)

        if field == NAME_FIELD:
            if not isinstance(value, str) or not value.strip():
                raise PatchError(f"{where}: name must be a non-empty string")
            op = {"op": "rename", "target": target, "name": value.strip()}
            key = ("rename", json.dumps(target, sort_keys=True))
        elif field in FIELD_MAP:
            dictionary, attr = FIELD_MAP[field]
            if value is None or str(value).strip() == "":
                raise PatchError(f"{where}: {field} must have a non-empty value")
            op = {
                "op": "set_attribute",
                "target": target,
                "dict": dictionary,
                "key": attr,
                "value": value,
            }
            key = (
                "set_attribute",
                json.dumps(target, sort_keys=True),
                dictionary,
                attr,
            )
        else:
            known = ", ".join(sorted([*FIELD_MAP, NAME_FIELD]))
            raise PatchError(
                f"{where}: field {field!r} has no bridge mapping (known: {known})"
            )

        # Last write wins, mirroring sequential application, without inflating
        # the plan with commands the bridge would apply twice.
        if key in seen:
            ops[seen[key]] = op
        else:
            seen[key] = len(ops)
            ops.append(op)

    if len(ops) > MAX_OPS:
        raise PatchError(f"plan has {len(ops)} operations (bridge max {MAX_OPS})")

    plan = {
        "schema": PLAN_SCHEMA,
        "name": name or _plan_name(patch),
        "note": _plan_note(patch),
        "transaction": transaction,
        "operations": ops,
    }
    return plan


def _plan_name(patch: dict) -> str:
    base = patch.get("model_name") or patch.get("model") or "model"
    return f"{base} semantic patch"


def _plan_note(patch: dict) -> str:
    return (
        "generated by cad2llm_patch.py from "
        f"{patch.get('model', '?')} "
        f"(src {patch.get('source_sha256', '?')}, registry {patch.get('registry_version', '?')})"
    )


def validate_plan(plan: dict) -> list[str]:
    """Structural lint mirroring the Ruby side. Returns a list of problems."""
    problems: list[str] = []
    if not isinstance(plan, dict):
        return ["plan must be an object"]
    allowed = {"schema", "name", "note", "transaction", "operations"}
    unknown = set(plan) - allowed
    if unknown:
        problems.append(f"unknown plan keys: {sorted(unknown)}")
    if plan.get("schema") != PLAN_SCHEMA:
        problems.append(f"schema must be {PLAN_SCHEMA}")
    if plan.get("transaction") not in ("per_op", "single"):
        problems.append("transaction must be per_op or single")

    ops = plan.get("operations")
    if not isinstance(ops, list) or not ops:
        problems.append("operations must be a non-empty array")
        return problems
    if len(ops) > MAX_OPS:
        problems.append(f"operations exceeds {MAX_OPS}")

    for index, op in enumerate(ops):
        where = f"operation {index}"
        if not isinstance(op, dict):
            problems.append(f"{where}: must be an object")
            continue
        kind = op.get("op")
        if kind not in ("set_attribute", "set_tag", "rename", "set_description"):
            problems.append(f"{where}: unknown op {kind!r}")
            continue
        extra = set(op) - {
            "op",
            "target",
            "dict",
            "key",
            "value",
            "tag",
            "name",
            "description",
            "preserve_original",
        }
        if extra:
            problems.append(f"{where}: unknown keys {sorted(extra)}")
        target = op.get("target")
        if not isinstance(target, dict) or not target:
            problems.append(f"{where}: target must be a non-empty object")
        else:
            bad = set(target) - set(TARGET_KEYS)
            if bad:
                problems.append(f"{where}: unsupported target keys {sorted(bad)}")
        if kind == "set_attribute":
            for required in ("dict", "key", "value"):
                if required not in op:
                    problems.append(f"{where}: set_attribute needs {required}")
            if str(op.get("dict", "")).upper().startswith("SU_"):
                problems.append(
                    f"{where}: dict {op.get('dict')!r} is reserved by SketchUp"
                )
        elif kind == "rename" and not op.get("name"):
            problems.append(f"{where}: rename needs name")
    return problems


def load_patch(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def plan_for_file(path: str | Path) -> dict:
    patch = load_patch(path)
    if not patch.get("commands"):
        raise EmptyPatch("no applyable commands")
    plan = patch_to_plan(patch)
    problems = validate_plan(plan)
    if problems:
        raise PatchError("; ".join(problems))
    return plan


def apply_patch(
    path: str | Path, dry_run: bool, report: str | Path | None = None
) -> dict:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from cad2llm_bridge import BridgeClient

    plan = plan_for_file(path)
    client = BridgeClient()
    result = client.apply(plan, dry_run=dry_run)
    if report:
        Path(report).write_text(
            json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
    return result


def summary_line(path: Path, plan: dict) -> str:
    attrs = sum(1 for op in plan["operations"] if op["op"] == "set_attribute")
    renames = sum(1 for op in plan["operations"] if op["op"] == "rename")
    return f"{path.name}: {len(plan['operations'])} ops ({attrs} attributes, {renames} names)"


# ----------------------------------------------------------------- selftest


def selftest() -> int:
    failures = []

    def check(name, condition):
        if not condition:
            failures.append(name)
        print(f"  {name:<52} {'ok' if condition else 'FAIL'}")

    base = {
        "contract": PATCH_CONTRACT,
        "model": "m.json",
        "model_name": "m",
        "commands": [
            {
                "op": "set_attribute",
                "target": {"persistent_id": 1},
                "field": "cad2llm.role",
                "value": "wall",
                "status": "auto",
            },
            {
                "op": "set_attribute",
                "target": {"persistent_id": 1},
                "field": "ifc.type",
                "value": "IfcWall",
                "status": "auto",
            },
            {
                "op": "set_attribute",
                "target": {"persistent_id": 1},
                "field": "name",
                "value": "wall_01",
                "status": "auto",
            },
        ],
    }
    plan = patch_to_plan(base)
    check("tri prikazy -> tri operace", len(plan["operations"]) == 3)
    check(
        "ifc.type -> dict ifc, key type",
        plan["operations"][1]["dict"] == "ifc"
        and plan["operations"][1]["key"] == "type",
    )
    check("name -> rename", plan["operations"][2]["op"] == "rename")
    check("plan je validni", validate_plan(plan) == [])

    dup = json.loads(json.dumps(base))
    dup["commands"].append(
        {
            "op": "set_attribute",
            "target": {"persistent_id": 1},
            "field": "cad2llm.role",
            "value": "beam",
            "status": "auto",
        }
    )
    check(
        "duplicitni pole = posledni vyhrava",
        patch_to_plan(dup)["operations"][0]["value"] == "beam",
    )

    bad = json.loads(json.dumps(base))
    bad["commands"][0]["field"] = "cad2llm.ifc_type"
    try:
        patch_to_plan(bad)
        check("nezname pole = chyba", False)
    except PatchError:
        check("nezname pole = chyba", True)

    pending = json.loads(json.dumps(base))
    pending["commands"][0]["status"] = "pending_review"
    try:
        patch_to_plan(pending)
        check("pending_review se nezapisuje", False)
    except PatchError:
        check("pending_review se nezapisuje", True)

    nopath = json.loads(json.dumps(base))
    nopath["commands"][0]["target"] = {"uid": "1@x"}
    try:
        patch_to_plan(nopath)
        check("target bez persistent_id = chyba", False)
    except PatchError:
        check("target bez persistent_id = chyba", True)

    check(
        "prazdny plan je nevalidni",
        validate_plan({"schema": PLAN_SCHEMA, "operations": []}) != [],
    )
    check(
        "SU_ dict je zakazany",
        validate_plan(
            {
                "schema": PLAN_SCHEMA,
                "transaction": "single",
                "operations": [
                    {
                        "op": "set_attribute",
                        "target": {"persistent_id": 1},
                        "dict": "SU_Test",
                        "key": "k",
                        "value": "v",
                    }
                ],
            }
        )
        != [],
    )

    print(f"\nselftest: {len(failures)} selhani")
    return 0 if not failures else 1


# --------------------------------------------------------------------- cli


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass

    parser = argparse.ArgumentParser(
        description="CAD2LLM semantic patch -> annotate plan"
    )
    parser.add_argument("--selftest", action="store_true")
    sub = parser.add_subparsers(dest="command")

    p_check = sub.add_parser("check", help="offline contract lint")
    p_check.add_argument("patches", nargs="+")

    p_plan = sub.add_parser("plan", help="write the annotate plan")
    p_plan.add_argument("patch")
    p_plan.add_argument("-o", "--out", default=None)

    p_apply = sub.add_parser("apply", help="apply through the running bridge")
    p_apply.add_argument("patch")
    p_apply.add_argument("--dry-run", action="store_true")
    p_apply.add_argument("--report", default=None)

    args = parser.parse_args(argv)

    if args.selftest:
        return selftest()
    if args.command == "check":
        failed = 0
        for raw in args.patches:
            path = Path(raw)
            try:
                plan = plan_for_file(path)
            except EmptyPatch:
                print(f"skip {path.name}: no applyable commands (waiting for review)")
                continue
            except (PatchError, OSError, json.JSONDecodeError) as exc:
                failed += 1
                print(f"FAIL {path.name}: {exc}")
                continue
            print(f"ok   {summary_line(path, plan)}")
        return 1 if failed else 0
    if args.command == "plan":
        plan = plan_for_file(args.patch)
        text = json.dumps(plan, indent=1, ensure_ascii=False) + "\n"
        if args.out:
            Path(args.out).write_text(text, encoding="utf-8")
            print(f"plan: {args.out}")
        else:
            print(text, end="")
        return 0
    if args.command == "apply":
        try:
            result = apply_patch(args.patch, args.dry_run, args.report)
        except (PatchError, OSError, json.JSONDecodeError) as exc:
            print(f"ERROR {exc}", file=sys.stderr)
            return 1
        except Exception as exc:  # bridge errors carry a code
            code = getattr(exc, "code", "error")
            message = getattr(exc, "message", str(exc))
            print(f"ERROR {code}: {message}", file=sys.stderr)
            return (
                2 if code in ("unreachable", "no_token_file", "bad_token_file") else 3
            )
        s = result.get("summary") or {}
        print(
            f"OK  total={s.get('total')} applied={s.get('applied')} "
            f"planned={s.get('planned')} unchanged={s.get('unchanged')} "
            f"errors={s.get('errors')}"
            + ("  (dry run)" if result.get("dry_run") else "")
        )
        for op in result.get("operations") or []:
            if op.get("status") == "error":
                print(f"    [{op.get('index')}] {op.get('code')}: {op.get('message')}")
        if result.get("rolled_back"):
            print(f"    ROLLED BACK: {result.get('rollback_reason')}")
        if s.get("errors") or result.get("rolled_back"):
            return 3
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
