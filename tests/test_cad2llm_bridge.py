#!/usr/bin/env python3
"""Tests for the CAD2LLM bridge client (protocol v1) against a fake TCP server.

Run:  python -m unittest discover -s tests -v
"""

import io
import json
import socket
import sys
import tempfile
import threading
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import cad2llm_bridge as bridge  # noqa: E402


def ok_handler(request):
    return {
        "id": request["id"],
        "ok": True,
        "result": {"echo": request["method"], "token_seen": request.get("token")},
    }


def error_handler(request):
    return {
        "id": request["id"],
        "ok": False,
        "error": {"code": "unauthorized", "message": "missing or invalid token"},
    }


class FakeBridge:
    def __init__(self, handler):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(8)
        self.port = self.sock.getsockname()[1]
        self.handler = handler
        self.received = []
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            with conn:
                buf = b""
                try:
                    while b"\n" not in buf:
                        chunk = conn.recv(65_536)
                        if not chunk:
                            break
                        buf += chunk
                    if b"\n" not in buf:
                        continue
                    request = json.loads(buf.split(b"\n", 1)[0])
                    self.received.append(request)
                    conn.sendall(
                        (json.dumps(self.handler(request)) + "\n").encode("utf-8")
                    )
                except OSError:
                    pass

    def close(self):
        self.sock.close()


class BridgeClientTests(unittest.TestCase):
    def setUp(self):
        self.servers = []

    def tearDown(self):
        for server in self.servers:
            server.close()

    def server(self, handler=ok_handler):
        server = FakeBridge(handler)
        self.servers.append(server)
        return server

    def test_call_roundtrip_and_token(self):
        server = self.server()
        client = bridge.BridgeClient(port=server.port, token="abc123")
        result = client.call("ping")
        self.assertEqual(result["echo"], "ping")
        self.assertEqual(result["token_seen"], "abc123")
        self.assertEqual(server.received[0]["method"], "ping")
        self.assertEqual(server.received[0]["params"], {})

    def test_error_maps_to_bridge_error(self):
        server = self.server(error_handler)
        client = bridge.BridgeClient(port=server.port, token="bad")
        with self.assertRaises(bridge.BridgeError) as ctx:
            client.info()
        self.assertEqual(ctx.exception.code, "unauthorized")

    def test_token_file_provides_port_and_token(self):
        server = self.server()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cad2llm_bridge.json"
            path.write_text(
                json.dumps(
                    {"host": "127.0.0.1", "port": server.port, "token": "fromfile"}
                ),
                encoding="utf-8",
            )
            client = bridge.BridgeClient(token_file=path)
            self.assertEqual(client.port, server.port)
            client.ping()
            self.assertEqual(server.received[0]["token"], "fromfile")

    def test_token_candidates_prefers_localappdata(self):
        with tempfile.TemporaryDirectory() as tmp:
            local = Path(tmp) / "cad2llm" / "bridge.json"
            local.parent.mkdir(parents=True)
            local.write_text('{"port": 1, "token": "x"}', encoding="utf-8")
            old = bridge.os.environ.get("LOCALAPPDATA")
            bridge.os.environ["LOCALAPPDATA"] = tmp
            try:
                self.assertEqual(bridge.token_path(), local)
                self.assertEqual(bridge.load_token()["token"], "x")
            finally:
                if old is None:
                    bridge.os.environ.pop("LOCALAPPDATA", None)
                else:
                    bridge.os.environ["LOCALAPPDATA"] = old

    def test_missing_token_file(self):
        with self.assertRaises(bridge.BridgeError) as ctx:
            bridge.BridgeClient(
                token_file=Path(tempfile.gettempdir()) / "definitely-missing.json"
            )
        self.assertEqual(ctx.exception.code, "no_token_file")

    def test_connection_refused(self):
        probe = socket.socket()
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
        probe.close()
        with self.assertRaises(bridge.BridgeError) as ctx:
            bridge.BridgeClient(port=port, token="x", timeout=2).ping()
        self.assertEqual(ctx.exception.code, "unreachable")

    def test_id_mismatch_detected(self):
        def bad_id(request):
            return {"id": request["id"] + 100, "ok": True, "result": {}}

        server = self.server(bad_id)
        with self.assertRaises(bridge.BridgeError) as ctx:
            bridge.BridgeClient(port=server.port, token="t").ping()
        self.assertEqual(ctx.exception.code, "id_mismatch")

    def test_describe_builds_targets(self):
        server = self.server()
        bridge.BridgeClient(port=server.port, token="t").describe(
            pids=[1, 2], definitions=["wall"]
        )
        params = server.received[0]["params"]
        self.assertEqual(
            params["targets"],
            [
                {"persistent_id": 1},
                {"persistent_id": 2},
                {"kind": "definition", "name": "wall"},
            ],
        )

    def test_describe_selection_sends_no_targets(self):
        server = self.server()
        bridge.BridgeClient(port=server.port, token="t").describe()
        self.assertEqual(server.received[0]["params"], {})

    def test_purge_unused_tags_method(self):
        server = self.server()
        bridge.BridgeClient(port=server.port, token="t").purge_unused_tags()
        self.assertEqual(server.received[0]["method"], "purge_unused_tags")

    def test_apply_sends_plan(self):
        server = self.server()
        plan = {"schema": "cad2llm/annotate_plan/v1", "operations": []}
        bridge.BridgeClient(port=server.port, token="t").apply(plan, dry_run=True)
        body = server.received[0]
        self.assertEqual(body["method"], "apply")
        self.assertEqual(body["params"]["plan"], plan)
        self.assertTrue(body["params"]["dry_run"])

    def test_apply_accepts_path(self):
        server = self.server()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "plan.json"
            path.write_text(
                '{"schema":"cad2llm/annotate_plan/v1","operations":[]}',
                encoding="utf-8",
            )
            bridge.BridgeClient(port=server.port, token="t").apply(path)
        self.assertEqual(server.received[0]["params"]["plan"]["operations"], [])


class CliTests(unittest.TestCase):
    def setUp(self):
        self.server = FakeBridge(ok_handler)
        self.base = ["--port", str(self.server.port), "--token", "t"]

    def tearDown(self):
        self.server.close()

    def test_info_human_readable(self):
        out = io.StringIO()
        with redirect_stdout(out):
            code = bridge.main(self.base + ["info"])
        self.assertEqual(code, 0)
        self.assertIn("OK", out.getvalue())

    def test_json_flag(self):
        out = io.StringIO()
        with redirect_stdout(out):
            code = bridge.main(self.base + ["--json", "ping"])
        self.assertEqual(code, 0)
        payload = json.loads(out.getvalue())
        self.assertEqual(payload["echo"], "ping")

    def test_bridge_error_exit_code(self):
        server = FakeBridge(error_handler)
        try:
            err = io.StringIO()
            with redirect_stderr(err):
                code = bridge.main(["--port", str(server.port), "--token", "t", "info"])
            self.assertEqual(code, 3)
            self.assertIn("unauthorized", err.getvalue())
        finally:
            server.close()


if __name__ == "__main__":
    unittest.main()
