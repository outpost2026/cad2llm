#!/usr/bin/env python3
"""CAD2LLM Bridge client - talk to the running SketchUp session (protocol v1).

The Ruby side (src/cad2llm_exporter/lib/bridge.rb) listens on 127.0.0.1 and
writes its port + token to the temp dir. This client reads that file unless
--port / --token are given explicitly.

python tools/cad2llm_bridge.py ping
python tools/cad2llm_bridge.py info
python tools/cad2llm_bridge.py export [out.json] [--schema 4.0]
python tools/cad2llm_bridge.py apply plan.json [--dry-run] [--report report.json]
python tools/cad2llm_bridge.py undo

Exit codes: 0 ok, 2 bridge not reachable / protocol error, 3 bridge returned error.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import tempfile
from pathlib import Path

import cad2llm_verify as verify

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 9877
TOKEN_FILE = "cad2llm_bridge.json"
MAX_RESPONSE_BYTES = 4 * 1024 * 1024


class BridgeError(RuntimeError):
    """Error reported by the bridge or by the local transport."""

    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


def token_candidates() -> list[Path]:
    out: list[Path] = []
    local = os.environ.get("LOCALAPPDATA")
    if local:
        out.append(Path(local) / "cad2llm" / "bridge.json")
    out.append(Path(tempfile.gettempdir()) / TOKEN_FILE)
    return out


def token_path() -> Path:
    for candidate in token_candidates():
        if candidate.is_file():
            return candidate
    return token_candidates()[0]


def load_token(path: str | Path | None = None) -> dict:
    candidates = [Path(path)] if path else token_candidates()
    for candidate in candidates:
        if not candidate.is_file():
            continue
        try:
            return json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise BridgeError("bad_token_file", f"{candidate}: {exc}") from exc
    checked = ", ".join(str(c) for c in candidates)
    raise BridgeError(
        "no_token_file",
        f"no token file found ({checked}) - is the bridge started? "
        "(Extensions > CAD2LLM > Start local bridge)",
    )


class BridgeClient:
    def __init__(
        self,
        host: str | None = None,
        port: int | None = None,
        token: str | None = None,
        timeout: float = 30.0,
        token_file: str | Path | None = None,
    ):
        info: dict = {}
        if port is None or token is None:
            info = load_token(token_file)
        self.host = host or info.get("host") or DEFAULT_HOST
        self.port = int(port if port is not None else info.get("port", DEFAULT_PORT))
        self.token = token if token is not None else info.get("token")
        self.timeout = timeout
        self._id = 0

    def call(self, method: str, params: dict | None = None) -> dict:
        self._id += 1
        request = {"id": self._id, "method": method, "params": params or {}}
        if self.token:
            request["token"] = self.token

        payload = (json.dumps(request, ensure_ascii=False) + "\n").encode("utf-8")
        try:
            with socket.create_connection(
                (self.host, self.port), timeout=self.timeout
            ) as sock:
                sock.sendall(payload)
                raw = self._read_line(sock)
        except OSError as exc:
            raise BridgeError(
                "unreachable",
                f"{self.host}:{self.port} - {exc}. Is SketchUp running with the bridge started?",
            ) from exc

        try:
            response = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise BridgeError("bad_response", f"{exc}: {raw[:200]!r}") from exc

        if response.get("id") not in (None, request["id"]):
            raise BridgeError(
                "id_mismatch", f"asked {request['id']}, got {response.get('id')}"
            )
        if not response.get("ok"):
            err = response.get("error") or {}
            raise BridgeError(
                err.get("code", "unknown"), err.get("message", "no message")
            )
        return response.get("result") or {}

    @staticmethod
    def _read_line(sock: socket.socket) -> bytes:
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = sock.recv(65_536)
            if not chunk:
                raise BridgeError("connection_closed", "bridge closed the connection")
            chunks.append(chunk)
            total += len(chunk)
            if total > MAX_RESPONSE_BYTES:
                raise BridgeError(
                    "response_too_large", f"exceeded {MAX_RESPONSE_BYTES} bytes"
                )
            buf = b"".join(chunks)
            if b"\n" in buf:
                return buf.split(b"\n", 1)[0]

    def ping(self) -> dict:
        return self.call("ping")

    def info(self) -> dict:
        return self.call("info")

    def export(self, path: str | None = None, schema: str | None = None) -> dict:
        params: dict = {}
        if path:
            params["path"] = str(Path(path).resolve())
        if schema:
            params["schema"] = schema
        return self.call("export", params)

    def export_inline(self) -> dict:
        return self.call("export", {"inline": True})

    def apply(self, plan: dict | str | Path, dry_run: bool = False) -> dict:
        if isinstance(plan, (str, Path)):
            plan = json.loads(Path(plan).read_text(encoding="utf-8"))
        return self.call("apply", {"plan": plan, "dry_run": bool(dry_run)})

    def attributes(self, limit: int = 200) -> dict:
        return self.call("attributes", {"limit": limit})

    def describe(self, *, pids=None, definitions=None) -> dict:
        targets = [{"persistent_id": pid} for pid in (pids or [])]
        targets += [
            {"kind": "definition", "name": name} for name in (definitions or [])
        ]
        return self.call("describe", {"targets": targets} if targets else {})

    def purge_unused_tags(self) -> dict:
        return self.call("purge_unused_tags")

    def undo(self) -> dict:
        return self.call("undo")


def _print_result(args, result: dict) -> None:
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return
    method = args.command
    if method == "ping":
        print(
            f"OK  protocol={result.get('protocol')} sketchup={result.get('sketchup')} "
            f"authenticated={result.get('authenticated')}"
        )
    elif method == "info":
        print(f"OK  {result.get('title')}  ({result.get('path') or 'unsaved'})")
        print(
            f"    top_level={result.get('top_level_entities')} "
            f"groups={result.get('top_level_groups')} "
            f"components={result.get('top_level_components')} tags={len(result.get('tags') or [])}"
        )
    elif method == "export":
        print(f"OK  {result.get('path')}")
        print(
            f"    schema={result.get('schema')} nodes={result.get('nodes')} "
            f"bytes={result.get('bytes')} issues={result.get('issues')} "
            f"errors={result.get('errors')} warnings={result.get('warnings')}"
        )
    elif method == "apply":
        s = result.get("summary") or {}
        post = result.get("post_check") or {}
        print(
            f"OK  total={s.get('total')} applied={s.get('applied')} planned={s.get('planned')} "
            f"unchanged={s.get('unchanged')} errors={s.get('errors')}"
            + ("  (dry run)" if result.get("dry_run") else "")
        )
        for op in result.get("operations") or []:
            if op.get("status") == "error":
                print(f"    [{op.get('index')}] {op.get('code')}: {op.get('message')}")
        if post:
            print(
                f"    post: nodes={post.get('nodes')} tags={post.get('tag_distribution')} "
                f"issues={post.get('issues')}"
            )
        if result.get("snapshot"):
            print(f"    snapshot: {result.get('snapshot')}")
        if result.get("rolled_back"):
            print(f"    ROLLED BACK: {result.get('rollback_reason')}")
    elif method == "verify":
        counts = result.get("counts") or {}
        status = "OK  " if result.get("ok") else "DRIFT"
        print(
            f"{status} nodes {counts.get('baseline')} vs {counts.get('current')} "
            f"changed={counts.get('changed')} missing={counts.get('missing')} "
            f"added={counts.get('added')}"
        )
        for item in result.get("drift") or []:
            if item.get("field"):
                print(
                    f"    pid {item.get('pid')} {item.get('name')} {item.get('field')}: "
                    f"expected={item.get('expected')!r} actual={item.get('actual')!r}"
                )
            else:
                print(
                    f"    pid {item.get('pid')} {item.get('name')}: {item.get('status')}"
                )
    elif method == "attributes":
        print(
            f"OK  entities={result.get('entities_total')} "
            f"with_dicts={result.get('entities_with_dicts')} "
            f"definitions_with_dicts={result.get('definitions_with_dicts')}"
        )
        seen = {}
        for entity in result.get("entities") or []:
            for name in entity.get("dicts") or {}:
                seen[name] = seen.get(name, 0) + 1
        for name in sorted(seen):
            print(f"    {name}: {seen[name]} entities")
        for name, dicts in (result.get("definitions") or {}).items():
            if dicts:
                print(f"    def {name}: {', '.join(sorted(dicts))}")
    elif method == "describe":
        print(f"OK  items={result.get('count')}")
        for item in result.get("items") or []:
            if item.get("error"):
                print(
                    f"    ERROR {item.get('error')}: {item.get('name') or item.get('message')}"
                )
                continue
            label = item.get("name")
            if item.get("persistent_id") is not None:
                label = f"{label} (pid {item.get('persistent_id')})"
            print(f"    {item.get('kind')}: {label}")
            if item.get("tag"):
                print(
                    f"      tag: {item.get('tag')}  definition: {item.get('definition_name')}"
                )
            description = item.get("description") or item.get("definition_description")
            if description:
                print(f"      popis: {description}")
            for dict_name, values in (item.get("dicts") or {}).items():
                pairs = ", ".join(f"{k}={v}" for k, v in values.items())
                print(f"      [{dict_name}] {pairs}")
    elif method == "purge-tags":
        print(
            f"OK  purged={result.get('purged')}  tags {result.get('before')} -> {result.get('after')}"
        )
    elif method == "undo":
        print(f"OK  undo via {result.get('via')}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="CAD2LLM bridge client (protocol v1)")
    parser.add_argument("--host", default=None, help=f"default {DEFAULT_HOST}")
    parser.add_argument(
        "--port", type=int, default=None, help=f"default {DEFAULT_PORT}"
    )
    parser.add_argument(
        "--token", default=None, help="override token from the temp file"
    )
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--json", action="store_true", help="print raw JSON")

    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("ping")
    sub.add_parser("info")

    p_export = sub.add_parser("export")
    p_export.add_argument("path", nargs="?", default=None)
    p_export.add_argument("--schema", default=None, choices=["4.0", "3.0"])

    p_apply = sub.add_parser("apply")
    p_apply.add_argument("plan")
    p_apply.add_argument("--dry-run", action="store_true")
    p_apply.add_argument("--report", default=None)

    p_verify = sub.add_parser("verify")
    p_verify.add_argument("--baseline", required=True)
    p_verify.add_argument("--current", default=None)
    p_verify.add_argument("--live", action="store_true")
    p_verify.add_argument("--fields", default=None)

    p_attr = sub.add_parser("attributes")
    p_attr.add_argument("--limit", type=int, default=200)

    p_desc = sub.add_parser("describe")
    p_desc.add_argument("--pid", type=int, action="append", default=None)
    p_desc.add_argument("--definition", action="append", default=None)

    sub.add_parser("purge-tags")
    sub.add_parser("undo")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "verify":
            baseline = verify.load_document(args.baseline)
            if args.live:
                if args.current:
                    raise ValueError("--live and --current are mutually exclusive")
                client = BridgeClient(
                    host=args.host,
                    port=args.port,
                    token=args.token,
                    timeout=args.timeout,
                )
                current = client.export_inline()["document"]
            else:
                if not args.current:
                    raise ValueError("verify needs --current PATH or --live")
                current = verify.load_document(args.current)
            result = verify.compare_invariants(
                baseline, current, verify.parse_fields(args.fields)
            )
            _print_result(args, result)
            return 0 if result["ok"] else 3

        client = BridgeClient(
            host=args.host, port=args.port, token=args.token, timeout=args.timeout
        )
        if args.command == "apply":
            plan_path = Path(args.plan)
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
            result = client.apply(plan, dry_run=args.dry_run)
            if args.report:
                Path(args.report).write_text(
                    json.dumps(result, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8",
                )
        elif args.command == "attributes":
            result = client.attributes(args.limit)
        elif args.command == "describe":
            result = client.describe(pids=args.pid, definitions=args.definition)
        elif args.command == "purge-tags":
            result = client.purge_unused_tags()
        elif args.command == "export":
            result = client.export(args.path, args.schema)
        else:
            result = getattr(client, args.command)()
    except BridgeError as exc:
        print(f"ERROR {exc.code}: {exc.message}", file=sys.stderr)
        return (
            3
            if exc.code not in ("unreachable", "no_token_file", "bad_token_file")
            else 2
        )
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"ERROR {exc}", file=sys.stderr)
        return 2

    _print_result(args, result)
    if args.command == "apply" and (
        (result.get("summary") or {}).get("errors") or result.get("rolled_back")
    ):
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
