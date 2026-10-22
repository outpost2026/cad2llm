#!/usr/bin/env python3
"""CAD2LLM CLI — deterministic CAD -> JSON pipeline (no LLM, no SketchUp).

python tools/cad2llm.py convert  <model.dae> <out.json> [--rules rules.json] [--no-validate]
python tools/cad2llm.py validate <doc.json> [--rules rules.json] [--out report.json]
python tools/cad2llm.py summary  <doc.json> [--md report.md]
python tools/cad2llm.py diff     <a.json> <b.json>
python tools/cad2llm.py enrich   <doc.json> <out.json> [--excerpt]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import cad2llm_core as core
import cad2llm_v5 as v5


def _load_rules(path: str | None) -> dict:
    if not path:
        default = Path(__file__).with_name("cad2llm_rules.json")
        path = str(default) if default.exists() else None
    if not path:
        return {}
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _write_json(path: str, data: dict) -> None:
    Path(path).write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def cmd_convert(args) -> int:
    doc = core.parse_dae(args.dae)
    if not args.no_validate:
        doc["validation"] = core.validate(doc, _load_rules(args.rules))
    else:
        doc["validation"] = {"issues": doc["validation"]["issues"]}
    _write_json(args.out, doc)
    c = (doc.get("validation") or {}).get("counts") or {}
    print(f"OK  {args.out}")
    print(
        f"    nodes={len(doc['nodes'])} with_bbox={c.get('with_bbox', '?')} "
        f"errors={c.get('errors', '?')} warnings={c.get('warnings', '?')}"
    )
    for i in doc["validation"].get("issues", [])[:10]:
        print(f"    [{i['severity']}] {i['code']}: {i['message']}")
    return 0


def cmd_validate(args) -> int:
    doc = core.load_any(args.doc)
    v = core.validate(doc, _load_rules(args.rules))
    doc["validation"] = v
    if args.out:
        _write_json(args.out, doc)
    print(
        f"nodes={v['counts']['nodes']} with_bbox={v['counts']['with_bbox']} "
        f"errors={v['counts']['errors']} warnings={v['counts']['warnings']} "
        f"overlaps={len(v['overlaps'])}"
    )
    for i in v["issues"][:20]:
        print(f"  [{i['severity']}] {i['code']}: {i['message']}")
    return 0 if v["counts"]["errors"] == 0 else 2


def cmd_summary(args) -> int:
    doc = core.load_any(args.doc)
    if not (doc.get("validation") or {}).get("z_table"):
        doc["validation"] = core.validate(doc, _load_rules(args.rules))
    md = core.summarize(doc, max_rows=args.max_rows)
    print(md)
    if args.md:
        Path(args.md).write_text(md, encoding="utf-8")
        print(f"[written] {args.md}")
    return 0


def cmd_diff(args) -> int:
    d = core.diff(core.load_any(args.a), core.load_any(args.b))
    print(json.dumps(d, indent=2, ensure_ascii=False))
    return 0


def cmd_enrich(args) -> int:
    doc = core.load_any(args.doc)
    computed = not v5.has_validation(doc)
    out = v5.enrich(
        doc,
        source_hash=v5.file_hash(args.doc),
        basis="excerpt" if args.excerpt else "full",
    )
    v5.write_json(args.out, out)
    sem = [n["semantics"] for n in out["nodes"]]
    known = sum(1 for s in sem if s["role"] != "unknown")
    print(f"OK  {args.out}")
    print(
        f"    nodes={len(sem)} known_role={known} unknown={len(sem) - known} "
        f"brief={len(out['brief']['markdown'])} chars "
        f"findings={len(out['brief']['top_findings'])}"
    )
    if computed:
        print("    validation=computed")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        prog="cad2llm",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("convert", help="DAE -> schema v4 JSON")
    c.add_argument("dae")
    c.add_argument("out")
    c.add_argument("--rules")
    c.add_argument("--no-validate", action="store_true")
    c.set_defaults(func=cmd_convert)

    v = sub.add_parser("validate", help="validate JSON (v4 or legacy v3)")
    v.add_argument("doc")
    v.add_argument("--rules")
    v.add_argument("--out")
    v.set_defaults(func=cmd_validate)

    s = sub.add_parser("summary", help="human/LLM markdown summary")
    s.add_argument("doc")
    s.add_argument("--md")
    s.add_argument("--rules")
    s.add_argument("--max-rows", type=int, default=60)
    s.set_defaults(func=cmd_summary)

    d = sub.add_parser("diff", help="spatial diff of two exports")
    d.add_argument("a")
    d.add_argument("b")
    d.set_defaults(func=cmd_diff)

    e = sub.add_parser("enrich", help="v4 -> v5 (semantics + validation + brief)")
    e.add_argument("doc")
    e.add_argument("out")
    e.add_argument("--excerpt", action="store_true")
    e.set_defaults(func=cmd_enrich)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
