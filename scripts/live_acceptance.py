"""Metadata-only live acceptance for the candidate stdio MCP.

The script keeps successful WeChat payloads only in process memory. Its report
contains tool names, booleans, counts, safe field names and timings; no message
body, account/chat identifier, key, filename, hash or private path is emitted.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import queue
import re
import subprocess
import sys
import threading
import time
from typing import Any, Iterable, Mapping, Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from adapter.resolver import Candidate, candidates_from_sources, resolve
from adapter.server import AdapterConfig, TOOL_SPECS, validate_locked_runtime


EXPECTED_TOOLS = [spec.name for spec in TOOL_SPECS]
LOCK_PATH = PROJECT_ROOT / "provenance" / "sources.lock.json"
REPORT_PATH = PROJECT_ROOT / ".artifacts" / "live-acceptance-report.json"
PROCESS_NAMES = frozenset({"weixin.exe", "wechat.exe"})
SAFE_FIELD = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,79}$")
TEXT_KEYS = frozenset({"text", "content", "match", "summary", "description", "title"})
COLLECTION_KEYS = (
    "messages",
    "sessions",
    "contacts",
    "events",
    "hits",
    "members",
    "resources",
    "favorites",
    "items",
    "notifications",
    "transfers",
    "red_packets",
    "posts",
    "media",
    "rows",
)


class AcceptanceError(Exception):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def process_count() -> int:
    completed = subprocess.run(
        ["tasklist.exe", "/FO", "CSV", "/NH"],
        shell=False,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )
    if completed.returncode != 0:
        raise AcceptanceError("process_inventory_failed")
    count = 0
    for row in csv.reader(completed.stdout.splitlines()):
        if row and row[0].strip().lower() in PROCESS_NAMES:
            count += 1
    return count


def path_is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def enumerate_source_files(db_root: Path) -> tuple[list[Path], list[Path], list[Path]]:
    storage = db_root.joinpath("db_storage").resolve(strict=True)
    if not storage.is_dir() or not path_is_within(storage, db_root.resolve(strict=True)):
        raise AcceptanceError("invalid_db_storage")
    databases: list[Path] = []
    wal_files: list[Path] = []
    shm_files: list[Path] = []
    for directory, dirnames, filenames in os.walk(storage, followlinks=False):
        directory_path = Path(directory)
        dirnames[:] = [name for name in dirnames if not directory_path.joinpath(name).is_symlink()]
        for filename in filenames:
            candidate = directory_path.joinpath(filename)
            if candidate.is_symlink():
                continue
            resolved = candidate.resolve(strict=True)
            if not path_is_within(resolved, storage):
                raise AcceptanceError("source_path_escape")
            lowered = filename.lower()
            if lowered.endswith(".db"):
                databases.append(resolved)
            elif lowered.endswith(".db-wal"):
                wal_files.append(resolved)
            elif lowered.endswith(".db-shm"):
                shm_files.append(resolved)
    if not databases:
        raise AcceptanceError("no_source_databases")
    return sorted(databases), sorted(wal_files), sorted(shm_files)


def fingerprint_files(paths: Iterable[Path], root: Path, *, hash_content: bool) -> dict[str, tuple[int, int, str | None]]:
    result: dict[str, tuple[int, int, str | None]] = {}
    for path in paths:
        resolved = path.resolve(strict=True)
        if not path_is_within(resolved, root):
            raise AcceptanceError("fingerprint_path_escape")
        stat = resolved.stat()
        token = resolved.relative_to(root).as_posix()
        result[token] = (stat.st_size, stat.st_mtime_ns, sha256_file(resolved) if hash_content else None)
    return result


def fingerprint_tree(root: Path) -> dict[str, tuple[int, int, str]]:
    resolved_root = root.resolve(strict=True)
    paths: list[Path] = []
    for directory, dirnames, filenames in os.walk(resolved_root, followlinks=False):
        directory_path = Path(directory)
        dirnames[:] = [name for name in dirnames if not directory_path.joinpath(name).is_symlink()]
        for filename in filenames:
            candidate = directory_path.joinpath(filename)
            if not candidate.is_symlink():
                paths.append(candidate)
    return {
        key: (size, mtime, digest or "")
        for key, (size, mtime, digest) in fingerprint_files(paths, resolved_root, hash_content=True).items()
    }


def compare_fingerprints(
    before: Mapping[str, tuple[Any, ...]],
    after: Mapping[str, tuple[Any, ...]],
) -> dict[str, int | bool]:
    before_keys = set(before)
    after_keys = set(after)
    shared = before_keys & after_keys
    content_changed = sum(
        1
        for key in shared
        if len(before[key]) >= 3 and len(after[key]) >= 3 and before[key][2] != after[key][2]
    )
    size_changed = sum(1 for key in shared if before[key][0] != after[key][0])
    mtime_changed = sum(1 for key in shared if before[key][1] != after[key][1])
    added = len(after_keys - before_keys)
    removed = len(before_keys - after_keys)
    return {
        "before_count": len(before),
        "after_count": len(after),
        "added": added,
        "removed": removed,
        "content_changed": content_changed,
        "size_changed": size_changed,
        "mtime_changed": mtime_changed,
        "unchanged": added == 0 and removed == 0 and content_changed == 0 and size_changed == 0 and mtime_changed == 0,
    }


def load_state_dir(config_path: Path) -> Path:
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8-sig"))
        value = raw["managed_state_dir"]
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise AcceptanceError("state_dir_config_invalid") from exc
    if not isinstance(value, str):
        raise AcceptanceError("state_dir_config_invalid")
    path = Path(value)
    if not path.is_absolute() or not path.is_dir():
        raise AcceptanceError("state_dir_config_invalid")
    return path.resolve(strict=True)


class JsonlMcpClient:
    def __init__(self, python_exe: Path, config_path: Path) -> None:
        self._process = subprocess.Popen(
            [str(python_exe), "-m", "adapter", "--config", str(config_path)],
            cwd=str(PROJECT_ROOT),
            shell=False,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="strict",
            bufsize=1,
        )
        if self._process.stdin is None or self._process.stdout is None:
            raise AcceptanceError("adapter_stdio_unavailable")
        self._responses: queue.Queue[str | None] = queue.Queue()
        self._reader = threading.Thread(target=self._read_stdout, daemon=True)
        self._reader.start()
        self._next_id = 1

    def _read_stdout(self) -> None:
        assert self._process.stdout is not None
        try:
            for line in self._process.stdout:
                self._responses.put(line)
        finally:
            self._responses.put(None)

    def request(self, method: str, params: Mapping[str, Any] | None = None, *, timeout: float = 180.0) -> dict[str, Any]:
        request_id = self._next_id
        self._next_id += 1
        payload = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": dict(params or {})}
        assert self._process.stdin is not None
        try:
            self._process.stdin.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
            self._process.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise AcceptanceError("adapter_pipe_closed") from exc
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AcceptanceError("adapter_response_timeout")
            try:
                line = self._responses.get(timeout=remaining)
            except queue.Empty as exc:
                raise AcceptanceError("adapter_response_timeout") from exc
            if line is None:
                raise AcceptanceError("adapter_exited")
            try:
                response = json.loads(line)
            except json.JSONDecodeError as exc:
                raise AcceptanceError("adapter_invalid_json") from exc
            if isinstance(response, dict) and response.get("id") == request_id:
                return response

    def initialize(self) -> None:
        response = self.request(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "live-acceptance", "version": "1"},
            },
        )
        if "error" in response:
            raise AcceptanceError("adapter_initialize_failed")

    def call_tool(self, name: str, arguments: Mapping[str, Any]) -> tuple[dict[str, Any] | None, bool, str | None]:
        response = self.request("tools/call", {"name": name, "arguments": dict(arguments)})
        if "error" in response:
            return None, False, "jsonrpc_error"
        result = response.get("result")
        if not isinstance(result, dict):
            return None, False, "invalid_mcp_result"
        is_error = result.get("isError") is True
        content = result.get("content")
        if not isinstance(content, list) or not content or not isinstance(content[0], dict):
            return None, False, "invalid_mcp_content"
        text = content[0].get("text")
        if not isinstance(text, str):
            return None, False, "invalid_mcp_content"
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return None, False, "invalid_tool_json"
        if not isinstance(payload, dict):
            return None, False, "invalid_tool_payload"
        return payload, not is_error and payload.get("ok") is not False, None if not is_error else "tool_error"

    def close(self) -> None:
        if self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait(timeout=10)


def payload_shape(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    if payload is None:
        return {"top_level_fields": [], "data_fields": [], "result_count": 0, "readable_path_count": 0}
    top_level_fields = sorted(key for key in payload if isinstance(key, str) and SAFE_FIELD.fullmatch(key))
    data = payload.get("data")
    data_fields = (
        sorted(key for key in data if isinstance(key, str) and SAFE_FIELD.fullmatch(key))
        if isinstance(data, dict)
        else []
    )
    primary_counts = (
        [len(data[key]) for key in COLLECTION_KEYS if isinstance(data.get(key), list)]
        if isinstance(data, dict)
        else []
    )
    counts: list[int] = []
    readable_path_count = 0

    def walk(value: Any, budget: list[int]) -> None:
        nonlocal readable_path_count
        if budget[0] <= 0:
            return
        budget[0] -= 1
        if isinstance(value, dict):
            for key, child in value.items():
                if key in COLLECTION_KEYS and isinstance(child, list):
                    counts.append(len(child))
                if key == "path" and isinstance(child, str) and child:
                    readable_path_count += 1
                walk(child, budget)
        elif isinstance(value, list):
            for child in value[:1000]:
                walk(child, budget)

    walk(payload, [20_000])
    return {
        "top_level_fields": top_level_fields,
        "data_fields": data_fields,
        "result_count": max(primary_counts, default=max(counts, default=0)),
        "readable_path_count": readable_path_count,
    }


def collection(payload: Mapping[str, Any] | None, names: Sequence[str]) -> list[dict[str, Any]]:
    if payload is None:
        return []
    containers: list[Any] = [payload]
    if isinstance(payload.get("data"), dict):
        containers.append(payload["data"])
    for container_value in containers:
        if not isinstance(container_value, dict):
            continue
        for name in names:
            value = container_value.get(name)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def message_anchor(messages: Sequence[Mapping[str, Any]]) -> tuple[int | None, str | None]:
    for message in reversed(messages):
        identifier = message.get("id") if isinstance(message.get("id"), dict) else message
        local_id = identifier.get("local_id") if isinstance(identifier, dict) else None
        server_id = identifier.get("server_id_str") if isinstance(identifier, dict) else None
        if isinstance(local_id, int) and not isinstance(local_id, bool) and local_id >= 0:
            return local_id, server_id if isinstance(server_id, str) else None
        if isinstance(server_id, str) and re.fullmatch(r"[0-9]{1,20}", server_id):
            return None, server_id
    return None, None


def text_probe(value: Any) -> str | None:
    candidates: list[str] = []

    def walk(item: Any, budget: list[int]) -> None:
        if budget[0] <= 0:
            return
        budget[0] -= 1
        if isinstance(item, dict):
            for key, child in item.items():
                if key in TEXT_KEYS and isinstance(child, str):
                    candidates.append(child)
                else:
                    walk(child, budget)
        elif isinstance(item, list):
            for child in item[:200]:
                walk(child, budget)

    walk(value, [5000])
    for candidate in candidates:
        cleaned = " ".join(candidate.split())
        for match in re.findall(r"[\u3400-\u9fff]{4,12}|[A-Za-z]{5,24}", cleaned):
            if not re.fullmatch(r"(?i)(https?|wechat|wxid|chatroom)", match):
                return match
    return None


def exact_candidate(candidates: Sequence[Candidate], *, group: bool | None = None) -> Candidate | None:
    for candidate in candidates:
        is_group = candidate.internal_chat.lower().endswith("@chatroom")
        if group is not None and is_group != group:
            continue
        resolution = resolve(candidates, candidate.label)
        if resolution.ok and resolution.exact and not resolution.ambiguous:
            return candidate
    return None


def partial_query(candidates: Sequence[Candidate]) -> str | None:
    for candidate in candidates:
        label = candidate.label.strip()
        variants: list[str] = []
        if len(label) > 2:
            variants.extend((label[1:], label[:-1]))
        for width in range(min(8, len(label) - 1), 1, -1):
            for start in range(0, len(label) - width + 1):
                variants.append(label[start : start + width])
        for query_value in variants:
            result = resolve(candidates, query_value)
            if result.ok and result.partial and not result.ambiguous:
                return query_value
    return None


def ambiguous_query(candidates: Sequence[Candidate]) -> str | None:
    seen: set[str] = set()
    for candidate in candidates:
        label = candidate.label.strip()
        for width in (1, 2, 3):
            if width >= len(label):
                continue
            for start in range(0, len(label) - width + 1):
                query_value = label[start : start + width].strip()
                if not query_value or query_value in seen:
                    continue
                seen.add(query_value)
                result = resolve(candidates, query_value)
                if result.ok and result.ambiguous:
                    return query_value
    return None


def safe_status_inventory(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    source = payload.get("source_inventory") if isinstance(payload, dict) else None
    if not isinstance(source, dict):
        return {"available": False}
    result: dict[str, Any] = {"available": True}
    for key, value in source.items():
        if not isinstance(key, str) or not SAFE_FIELD.fullmatch(key):
            continue
        if isinstance(value, bool) or (isinstance(value, int) and not isinstance(value, bool)):
            result[key] = value
    return result


def write_report(report: Mapping[str, Any]) -> None:
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Live strict-read-only MCP acceptance")
    parser.add_argument("--config", required=True, help="absolute machine-private adapter config path")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    started = time.time()
    report: dict[str, Any] = {
        "schema_version": 1,
        "check": "live_candidate_mcp",
        "metadata_only_report": True,
        "payload_persisted": False,
        "snapshot_used": False,
        "fallback_used": False,
        "tools": {},
        "resolver_scenarios": {},
        "errors": [],
    }
    client: JsonlMcpClient | None = None
    baseline_started = False
    before_db: dict[str, tuple[int, int, str | None]] = {}
    before_wal: dict[str, tuple[int, int, str | None]] = {}
    before_shm: dict[str, tuple[int, int, str | None]] = {}
    before_state: dict[str, tuple[int, int, str]] = {}
    config_path: Path | None = None
    config: AdapterConfig | None = None
    db_root: Path | None = None
    state_dir: Path | None = None
    invariant_hashes_before: dict[str, str] = {}
    fatal = False

    def record_call(label: str, name: str, arguments: Mapping[str, Any]) -> dict[str, Any] | None:
        nonlocal fatal
        if client is None:
            raise AcceptanceError("adapter_not_started")
        print(f"acceptance: {label}", flush=True)
        call_started = time.perf_counter()
        payload, ok, error_code = client.call_tool(name, arguments)
        elapsed_ms = round((time.perf_counter() - call_started) * 1000)
        item = {
            "tool": name,
            "called": True,
            "ok": ok,
            "elapsed_ms": elapsed_ms,
            **payload_shape(payload),
        }
        if error_code is not None:
            item["error_code"] = error_code
        report["tools"][label] = item
        if not ok:
            fatal = True
        return payload

    try:
        if os.name != "nt":
            raise AcceptanceError("windows_required")
        config_path = Path(args.config)
        if not config_path.is_absolute():
            raise AcceptanceError("absolute_config_required")
        config_path = config_path.resolve(strict=True)
        if process_count() != 0:
            raise AcceptanceError("weixin_processes_not_zero")

        config = AdapterConfig.from_private_json(config_path)
        validate_locked_runtime(config, LOCK_PATH, current_python=config.python_exe)
        if config.python_exe is None or config.db_account_root is None or config.managed_key_config is None or config.wcdb_dll is None:
            raise AcceptanceError("candidate_config_incomplete")
        db_root = config.db_account_root.resolve(strict=True)
        state_dir = load_state_dir(config_path)
        databases, wal_files, shm_files = enumerate_source_files(db_root)
        storage = db_root.joinpath("db_storage").resolve(strict=True)

        print("acceptance: hashing DB/WAL baseline", flush=True)
        before_db = fingerprint_files(databases, storage, hash_content=True)
        before_wal = fingerprint_files(wal_files, storage, hash_content=True)
        before_shm = fingerprint_files(shm_files, storage, hash_content=True)
        before_state = fingerprint_tree(state_dir)
        invariant_hashes_before = {
            "config": sha256_file(config_path),
            "key_map": sha256_file(config.managed_key_config),
            "wechat_cli": sha256_file(config.wechat_cli_exe),
            "wcdb": sha256_file(config.wcdb_dll),
        }
        baseline_started = True
        report["source_before"] = {
            "db_count": len(before_db),
            "wal_count": len(before_wal),
            "shm_count": len(before_shm),
            "db_bytes": sum(value[0] for value in before_db.values()),
            "wal_bytes": sum(value[0] for value in before_wal.values()),
            "shm_bytes": sum(value[0] for value in before_shm.values()),
            "state_file_count": len(before_state),
        }
        if process_count() != 0:
            raise AcceptanceError("weixin_started_after_baseline")

        client = JsonlMcpClient(config.python_exe, config_path)
        client.initialize()
        list_response = client.request("tools/list", {})
        listed = list_response.get("result", {}).get("tools") if isinstance(list_response.get("result"), dict) else None
        listed_names = [item.get("name") for item in listed if isinstance(item, dict)] if isinstance(listed, list) else []
        report["tool_inventory"] = {
            "count": len(listed_names),
            "exact_match": listed_names == EXPECTED_TOOLS,
            "names": listed_names if listed_names == EXPECTED_TOOLS else [],
        }
        if listed_names != EXPECTED_TOOLS:
            fatal = True
            raise AcceptanceError("candidate_tool_inventory_mismatch")

        status_payload = record_call("status", "status", {})
        status_gates = {
            "backend_direct": isinstance(status_payload, dict) and status_payload.get("backend_used") == "direct",
            "live_read_ok": isinstance(status_payload, dict) and status_payload.get("live_read_ok") is True,
            "metadata_only": isinstance(status_payload, dict) and status_payload.get("metadata_only") is True,
            "strict_read_only": isinstance(status_payload, dict) and status_payload.get("strict_read_only") is True,
            "auto_refresh_disabled": isinstance(status_payload, dict) and status_payload.get("auto_refresh_disabled") is True,
            "fallback_disabled": isinstance(status_payload, dict) and status_payload.get("fallback_enabled") is False,
        }
        report["status_gates"] = status_gates
        report["source_inventory"] = safe_status_inventory(status_payload)
        if not all(status_gates.values()):
            fatal = True
            raise AcceptanceError("status_safety_gate_failed")

        sessions_payload = record_call("sessions", "sessions", {"limit": 100, "offset": 0})
        contacts_payload = record_call("contacts", "contacts", {"limit": 5000, "offset": 0})
        record_call("unread", "unread", {"limit": 20, "offset": 0})
        candidates = candidates_from_sources(sessions_payload, contacts_payload)
        chosen = exact_candidate(candidates)
        if chosen is None:
            raise AcceptanceError("no_unique_chat_candidate")

        exact_payload = record_call("resolve_chat_exact", "resolve_chat", {"query": chosen.label})
        report["resolver_scenarios"]["exact"] = {
            "available": True,
            "ok": isinstance(exact_payload, dict) and exact_payload.get("exact") is True and exact_payload.get("ambiguous") is False,
        }

        partial = partial_query(candidates)
        if partial is not None:
            partial_payload = record_call("resolve_chat_partial", "resolve_chat", {"query": partial})
            report["resolver_scenarios"]["partial"] = {
                "available": True,
                "ok": isinstance(partial_payload, dict) and partial_payload.get("partial") is True and partial_payload.get("ambiguous") is False,
            }
        else:
            report["resolver_scenarios"]["partial"] = {"available": False, "ok": None}

        ambiguous = ambiguous_query(candidates)
        if ambiguous is not None:
            ambiguous_payload, ambiguous_ok, ambiguous_error = client.call_tool("resolve_chat", {"query": ambiguous})
            report["tools"]["resolve_chat_ambiguous"] = {
                "tool": "resolve_chat",
                "called": True,
                "ok": isinstance(ambiguous_payload, dict) and ambiguous_payload.get("ambiguous") is True,
                "expected_tool_error": not ambiguous_ok,
                "error_code": ambiguous_error,
                **payload_shape(ambiguous_payload),
            }
            report["resolver_scenarios"]["ambiguous"] = {
                "available": True,
                "ok": isinstance(ambiguous_payload, dict) and ambiguous_payload.get("ambiguous") is True,
            }
        else:
            report["resolver_scenarios"]["ambiguous"] = {"available": False, "ok": None}

        timeline_payload = record_call("chat_timeline", "chat_timeline", {"chat": chosen.label, "limit": 50})
        messages = collection(timeline_payload, ("messages", "rows"))
        local_id, server_id_str = message_anchor(messages)
        if local_id is None and server_id_str is None:
            raise AcceptanceError("timeline_message_anchor_unavailable")
        context_args: dict[str, Any] = {"chat": chosen.label, "before_count": 5, "after_count": 5}
        if local_id is not None:
            context_args["local_id"] = local_id
        else:
            context_args["server_id_str"] = server_id_str
        record_call("message_context", "message_context", context_args)

        keyword = text_probe(messages)
        if keyword is None:
            raise AcceptanceError("known_hit_keyword_unavailable")
        search_payload = record_call("search", "search", {"keyword": keyword, "limit": 10})
        report["known_hit_search"] = {"hit_count": payload_shape(search_payload)["result_count"], "keyword_persisted": False}
        record_call(
            "search_with_context",
            "search_with_context",
            {"keyword": keyword, "limit": 5, "context_limit": 2, "before_count": 3, "after_count": 3},
        )
        record_call("read_events", "read_events", {"chat": chosen.label, "limit": 10})

        media_args: dict[str, Any] = {"chat": chosen.label, "limit": 10}
        if local_id is not None:
            media_args["local_id"] = local_id
        elif server_id_str is not None:
            media_args["server_id_str"] = server_id_str
        media_payload = record_call("media_resources", "media_resources", media_args)
        media_shape = payload_shape(media_payload)
        if media_shape["result_count"] == 0:
            media_payload = record_call("media_resources_global", "media_resources", {"limit": 200, "offset": 0})
            media_shape = payload_shape(media_payload)
        report["media_validation"] = {
            "nonempty": media_shape["result_count"] > 0,
            "result_count": media_shape["result_count"],
            "readable_path_count": media_shape["readable_path_count"],
        }

        group = exact_candidate(candidates, group=True)
        if group is not None:
            record_call("group_members", "group_members", {"chat": group.label, "limit": 20})
        else:
            report["tools"]["group_members"] = {"tool": "group_members", "called": False, "ok": None, "reason_code": "no_group_candidate"}
            fatal = True

        record_call("favorites", "favorites", {"limit": 10, "offset": 0})
        moments_payload = record_call("moments_feed", "moments_feed", {"limit": 10, "offset": 0})
        moments_keyword = text_probe(moments_payload) or "wechatlocalplatformnomatchprobe"
        record_call("moments_search", "moments_search", {"keyword": moments_keyword, "limit": 10, "offset": 0})
        report["moments_search_probe_from_live_content"] = text_probe(moments_payload) is not None
        record_call("moments_notifications", "moments_notifications", {"limit": 10, "offset": 0})
        record_call("transfers", "transfers", {"limit": 10, "offset": 0})
        record_call("red_packets", "red_packets", {"limit": 10, "offset": 0})

    except Exception as exc:
        fatal = True
        report["errors"].append({"code": type(exc).__name__, "stage_code": exc.args[0] if isinstance(exc, AcceptanceError) and exc.args else "unexpected_error"})
    finally:
        if client is not None:
            client.close()
        try:
            report["weixin_process_count_after"] = process_count()
            if report["weixin_process_count_after"] != 0:
                fatal = True
        except Exception:
            report["weixin_process_count_after"] = None
            fatal = True

        if baseline_started and config is not None and config_path is not None and db_root is not None and state_dir is not None:
            try:
                print("acceptance: hashing DB/WAL after", flush=True)
                databases_after, wal_after, shm_after = enumerate_source_files(db_root)
                storage = db_root.joinpath("db_storage").resolve(strict=True)
                after_db = fingerprint_files(databases_after, storage, hash_content=True)
                after_wal = fingerprint_files(wal_after, storage, hash_content=True)
                after_shm = fingerprint_files(shm_after, storage, hash_content=True)
                after_state = fingerprint_tree(state_dir)
                invariant_hashes_after = {
                    "config": sha256_file(config_path),
                    "key_map": sha256_file(config.managed_key_config),
                    "wechat_cli": sha256_file(config.wechat_cli_exe),
                    "wcdb": sha256_file(config.wcdb_dll),
                }
                db_diff = compare_fingerprints(before_db, after_db)
                wal_diff = compare_fingerprints(before_wal, after_wal)
                shm_diff = compare_fingerprints(before_shm, after_shm)
                state_diff = compare_fingerprints(before_state, after_state)
                invariant_changes = sum(
                    1 for key, value in invariant_hashes_before.items() if invariant_hashes_after.get(key) != value
                )
                report["immutability"] = {
                    "db": db_diff,
                    "wal": wal_diff,
                    "shm": shm_diff,
                    "state": state_diff,
                    "runtime_config_keymap_changed_count": invariant_changes,
                    "original_db_wal_written_by_access": not (db_diff["unchanged"] and wal_diff["unchanged"]),
                }
                if not db_diff["unchanged"] or not wal_diff["unchanged"] or not state_diff["unchanged"] or invariant_changes != 0:
                    fatal = True
            except Exception as exc:
                fatal = True
                report["errors"].append({"code": type(exc).__name__, "stage_code": "post_fingerprint_failed"})

        tool_outcomes = [item for item in report.get("tools", {}).values() if isinstance(item, dict) and item.get("called") is True]
        expected_ambiguity = report.get("resolver_scenarios", {}).get("ambiguous", {}).get("ok") is True
        ordinary_failures = [
            item for key, item in report.get("tools", {}).items()
            if isinstance(item, dict) and item.get("called") is True and item.get("ok") is not True and key != "resolve_chat_ambiguous"
        ]
        report["summary"] = {
            "called_scenario_count": len(tool_outcomes),
            "ordinary_failure_count": len(ordinary_failures),
            "ambiguous_scenario_passed": expected_ambiguity,
            "all_expected_tools_listed": report.get("tool_inventory", {}).get("exact_match") is True,
        }
        if ordinary_failures:
            fatal = True
        report["passed"] = not fatal
        report["duration_ms"] = round((time.time() - started) * 1000)
        write_report(report)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
