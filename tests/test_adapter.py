from __future__ import annotations

import copy
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from adapter.resolver import Candidate, candidates_from_sources, resolve
from adapter.server import (
    AdapterConfig,
    InvalidParams,
    McpServer,
    PUBLIC_SCHEMAS,
    SubprocessRunner,
    TOOL_BY_NAME,
    TOOL_SPECS,
    UpstreamError,
    WechatCliClient,
    source_inventory_metadata,
    serve_jsonl,
)


EXPECTED_TOOLS = [
    "status",
    "sessions",
    "unread",
    "resolve_chat",
    "chat_timeline",
    "message_context",
    "search",
    "search_with_context",
    "read_events",
    "contacts",
    "group_members",
    "media_resources",
    "favorites",
    "moments_feed",
    "moments_search",
    "moments_notifications",
    "transfers",
    "red_packets",
]


def catalog() -> dict:
    return {
        "ok": True,
        "data": {
            "tools": [
                {
                    "name": spec.upstream,
                    "read_only": True,
                    "local_write_mode": "none",
                    "strict_read_only_behavior": "same",
                }
                for spec in TOOL_SPECS
                if spec.name != "resolve_chat"
            ]
        },
    }


class FakeRunner:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    @staticmethod
    def _spec_for(command: str):
        for spec in TOOL_SPECS:
            if command in {spec.name, spec.upstream, *spec.catalog_aliases}:
                return spec
        raise AssertionError(command)

    def run_json(self, arguments):
        args = list(arguments)
        self.calls.append(args)
        if args == ["tools"]:
            return catalog()
        if args[:1] == ["tool-schema"]:
            spec = self._spec_for(args[1])
            schema = copy.deepcopy(PUBLIC_SCHEMAS[spec.name])
            if spec.name == "status":
                schema["properties"]["mode"] = {
                    "type": "string",
                    "enum": ["overview", "coverage", "workflows", "status"],
                }
            return {"ok": True, "data": {"tool": {"inputSchema": schema}}}
        if args == ["call-json", "coverage", "{}"]:
            return {"ok": True, "data": {"coverage": [], "quality_gates": []}}
        if args[:2] == ["call-json", "read_os"]:
            return {
                "ok": True,
                "data": {"status": {"live_read_ok": True, "mode": {"strict_read_only": True}}},
            }
        if args[:2] == ["call-json", "sessions"]:
            return {"ok": True, "data": {"sessions": [], "query": {"has_more": False}}}
        if args[:2] == ["call-json", "contacts"]:
            return {"ok": True, "data": {"contacts": [], "query": {"has_more": False}}}
        if args[:1] == ["call-json"]:
            return {"ok": True, "tool": args[1], "data": {}}
        raise AssertionError(args)


class ResolverTests(unittest.TestCase):
    def test_exact_unique_partial_and_ambiguous(self):
        candidates = [
            Candidate("Alice Chen", "wxid_alice"),
            Candidate("Alice Zhang", "wxid_zhang"),
            Candidate("Bob", "wxid_bob"),
        ]
        exact = resolve(candidates, "alice chen")
        partial = resolve(candidates, "Chen")
        ambiguous = resolve(candidates, "Alice")
        self.assertTrue(exact.ok and exact.exact and not exact.ambiguous)
        self.assertTrue(partial.ok and partial.partial and not partial.ambiguous)
        self.assertTrue(ambiguous.ok and ambiguous.partial and ambiguous.ambiguous)
        self.assertIsNone(ambiguous.internal_chat)
        self.assertNotIn("wxid_", json.dumps(ambiguous.public()))
        self.assertNotIn("internal_chat", json.dumps(exact.public()))

    def test_only_direct_session_and_contact_labels_become_candidates(self):
        result = candidates_from_sources(
            {"data": {"sessions": [{"display_name": "Alice", "username": "wxid_alice", "body": "secret"}]}},
            {"data": {"contacts": [{"remark": "Bob", "username": "wxid_bob", "message_id": "123"}]}},
        )
        self.assertEqual(
            [(item.label, item.internal_chat) for item in result],
            [("Alice", "wxid_alice"), ("Bob", "wxid_bob")],
        )

    def test_identifier_shaped_display_labels_are_rejected(self):
        result = candidates_from_sources(
            {"data": {"sessions": [{"display_name": "1234567890@chatroom", "username": "1234567890@chatroom"}]}},
            {"data": {"contacts": [{"display_name": "wxid_private", "username": "wxid_private"}]}},
        )
        self.assertEqual(result, [])

    def test_multiple_labels_for_one_internal_chat_are_not_ambiguous(self):
        result = candidates_from_sources(
            {"data": {"sessions": [{"display_name": "Alice Chen", "remark": "Project Alice", "username": "wxid_alice"}]}},
            {"data": {"contacts": [{"nick_name": "Alice C.", "username": "wxid_alice"}]}},
        )
        resolution = resolve(result, "Alice")
        self.assertTrue(resolution.ok)
        self.assertFalse(resolution.ambiguous)
        self.assertEqual(resolution.match_count, 1)


class AdapterPolicyTests(unittest.TestCase):
    def test_exact_complete_read_allowlist_and_no_mutating_tools(self):
        server = McpServer(WechatCliClient(FakeRunner()))
        listed = server.dispatch("tools/list", {})["tools"]
        self.assertEqual([item["name"] for item in listed], EXPECTED_TOOLS)
        self.assertEqual(len(listed), 18)
        self.assertTrue(all(item["inputSchema"]["additionalProperties"] is False for item in listed))
        forbidden = {
            "sql",
            "schema",
            "export",
            "export_messages",
            "cache_status",
            "cache_refresh",
            "cache_rebuild",
            "update",
            "wxkey",
            "companion",
            "send",
        }
        self.assertTrue(forbidden.isdisjoint(EXPECTED_TOOLS))
        for name in forbidden:
            response = server.handle_request(
                {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": {}}}
            )
            self.assertEqual(response["error"]["code"], -32602)

    def test_public_schemas_exclude_debug_write_and_path_switches(self):
        forbidden_properties = {
            "debug",
            "include_debug",
            "include_media_paths",
            "include_local_paths",
            "include_images",
            "path",
            "follow",
            "jsonl",
            "poll_interval",
            "force",
            "background",
        }
        for name, schema in PUBLIC_SCHEMAS.items():
            self.assertTrue(forbidden_properties.isdisjoint(schema["properties"]), name)
        timeline = PUBLIC_SCHEMAS["chat_timeline"]["properties"]
        self.assertIn("before_message", timeline)
        self.assertIn("after_server_id_str", timeline)
        context = PUBLIC_SCHEMAS["message_context"]["properties"]
        self.assertIn("local_id", context)
        self.assertIn("server_id_str", context)

    @patch("adapter.server.subprocess.run")
    def test_fixed_absolute_argv_shell_false_and_forced_environment(self, run_mock):
        run_mock.return_value = SimpleNamespace(returncode=0, stdout='{"ok":true}', stderr="")
        runner = SubprocessRunner(
            Path("C:/locked/wechat-cli.exe"),
            7,
            base_environment={
                "WECHAT_CLI_STRICT_READ_ONLY": "0",
                "WECHAT_CLI_DISABLE_AUTO_REFRESH": "0",
                "WECHAT_CLI_COMPANION_ADDR": "127.0.0.1",
            },
            fixed_environment={"WECHAT_CLI_DB_ROOT": "D:/private/account"},
        )
        runner.run_json(["tools"])
        positional, kwargs = run_mock.call_args
        self.assertEqual(positional[0], [str(Path("C:/locked/wechat-cli.exe")), "tools"])
        self.assertFalse(kwargs["shell"])
        self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)
        self.assertEqual(kwargs["env"]["WECHAT_CLI_STRICT_READ_ONLY"], "1")
        self.assertEqual(kwargs["env"]["WECHAT_CLI_DISABLE_AUTO_REFRESH"], "1")
        self.assertNotIn("WECHAT_CLI_COMPANION_ADDR", kwargs["env"])

    def test_status_is_metadata_only_and_exposes_explicit_safety_gates(self):
        runner = FakeRunner()
        original = runner.run_json

        def run(arguments):
            args = list(arguments)
            if args[:2] == ["call-json", "read_os"]:
                return {
                    "ok": True,
                    "data": {
                        "status": {
                            "live_read_ok": True,
                            "mode": {"strict_read_only": True},
                            "private_path": "C:/private/account",
                            "talker": "wxid_private",
                        }
                    },
                }
            return original(args)

        runner.run_json = run
        client = WechatCliClient(runner)
        client.source_inventory_metadata = lambda: {
            "configured_db_count": 26,
            "keyed_db_count": 26,
            "missing_key_db_count": 0,
            "all_configured_dbs_keyed": True,
            "paths_emitted": False,
        }
        visible = json.loads(
            McpServer(client).dispatch("tools/call", {"name": "status", "arguments": {}})["content"][0]["text"]
        )
        self.assertEqual(visible["backend_used"], "direct")
        self.assertTrue(visible["live_read_ok"])
        self.assertTrue(visible["metadata_only"])
        self.assertTrue(visible["strict_read_only"])
        self.assertTrue(visible["auto_refresh_disabled"])
        self.assertFalse(visible["fallback_enabled"])
        encoded = json.dumps(visible)
        self.assertNotIn("C:/private", encoded)
        self.assertNotIn("wxid_private", encoded)

    def test_success_payload_preserves_text_ids_cursor_and_normal_media_path(self):
        runner = FakeRunner()
        upstream = {
            "ok": True,
            "data": {
                "query": {"has_more": False, "cursor": {"next": "opaque-cursor"}},
                "messages": [
                    {
                        "id": {"local_id": 123, "server_id_str": "9876543210", "talker": "group@chatroom"},
                        "sender_wxid": "wxid_sender",
                        "text": "user-requested content",
                        "images": [{"path": "D:/WeChat Files/readable.jpg"}],
                    }
                ],
            },
        }
        original = runner.run_json
        runner.run_json = lambda args: upstream if list(args)[:2] == ["call-json", "search"] else original(args)
        visible = json.loads(
            McpServer(WechatCliClient(runner)).dispatch(
                "tools/call", {"name": "search", "arguments": {"keyword": "needle", "limit": 10}}
            )["content"][0]["text"]
        )
        message = visible["data"]["messages"][0]
        self.assertEqual(message["text"], "user-requested content")
        self.assertEqual(message["id"]["local_id"], 123)
        self.assertEqual(message["sender_wxid"], "wxid_sender")
        self.assertEqual(message["images"][0]["path"], "D:/WeChat Files/readable.jpg")
        self.assertEqual(visible["data"]["query"]["cursor"]["next"], "opaque-cursor")

    def test_scoped_chat_label_resolves_in_memory_and_passes_talker(self):
        runner = FakeRunner()
        original = runner.run_json

        def run(arguments):
            args = list(arguments)
            runner.calls.append(args)
            if args[:2] == ["call-json", "contacts"]:
                return {
                    "ok": True,
                    "data": {
                        "contacts": [{"display_name": "Target", "username": "wxid_target"}],
                        "query": {"has_more": False},
                    },
                }
            if args[:2] == ["call-json", "chat_timeline"]:
                return {"ok": True, "data": {"messages": [{"text": "visible"}]}}
            return original(args)

        runner.run_json = run
        server = McpServer(WechatCliClient(runner))
        visible = json.loads(
            server.dispatch(
                "tools/call",
                {"name": "chat_timeline", "arguments": {"chat": "Target", "limit": 20}},
            )["content"][0]["text"]
        )
        self.assertEqual(visible["data"]["messages"][0]["text"], "visible")
        timeline_call = next(call for call in runner.calls if call[:2] == ["call-json", "chat_timeline"])
        forwarded = json.loads(timeline_call[2])
        self.assertEqual(forwarded, {"limit": 20, "talker": "wxid_target"})

    def test_message_context_requires_and_forwards_exact_anchor(self):
        runner = FakeRunner()
        client = WechatCliClient(runner)
        client._candidate_cache = [Candidate("Target", "wxid_target")]
        client._candidate_cache_expires_at = time.monotonic() + 60
        server = McpServer(client)
        with self.assertRaises(InvalidParams):
            server.dispatch("tools/call", {"name": "message_context", "arguments": {"chat": "Target"}})
        server.dispatch(
            "tools/call",
            {
                "name": "message_context",
                "arguments": {"chat": "Target", "local_id": 456, "before_count": 5, "after_count": 7},
            },
        )
        context_call = next(call for call in runner.calls if call[:2] == ["call-json", "message_context"])
        forwarded = json.loads(context_call[2])
        self.assertEqual(forwarded["local_id"], 456)
        self.assertEqual(forwarded["talker"], "wxid_target")
        self.assertEqual(forwarded["before_count"], 5)
        self.assertEqual(forwarded["after_count"], 7)

    def test_ambiguous_resolution_never_calls_scoped_reader(self):
        runner = FakeRunner()
        client = WechatCliClient(runner)
        client._candidate_cache = [Candidate("Alice One", "wxid_one"), Candidate("Alice Two", "wxid_two")]
        client._candidate_cache_expires_at = time.monotonic() + 60
        result = McpServer(client).dispatch(
            "tools/call", {"name": "chat_timeline", "arguments": {"chat": "Alice", "limit": 20}}
        )
        visible = json.loads(result["content"][0]["text"])
        self.assertTrue(result["isError"])
        self.assertTrue(visible["ambiguous"])
        self.assertFalse(any(call[:2] == ["call-json", "chat_timeline"] for call in runner.calls))

    def test_upstream_failure_is_redacted(self):
        runner = FakeRunner()
        original = runner.run_json
        runner.run_json = lambda args: (
            {"ok": False, "error": {"code": "db_error", "message": "C:/private/account key=secret"}}
            if list(args)[:2] == ["call-json", "search"]
            else original(args)
        )
        result = McpServer(WechatCliClient(runner)).dispatch(
            "tools/call", {"name": "search", "arguments": {"keyword": "needle"}}
        )
        encoded = result["content"][0]["text"]
        self.assertTrue(result["isError"])
        self.assertNotIn("C:/private", encoded)
        self.assertNotIn("secret", encoded)

    def test_source_inventory_never_emits_paths_salts_or_keys(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = base / "account-private"
            storage = root / "db_storage" / "message"
            storage.mkdir(parents=True)
            matched_salt = bytes.fromhex("11" * 16)
            (storage / "message_0.db").write_bytes(matched_salt + b"encrypted")
            (storage / "message_0.db-wal").write_bytes(b"wal")
            key_path = base / "managed-key.json"
            key_path.write_text(
                json.dumps({"schema_version": 2, "keys": {matched_salt.hex(): "secret-key"}}),
                encoding="utf-8",
            )
            visible = source_inventory_metadata(
                AdapterConfig(
                    wechat_cli_exe=Path("C:/locked/wechat-cli.exe"),
                    db_account_root=root,
                    managed_key_config=key_path,
                ),
                project_root=base,
            )
            self.assertEqual(visible["configured_db_count"], 1)
            self.assertTrue(visible["all_configured_dbs_keyed"])
            encoded = json.dumps(visible)
            self.assertNotIn(str(root), encoded)
            self.assertNotIn("message_0.db", encoded)
            self.assertNotIn(matched_salt.hex(), encoded)
            self.assertNotIn("secret-key", encoded)

    def test_protocol_notifications_are_not_responses(self):
        server = McpServer(WechatCliClient(FakeRunner()))
        input_stream = io.StringIO(
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}) + "\n"
            + json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n"
            + json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}) + "\n"
        )
        output_stream = io.StringIO()
        serve_jsonl(server, input_stream, output_stream)
        responses = [json.loads(line) for line in output_stream.getvalue().splitlines()]
        self.assertEqual([item["id"] for item in responses], [1, 2])

    def test_locked_runtime_files_have_expected_hashes(self):
        expected = {
            "wechat-cli.exe": "1ad112c4ed10e05757c685698a20d181ab0d75ae3dde3d076895cc6947ae91ed",
            "libWCDB.dll": "beefb9ea3822468116eb86ff49bae6c34e7811916c4c761acef31ec3952da360",
        }
        for name, digest in expected.items():
            payload = (ROOT / "runtime" / "windows-amd64" / name).read_bytes()
            self.assertEqual(hashlib.sha256(payload).hexdigest(), digest)


class LockedRuntimeCatalogTests(unittest.TestCase):
    def test_pinned_binary_attests_every_public_upstream_schema(self):
        if sys.platform != "win32":
            self.skipTest("Windows-only locked runtime")
        exe = (ROOT / "runtime" / "windows-amd64" / "wechat-cli.exe").resolve()
        server = McpServer(WechatCliClient(SubprocessRunner(exe, 30, base_environment={})))
        listed = server.dispatch("tools/list", {})["tools"]
        self.assertEqual([item["name"] for item in listed], EXPECTED_TOOLS)


if __name__ == "__main__":
    unittest.main()
