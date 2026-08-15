"""Fail-closed JSONL stdio MCP adapter for the local WeChat read platform.

Successful read responses preserve the safe, structured upstream payload. The
adapter has no database, key-extraction, cache mutation, update, export, SQL,
ASR, companion, elevation, send, or arbitrary-command surface. It delegates
only the statically listed read operations to a pinned absolute executable.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import subprocess
import sys
import sysconfig
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence, TextIO

try:  # Package execution is the production path; direct import aids tests.
    from .resolver import Resolution, candidates_from_sources, resolve
except ImportError:  # pragma: no cover - exercised only by direct script imports.
    from resolver import Resolution, candidates_from_sources, resolve


MCP_PROTOCOL_VERSION = "2024-11-05"
MAX_JSON_LINE_BYTES = 4 * 1024 * 1024
MAX_UPSTREAM_OUTPUT_CHARS = 16 * 1024 * 1024
RESOLVER_PAGE_SIZE = 5000
MAX_RESOLVER_SOURCE_ROWS = 1_000_000
RESOLVER_CACHE_SECONDS = 60.0
BACKEND_NAME = "direct"
LOCAL_TOOL_NAMES = frozenset({"resolve_chat"})
PRIVATE_UPSTREAM_ENV_FIELDS: Mapping[str, str] = {
    "wcdb_dll": "WECHAT_CLI_WCDB_LIB",
    "db_account_root": "WECHAT_CLI_DB_ROOT",
    "managed_key_config": "WECHAT_CLI_CONFIG",
    "managed_state_dir": "WECHAT_CLI_STATE_DIR",
}
ALLOWED_UPSTREAM_ENV = frozenset(PRIVATE_UPSTREAM_ENV_FIELDS.values())

# These are the db_storage roots reached by the fixed eighteen-tool MCP
# surface in the locked v1.6.20 engine.  New Weixin database families remain
# visible in aggregate diagnostics, but they are not treated as a dependency
# of tools that cannot address them.
SUPPORTED_DIRECT_DB_SUBDIRS = frozenset(
    {"contact", "favorite", "general", "message", "session", "sns"}
)


@dataclass(frozen=True)
class ToolSpec:
    name: str
    upstream: str
    description: str
    catalog_aliases: tuple[str, ...] = ()


TOOL_SPECS: tuple[ToolSpec, ...] = (
    ToolSpec("status", "read_os", "Return a metadata-only, redacted direct-reader readiness summary.", ("status",)),
    ToolSpec("sessions", "sessions", "List recent local sessions."),
    ToolSpec("unread", "unread", "List unread sessions when the direct reader reports this capability."),
    ToolSpec("resolve_chat", "resolve-chat", "Resolve a chat label before a scoped read."),
    ToolSpec("chat_timeline", "chat_timeline", "Read and page a bounded direct chat timeline.", ("timeline",)),
    ToolSpec("message_context", "message_context", "Read exact bounded context around a known message anchor.", ("context",)),
    ToolSpec("search", "search", "Search the live encrypted message FTS."),
    ToolSpec("search_with_context", "search_with_context", "Search live messages and expand bounded context.", ("search-context",)),
    ToolSpec("read_events", "read_events", "Read bounded incremental message or session events.", ("tail",)),
    ToolSpec("contacts", "contacts", "List local contacts."),
    ToolSpec("group_members", "group_members", "List members of one group.", ("members",)),
    ToolSpec("media_resources", "media_resources", "Resolve already available local media resources.", ("media",)),
    ToolSpec("favorites", "favorites", "Read bounded WeChat favorites."),
    ToolSpec("moments_feed", "sns_feed", "Read a bounded Moments feed.", ("sns-feed",)),
    ToolSpec("moments_search", "sns_search", "Search bounded Moments content.", ("sns-search",)),
    ToolSpec("moments_notifications", "sns_notifications", "Read bounded Moments notifications.", ("sns-notifications",)),
    ToolSpec("transfers", "transfers", "Read bounded transfer records."),
    ToolSpec("red_packets", "red_packets", "Read bounded red-packet records.", ("red-packets",)),
)
TOOL_BY_NAME: Mapping[str, ToolSpec] = {spec.name: spec for spec in TOOL_SPECS}
LOCAL_RESOLVE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "query": {"type": "string", "minLength": 1, "maxLength": 200},
    },
    "required": ["query"],
    "additionalProperties": False,
}

# The public surface keeps normal read filters, pagination anchors, message
# identifiers and cursors. Maintenance/debug fields that can trigger writes or
# reveal low-level key material stay absent. Successful output is not rewritten.
PUBLIC_SCHEMAS: Mapping[str, dict[str, Any]] = {
    "status": {"type": "object", "properties": {}, "additionalProperties": False},
    "sessions": {
        "type": "object",
        "properties": {
            "limit": {"type": "integer", "minimum": 1, "maximum": 5000},
            "offset": {"type": "integer", "minimum": 0, "maximum": 1000000},
            "type_filter": {"type": "string", "minLength": 1, "maxLength": 100},
            "keyword": {"type": "string", "minLength": 1, "maxLength": 200},
        },
        "additionalProperties": False,
    },
    "unread": {
        "type": "object",
        "properties": {
            "limit": {"type": "integer", "minimum": 1, "maximum": 5000},
            "offset": {"type": "integer", "minimum": 0, "maximum": 1000000},
            "type_filter": {"type": "string", "minLength": 1, "maxLength": 100},
            "filter": {"type": "string", "minLength": 1, "maxLength": 100},
        },
        "additionalProperties": False,
    },
    "resolve_chat": LOCAL_RESOLVE_SCHEMA,
    "chat_timeline": {
        "type": "object",
        "properties": {
            "chat": {"type": "string", "minLength": 1, "maxLength": 200},
            "talker": {"type": "string", "minLength": 1, "maxLength": 200},
            "limit": {"type": "integer", "minimum": 1, "maximum": 500},
            "offset": {"type": "integer", "minimum": 0, "maximum": 1000000},
            "after": {"type": "string", "minLength": 1, "maxLength": 64},
            "before": {"type": "string", "minLength": 1, "maxLength": 64},
            "before_message": {"type": "integer", "minimum": 0},
            "after_message": {"type": "integer", "minimum": 0},
            "before_server_id_str": {"type": "string", "pattern": "^[0-9]{1,20}$"},
            "after_server_id_str": {"type": "string", "pattern": "^[0-9]{1,20}$"},
            "keyword": {"type": "string", "minLength": 1, "maxLength": 500},
            "type": {"type": "string", "minLength": 1, "maxLength": 64},
            "kind_name": {"type": "string", "minLength": 1, "maxLength": 64},
            "base_kind": {"type": "integer", "minimum": 0, "maximum": 100000},
            "sender": {"type": "string", "minLength": 1, "maxLength": 200},
            "from_me": {"type": "boolean"},
            "order": {"type": "string", "enum": ["desc", "asc"]},
            "display_order": {"type": "string", "enum": ["query", "desc", "asc"]},
        },
        "additionalProperties": False,
    },
    "message_context": {
        "type": "object",
        "properties": {
            "chat": {"type": "string", "minLength": 1, "maxLength": 200},
            "talker": {"type": "string", "minLength": 1, "maxLength": 200},
            "local_id": {"type": "integer", "minimum": 0},
            "message_local_id": {"type": "integer", "minimum": 0},
            "server_id": {"type": "integer", "minimum": 0},
            "server_id_str": {"type": "string", "pattern": "^[0-9]{1,20}$"},
            "message_server_id": {"type": "integer", "minimum": 0},
            "message_server_id_str": {"type": "string", "pattern": "^[0-9]{1,20}$"},
            "before_count": {"type": "integer", "minimum": 0, "maximum": 500},
            "after_count": {"type": "integer", "minimum": 0, "maximum": 500},
            "limit": {"type": "integer", "minimum": 0, "maximum": 500},
            "include_anchor": {"type": "boolean"},
            "display_order": {"type": "string", "enum": ["asc", "desc"]},
        },
        "additionalProperties": False,
    },
    "search": {
        "type": "object",
        "properties": {
            "keyword": {"type": "string", "minLength": 1, "maxLength": 100},
            "chat": {"type": "string", "minLength": 1, "maxLength": 200},
            "talker": {"type": "string", "minLength": 1, "maxLength": 200},
            "after": {"type": "string", "minLength": 1, "maxLength": 64},
            "before": {"type": "string", "minLength": 1, "maxLength": 64},
            "type": {"type": "string", "minLength": 1, "maxLength": 64},
            "kind_name": {"type": "string", "minLength": 1, "maxLength": 64},
            "base_kind": {"type": "integer", "minimum": 0, "maximum": 100000},
            "sender": {"type": "string", "minLength": 1, "maxLength": 200},
            "from_me": {"type": "boolean"},
            "offset": {"type": "integer", "minimum": 0, "maximum": 1000000},
            "max_text_chars": {"type": "integer", "minimum": 1, "maximum": 2000},
            "snippet_only": {"type": "boolean"},
            "include_text": {"type": "boolean"},
            "search_mode": {"type": "string", "enum": ["fts", "like", "auto"]},
            "limit": {"type": "integer", "minimum": 1, "maximum": 500},
        },
        "required": ["keyword"],
        "additionalProperties": False,
    },
    "search_with_context": {
        "type": "object",
        "properties": {
            "keyword": {"type": "string", "minLength": 1, "maxLength": 100},
            "chat": {"type": "string", "minLength": 1, "maxLength": 200},
            "talker": {"type": "string", "minLength": 1, "maxLength": 200},
            "after": {"type": "string", "minLength": 1, "maxLength": 64},
            "before": {"type": "string", "minLength": 1, "maxLength": 64},
            "type": {"type": "string", "minLength": 1, "maxLength": 64},
            "kind_name": {"type": "string", "minLength": 1, "maxLength": 64},
            "base_kind": {"type": "integer", "minimum": 0, "maximum": 100000},
            "sender": {"type": "string", "minLength": 1, "maxLength": 200},
            "from_me": {"type": "boolean"},
            "max_text_chars": {"type": "integer", "minimum": 1, "maximum": 2000},
            "snippet_only": {"type": "boolean"},
            "include_text": {"type": "boolean"},
            "search_mode": {"type": "string", "enum": ["fts", "like", "auto"]},
            "limit": {"type": "integer", "minimum": 1, "maximum": 200},
            "context_limit": {"type": "integer", "minimum": 0, "maximum": 20},
            "before_count": {"type": "integer", "minimum": 0, "maximum": 500},
            "after_count": {"type": "integer", "minimum": 0, "maximum": 500},
        },
        "required": ["keyword"],
        "additionalProperties": False,
    },
    "read_events": {
        "type": "object",
        "properties": {
            "mode": {"type": "string", "enum": ["auto", "messages", "sessions"]},
            "chat": {"type": "string", "minLength": 1, "maxLength": 200},
            "talker": {"type": "string", "minLength": 1, "maxLength": 200},
            "cursor": {"type": "string", "minLength": 1, "maxLength": 500},
            "since_local_id": {"type": "integer", "minimum": 0},
            "since_time": {"type": "string", "minLength": 1, "maxLength": 64},
            "after": {"type": "string", "minLength": 1, "maxLength": 64},
            "type": {"type": "string", "minLength": 1, "maxLength": 64},
            "kind_name": {"type": "string", "minLength": 1, "maxLength": 64},
            "sender": {"type": "string", "minLength": 1, "maxLength": 200},
            "from_me": {"type": "boolean"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 1000},
            "scan_limit": {"type": "integer", "minimum": 1, "maximum": 5000},
        },
        "additionalProperties": False,
    },
    "contacts": {
        "type": "object",
        "properties": {
            "limit": {"type": "integer", "minimum": 1, "maximum": 5000},
            "offset": {"type": "integer", "minimum": 0, "maximum": 1000000},
            "keyword": {"type": "string", "minLength": 1, "maxLength": 200},
            "groups_only": {"type": "boolean"},
            "friends_only": {"type": "boolean"},
        },
        "additionalProperties": False,
    },
    "group_members": {
        "type": "object",
        "properties": {
            "chat": {"type": "string", "minLength": 1, "maxLength": 200},
            "chatroom_id": {"type": "string", "minLength": 1, "maxLength": 200},
            "stats": {"type": "boolean"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 5000},
            "offset": {"type": "integer", "minimum": 0, "maximum": 1000000},
        },
        "additionalProperties": False,
    },
    "media_resources": {
        "type": "object",
        "properties": {
            "chat": {"type": "string", "minLength": 1, "maxLength": 200},
            "talker": {"type": "string", "minLength": 1, "maxLength": 200},
            "local_id": {"type": "integer", "minimum": 0},
            "server_id": {"type": "integer", "minimum": 0},
            "server_id_str": {"type": "string", "pattern": "^[0-9]{1,20}$"},
            "message_server_id": {"type": "integer", "minimum": 0},
            "message_server_id_str": {"type": "string", "pattern": "^[0-9]{1,20}$"},
            "after": {"type": "string", "minLength": 1, "maxLength": 64},
            "before": {"type": "string", "minLength": 1, "maxLength": 64},
            "sender": {"type": "string", "minLength": 1, "maxLength": 200},
            "from_me": {"type": "boolean"},
            "type": {"type": "string", "minLength": 1, "maxLength": 64},
            "kind_name": {"type": "string", "minLength": 1, "maxLength": 64},
            "base_kind": {"type": "integer", "minimum": 0, "maximum": 100000},
            "resource_family": {"type": "string", "enum": ["image", "video", "file", "cover", "unknown"]},
            "resource_type_raw": {"type": "integer"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 500},
            "offset": {"type": "integer", "minimum": 0, "maximum": 1000000},
        },
        "additionalProperties": False,
    },
    "favorites": {
        "type": "object",
        "properties": {
            "limit": {"type": "integer", "minimum": 1, "maximum": 500},
            "offset": {"type": "integer", "minimum": 0, "maximum": 1000000},
            "after": {"type": "string", "minLength": 1, "maxLength": 64},
            "before": {"type": "string", "minLength": 1, "maxLength": 64},
        },
        "additionalProperties": False,
    },
    "moments_feed": {
        "type": "object",
        "properties": {
            "keyword": {"type": "string", "minLength": 1, "maxLength": 500},
            "user": {"type": "string", "minLength": 1, "maxLength": 200},
            "after": {"type": "string", "minLength": 1, "maxLength": 64},
            "before": {"type": "string", "minLength": 1, "maxLength": 64},
            "limit": {"type": "integer", "minimum": 1, "maximum": 500},
            "offset": {"type": "integer", "minimum": 0, "maximum": 1000000},
        },
        "additionalProperties": False,
    },
    "moments_search": {
        "type": "object",
        "properties": {
            "keyword": {"type": "string", "minLength": 1, "maxLength": 500},
            "user": {"type": "string", "minLength": 1, "maxLength": 200},
            "after": {"type": "string", "minLength": 1, "maxLength": 64},
            "before": {"type": "string", "minLength": 1, "maxLength": 64},
            "limit": {"type": "integer", "minimum": 1, "maximum": 500},
            "offset": {"type": "integer", "minimum": 0, "maximum": 1000000},
        },
        "required": ["keyword"],
        "additionalProperties": False,
    },
    "moments_notifications": {
        "type": "object",
        "properties": {
            "include_read": {"type": "boolean"},
            "after": {"type": "string", "minLength": 1, "maxLength": 64},
            "before": {"type": "string", "minLength": 1, "maxLength": 64},
            "limit": {"type": "integer", "minimum": 1, "maximum": 500},
            "offset": {"type": "integer", "minimum": 0, "maximum": 1000000},
        },
        "additionalProperties": False,
    },
    "transfers": {
        "type": "object",
        "properties": {
            "limit": {"type": "integer", "minimum": 1, "maximum": 500},
            "offset": {"type": "integer", "minimum": 0, "maximum": 1000000},
            "after": {"type": "string", "minLength": 1, "maxLength": 64},
            "before": {"type": "string", "minLength": 1, "maxLength": 64},
        },
        "additionalProperties": False,
    },
    "red_packets": {
        "type": "object",
        "properties": {
            "limit": {"type": "integer", "minimum": 1, "maximum": 500},
            "offset": {"type": "integer", "minimum": 0, "maximum": 1000000},
            "chat": {"type": "string", "minLength": 1, "maxLength": 200},
            "talker": {"type": "string", "minLength": 1, "maxLength": 200},
            "sender": {"type": "string", "minLength": 1, "maxLength": 200},
            "after": {"type": "string", "minLength": 1, "maxLength": 64},
            "before": {"type": "string", "minLength": 1, "maxLength": 64},
        },
        "additionalProperties": False,
    },
}

REQUIRED_SCOPE_TOOLS = frozenset({"chat_timeline", "message_context", "group_members"})
OPTIONAL_SCOPE_TOOLS = frozenset(
    {"search", "search_with_context", "read_events", "media_resources", "red_packets"}
)
SCOPED_TOOL_NAMES = REQUIRED_SCOPE_TOOLS | OPTIONAL_SCOPE_TOOLS
MESSAGE_ANCHOR_KEYS = frozenset(
    {
        "local_id",
        "message_local_id",
        "server_id",
        "server_id_str",
        "message_server_id",
        "message_server_id_str",
    }
)


class AdapterError(Exception):
    """Base class for errors safe to convert to protocol errors."""


class ConfigError(AdapterError):
    pass


class UpstreamError(AdapterError):
    pass


class InvalidParams(AdapterError):
    pass


@dataclass(frozen=True)
class AdapterConfig:
    """Configuration loaded only from an explicit machine-private JSON file."""

    wechat_cli_exe: Path
    wcdb_dll: Path | None = None
    python_exe: Path | None = None
    python_sha256: str | None = None
    timeout_seconds: int = 30
    upstream_environment: tuple[tuple[str, str], ...] = ()
    db_account_root: Path | None = None
    managed_key_config: Path | None = None

    @classmethod
    def from_private_json(cls, config_path: str | os.PathLike[str]) -> "AdapterConfig":
        path = Path(config_path)
        if not path.is_absolute():
            raise ConfigError("machine-private config path must be absolute")
        if path.suffix.lower() != ".json" or not path.is_file():
            raise ConfigError("machine-private config must be an existing JSON file")

        try:
            with path.open("r", encoding="utf-8-sig") as handle:
                raw = json.load(handle)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ConfigError("machine-private config could not be read as JSON") from exc

        if not isinstance(raw, dict):
            raise ConfigError("machine-private config must contain a JSON object")
        # This is the fixed project machine schema.  Only the four fields in
        # PRIVATE_UPSTREAM_ENV_FIELDS are ever forwarded to the child process.
        allowed = {
            "schema_version",
            "python_exe",
            "python_sha256",
            "wechat_cli_exe",
            "wcdb_dll",
            "db_account_root",
            "managed_key_config",
            "managed_state_dir",
            "codex_config",
            "cli_timeout_seconds",
            # Retained for the standalone adapter's early beta config files.
            "timeout_seconds",
        }
        if set(raw) - allowed:
            raise ConfigError("machine-private config contains unsupported fields")
        if raw.get("schema_version", 1) != 1:
            raise ConfigError("machine-private config schema_version must be 1")
        if set(PRIVATE_UPSTREAM_ENV_FIELDS) - set(raw):
            raise ConfigError("machine-private config is missing pinned runtime or data paths")
        python_hash = raw.get("python_sha256")
        python_value = raw.get("python_exe")
        if (python_hash is None) != (python_value is None):
            raise ConfigError("python_exe and python_sha256 must be provided together")
        if python_hash is not None and (not isinstance(python_hash, str) or re.fullmatch(r"[0-9a-f]{64}", python_hash) is None):
            raise ConfigError("python_sha256 must be a lowercase SHA-256 value")
        resolved_python: Path | None = None
        if python_value is not None:
            if not isinstance(python_value, str) or not python_value:
                raise ConfigError("python_exe must be an existing absolute .exe file")
            candidate_python = Path(python_value)
            if not candidate_python.is_absolute() or candidate_python.suffix.lower() != ".exe" or not candidate_python.is_file():
                raise ConfigError("python_exe must be an existing absolute .exe file")
            try:
                resolved_python = candidate_python.resolve(strict=True)
            except OSError as exc:
                raise ConfigError("python_exe could not be resolved safely") from exc

        exe_value = raw.get("wechat_cli_exe")
        if not isinstance(exe_value, str) or not exe_value:
            raise ConfigError("machine-private config requires wechat_cli_exe")
        exe_path = Path(exe_value)
        if not exe_path.is_absolute() or exe_path.suffix.lower() != ".exe" or not exe_path.is_file():
            raise ConfigError("wechat_cli_exe must be an existing absolute .exe file")

        if "cli_timeout_seconds" in raw and "timeout_seconds" in raw:
            raise ConfigError("machine-private config contains two timeout fields")
        timeout = raw.get("cli_timeout_seconds", raw.get("timeout_seconds", 30))
        if isinstance(timeout, bool) or not isinstance(timeout, int) or not 1 <= timeout <= 120:
            raise ConfigError("CLI timeout must be an integer from 1 through 120")

        private_environment: dict[str, str] = {}
        resolved_wcdb: Path | None = None
        resolved_db_account_root: Path | None = None
        resolved_managed_key_config: Path | None = None
        for field_name, environment_name in PRIVATE_UPSTREAM_ENV_FIELDS.items():
            if field_name not in raw:
                continue
            value = raw[field_name]
            if not isinstance(value, str) or not value:
                raise ConfigError(f"{field_name} must be a non-empty absolute path")
            private_path = Path(value)
            if not private_path.is_absolute():
                raise ConfigError(f"{field_name} must be a non-empty absolute path")
            expects_directory = field_name in {"db_account_root", "managed_state_dir"}
            if expects_directory and not private_path.is_dir():
                raise ConfigError(f"{field_name} must be an existing directory")
            if not expects_directory and not private_path.is_file():
                raise ConfigError(f"{field_name} must be an existing file")
            if field_name == "wcdb_dll" and private_path.suffix.lower() != ".dll":
                raise ConfigError("wcdb_dll must be an existing absolute .dll file")
            if field_name == "managed_key_config" and private_path.suffix.lower() != ".json":
                raise ConfigError("managed_key_config must be an existing absolute JSON file")
            try:
                resolved_private_path = private_path.resolve(strict=True)
                private_environment[environment_name] = str(resolved_private_path)
                if field_name == "wcdb_dll":
                    resolved_wcdb = resolved_private_path
                elif field_name == "db_account_root":
                    resolved_db_account_root = resolved_private_path
                elif field_name == "managed_key_config":
                    resolved_managed_key_config = resolved_private_path
            except OSError as exc:
                raise ConfigError(f"{field_name} could not be resolved safely") from exc

        # Resolve only after all validation; no user-supplied path is included in errors.
        try:
            resolved_executable = exe_path.resolve(strict=True)
        except OSError as exc:
            raise ConfigError("wechat_cli_exe could not be resolved safely") from exc
        return cls(
            wechat_cli_exe=resolved_executable,
            wcdb_dll=resolved_wcdb,
            python_exe=resolved_python,
            python_sha256=python_hash,
            timeout_seconds=timeout,
            upstream_environment=tuple(sorted(private_environment.items())),
            db_account_root=resolved_db_account_root,
            managed_key_config=resolved_managed_key_config,
        )


def _local_timestamp(value: float) -> str:
    return datetime.fromtimestamp(value, timezone.utc).astimezone().isoformat(timespec="seconds")


def _path_is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def source_inventory_metadata(
    config: AdapterConfig,
    *,
    project_root: Path | None = None,
) -> dict[str, Any]:
    """Inspect encrypted source file metadata and DB salts without reading rows.

    The result is intentionally limited to counts, booleans, and timestamps.
    Paths, filenames, salts, key values, account identifiers, and DB contents
    never leave this function.
    """

    root = config.db_account_root
    key_config = config.managed_key_config
    if root is None or key_config is None:
        raise ConfigError("source inventory paths are unavailable")
    try:
        root = root.resolve(strict=True)
        db_storage = root.joinpath("db_storage").resolve(strict=True)
        key_config = key_config.resolve(strict=True)
    except OSError as exc:
        raise ConfigError("source inventory paths could not be resolved") from exc
    if not db_storage.is_dir() or not _path_is_within(db_storage, root):
        raise ConfigError("configured source root has no safe db_storage directory")

    files: list[Path] = []
    try:
        for directory, dirnames, filenames in os.walk(db_storage, followlinks=False):
            directory_path = Path(directory)
            dirnames[:] = [name for name in dirnames if not directory_path.joinpath(name).is_symlink()]
            for filename in filenames:
                candidate = directory_path.joinpath(filename)
                if candidate.is_symlink():
                    continue
                lowered = filename.lower()
                if lowered.endswith((".db", ".db-wal", ".db-shm")):
                    resolved = candidate.resolve(strict=True)
                    if _path_is_within(resolved, db_storage):
                        files.append(resolved)
    except OSError as exc:
        raise ConfigError("source inventory could not enumerate encrypted files") from exc

    databases = [item for item in files if item.name.lower().endswith(".db")]
    wal_files = [item for item in files if item.name.lower().endswith(".db-wal")]
    shm_files = [item for item in files if item.name.lower().endswith(".db-shm")]
    if not databases:
        raise ConfigError("source inventory found no encrypted databases")

    try:
        with key_config.open("r", encoding="utf-8-sig") as stream:
            key_payload = json.load(stream)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ConfigError("managed key map could not be inventoried") from exc
    keys = key_payload.get("keys") if isinstance(key_payload, dict) else None
    if not isinstance(key_payload, dict) or key_payload.get("schema_version") != 2 or not isinstance(keys, dict):
        raise ConfigError("managed key map is not schema 2")
    salt_tokens = {
        token.lower()
        for token in keys
        if isinstance(token, str) and re.fullmatch(r"[0-9a-fA-F]{32}", token)
    }
    key_map_shape_valid = len(salt_tokens) == len(keys)

    def supported_by_public_tools(database: Path) -> bool:
        try:
            relative = database.relative_to(db_storage)
        except ValueError:
            return False
        return bool(relative.parts) and relative.parts[0].lower() in SUPPORTED_DIRECT_DB_SUBDIRS

    supported_databases = [item for item in databases if supported_by_public_tools(item)]
    unsupported_databases = [item for item in databases if not supported_by_public_tools(item)]
    keyed_db_count = 0
    missing_key_db_count = 0
    unreadable_db_header_count = 0
    supported_keyed_db_count = 0
    supported_missing_key_db_count = 0
    supported_unreadable_db_header_count = 0
    for database in databases:
        supported = supported_by_public_tools(database)
        try:
            with database.open("rb") as stream:
                salt = stream.read(16)
        except OSError:
            unreadable_db_header_count += 1
            if supported:
                supported_unreadable_db_header_count += 1
            continue
        if len(salt) != 16:
            unreadable_db_header_count += 1
            if supported:
                supported_unreadable_db_header_count += 1
        elif salt.hex() in salt_tokens:
            keyed_db_count += 1
            if supported:
                supported_keyed_db_count += 1
        else:
            missing_key_db_count += 1
            if supported:
                supported_missing_key_db_count += 1

    def latest_timestamp(paths: Sequence[Path]) -> str | None:
        try:
            value = max((item.stat().st_mtime for item in paths), default=None)
        except OSError as exc:
            raise ConfigError("source inventory timestamps could not be read") from exc
        return _local_timestamp(value) if value is not None else None

    source_latest = latest_timestamp([*databases, *wal_files])
    message_databases = [
        item
        for item in supported_databases
        if item.relative_to(db_storage).parts[0].lower() == "message"
    ]
    marker_pattern = re.compile(r"(?i)(?:backup|snapshot|cache|temp|archive|benchmark)")
    root_has_backup_marker = any(marker_pattern.search(part) is not None for part in root.parts)
    configured_root_inside_project = False
    if project_root is not None:
        try:
            configured_root_inside_project = _path_is_within(root, project_root.resolve(strict=True))
        except OSError:
            configured_root_inside_project = False

    return {
        "configured_db_count": len(databases),
        "configured_wal_count": len(wal_files),
        "configured_shm_count": len(shm_files),
        "message_db_count": len(message_databases),
        "public_tool_required_db_count": len(supported_databases),
        "public_tool_unaddressed_db_count": len(unsupported_databases),
        "key_map_entry_count": len(keys),
        "keyed_db_count": keyed_db_count,
        "missing_key_db_count": missing_key_db_count,
        "unreadable_db_header_count": unreadable_db_header_count,
        "public_tool_keyed_db_count": supported_keyed_db_count,
        "public_tool_missing_key_db_count": supported_missing_key_db_count,
        "public_tool_unreadable_db_header_count": supported_unreadable_db_header_count,
        "unaddressed_keyed_db_count": keyed_db_count - supported_keyed_db_count,
        "unaddressed_missing_key_db_count": missing_key_db_count - supported_missing_key_db_count,
        "unaddressed_unreadable_db_header_count": (
            unreadable_db_header_count - supported_unreadable_db_header_count
        ),
        "key_map_shape_valid": key_map_shape_valid,
        "all_public_tool_dbs_keyed": (
            key_map_shape_valid
            and bool(supported_databases)
            and supported_keyed_db_count == len(supported_databases)
            and supported_missing_key_db_count == 0
            and supported_unreadable_db_header_count == 0
        ),
        "all_configured_dbs_keyed": (
            key_map_shape_valid
            and keyed_db_count == len(databases)
            and missing_key_db_count == 0
            and unreadable_db_header_count == 0
        ),
        "latest_db_mtime": latest_timestamp(databases),
        "latest_wal_mtime": latest_timestamp(wal_files),
        "latest_db_or_wal_mtime": source_latest,
        "latest_message_db_mtime": latest_timestamp(message_databases),
        "configured_root_inside_project": configured_root_inside_project,
        "configured_root_has_backup_marker": root_has_backup_marker,
        "paths_emitted": False,
        "filenames_emitted": False,
        "salts_emitted": False,
        "key_values_emitted": False,
        "message_content_read": False,
    }


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_locked_runtime(
    config: AdapterConfig,
    lock_path: Path,
    *,
    current_python: Path | None = None,
) -> None:
    """Fail closed if any executable component differs from the project lock."""

    if config.wcdb_dll is None or config.python_exe is None or config.python_sha256 is None:
        raise ConfigError("machine-private config is missing locked runtime identity")
    try:
        with lock_path.open("r", encoding="utf-8-sig") as stream:
            lock = json.load(stream)
        exe_hash = lock["wechat_cli"]["inner_files"]["wechat-cli.exe"]["sha256"]
        dll_hash = lock["wechat_cli"]["inner_files"]["libWCDB.dll"]["sha256"]
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise ConfigError("sources lock could not be validated") from exc
    if not all(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) for value in (exe_hash, dll_hash)):
        raise ConfigError("sources lock contains an invalid runtime hash")
    active_python = Path(sys.executable if current_python is None else current_python).resolve(strict=True)
    if active_python != config.python_exe:
        raise ConfigError("adapter is not running under the machine-locked Python")
    try:
        hashes_match = (
            _sha256_file(config.wechat_cli_exe) == exe_hash
            and _sha256_file(config.wcdb_dll) == dll_hash
            and _sha256_file(active_python) == config.python_sha256
        )
    except OSError as exc:
        raise ConfigError("locked runtime could not be hashed") from exc
    if not hashes_match:
        raise ConfigError("locked runtime hash mismatch")


def ensure_windows_amd64() -> None:
    # platform.machine() is backed by PROCESSOR_ARCHITECTURE on Windows.
    # Codex intentionally launches MCP servers with a reduced environment, so
    # that value can be empty even for the pinned 64-bit x86 Python runtime.
    # sysconfig describes the running Python build and does not depend on that
    # inherited environment variable.
    python_platform = sysconfig.get_platform().lower()
    if os.name != "nt" or python_platform != "win-amd64":
        raise ConfigError("this adapter supports Windows amd64 only")


class SubprocessRunner:
    """Run only the fixed executable, with argv arrays and a forced read-only env."""

    def __init__(
        self,
        executable: Path,
        timeout_seconds: int,
        *,
        base_environment: Mapping[str, str] | None = None,
        fixed_environment: Mapping[str, str] | None = None,
    ) -> None:
        if not executable.is_absolute() or executable.suffix.lower() != ".exe":
            raise ConfigError("runner executable must be an absolute .exe path")
        self._executable = str(executable)
        self._timeout_seconds = timeout_seconds
        # Do not inherit arbitrary WECHAT_CLI_* behavior switches from the
        # launching shell.  Re-add only the four validated private paths.
        inherited = dict(os.environ if base_environment is None else base_environment)
        environment = {
            key: value
            for key, value in inherited.items()
            if not key.upper().startswith(("WECHAT_CLI_", "WX_MCP_"))
        }
        fixed = dict(fixed_environment or {})
        if (
            set(fixed) - ALLOWED_UPSTREAM_ENV
            or any(not isinstance(value, str) for value in fixed.values())
            or any(not Path(value).is_absolute() for value in fixed.values())
        ):
            raise ConfigError("runner fixed environment contains unsupported fields")
        environment.update(fixed)
        environment["WECHAT_CLI_STRICT_READ_ONLY"] = "1"
        environment["WECHAT_CLI_DISABLE_AUTO_REFRESH"] = "1"
        self._environment = environment

    def run_json(self, arguments: Sequence[str]) -> Any:
        if not arguments or any(not isinstance(item, str) or "\x00" in item for item in arguments):
            raise UpstreamError("invalid fixed upstream argv")
        argv = [self._executable, *arguments]
        try:
            completed = subprocess.run(
                argv,
                shell=False,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=dict(self._environment),
                timeout=self._timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise UpstreamError("upstream command timed out") from exc
        except OSError as exc:
            raise UpstreamError("upstream executable could not be started") from exc

        if completed.returncode != 0:
            # Never echo stderr: it may contain a database path, account ID, or key.
            stderr_present = bool((completed.stderr or "").strip())
            raise UpstreamError(
                "upstream command failed "
                f"(exit_code={completed.returncode}, stderr_present={str(stderr_present).lower()})"
            )
        output = (completed.stdout or "").strip()
        if not output:
            raise UpstreamError("upstream command returned no JSON")
        if len(output) > MAX_UPSTREAM_OUTPUT_CHARS:
            raise UpstreamError("upstream JSON exceeded the adapter limit")
        try:
            return json.loads(output)
        except json.JSONDecodeError as exc:
            raise UpstreamError("upstream command returned invalid JSON") from exc


class WechatCliClient:
    """Fail-closed facade over tools, tool-schema, and call-json only."""

    def __init__(self, runner: SubprocessRunner, config: AdapterConfig | None = None) -> None:
        self._runner = runner
        self._config = config
        self._catalog_verified = False
        self._schema_cache: dict[str, dict[str, Any]] = {}
        self._upstream_schema_cache: dict[str, dict[str, Any]] = {}
        self._candidate_cache: list[Any] | None = None
        self._candidate_cache_expires_at = 0.0

    def verify_catalog(self) -> None:
        if self._catalog_verified:
            return
        payload = self._runner.run_json(["tools"])
        records = _extract_tool_records(payload)
        if not records:
            raise UpstreamError("upstream tool catalog could not be verified")
        for spec in TOOL_SPECS:
            if spec.name in LOCAL_TOOL_NAMES:
                continue
            catalog_names = {spec.upstream, spec.name, *spec.catalog_aliases}
            matching_name = next((name for name in catalog_names if name in records), None)
            if matching_name is None:
                raise UpstreamError("the pinned upstream is missing a required read-only tool")
            record = records[matching_name]
            if record.get("read_only") is not True:
                raise UpstreamError("the upstream catalog did not attest a required tool as read-only")
            if record.get("local_write_mode") == "required":
                raise UpstreamError("the upstream catalog marks a required tool as write-requiring")
            if record.get("strict_read_only_behavior") not in {"same", "allowed_without_writes"}:
                raise UpstreamError("the upstream strict-read-only behavior could not be verified")
        self._catalog_verified = True

    def schema_for(self, spec: ToolSpec) -> dict[str, Any]:
        if spec.name in LOCAL_TOOL_NAMES:
            return copy.deepcopy(LOCAL_RESOLVE_SCHEMA)
        self.verify_catalog()
        if spec.name in PUBLIC_SCHEMAS:
            # Still load and validate the pinned upstream schema for every
            # tool, but never expose its identifier-bearing argument surface.
            if spec.name not in self._schema_cache:
                self._upstream_schema_for(spec)
                self._schema_cache[spec.name] = copy.deepcopy(PUBLIC_SCHEMAS[spec.name])
        return copy.deepcopy(self._schema_cache[spec.name])

    def _upstream_schema_for(self, spec: ToolSpec) -> dict[str, Any]:
        cached = self._upstream_schema_cache.get(spec.name)
        if cached is not None:
            return copy.deepcopy(cached)
        self.verify_catalog()
        payload = self._runner.run_json(["tool-schema", spec.upstream])
        schema = _harden_schema(_extract_input_schema(payload))
        self._upstream_schema_cache[spec.name] = schema
        return copy.deepcopy(schema)

    def call(self, spec: ToolSpec, arguments: Mapping[str, Any]) -> Any:
        if spec.name in LOCAL_TOOL_NAMES:
            raise UpstreamError("local tool requires the adapter dispatch path")
        # Schema lookup verifies both the upstream catalog and the argument surface.
        schema = self.schema_for(spec)
        _validate_schema_value(dict(arguments), schema)
        upstream_arguments = dict(arguments)
        if spec.name == "status":
            upstream_arguments["mode"] = "status"
        _validate_schema_value(upstream_arguments, self._upstream_schema_for(spec))
        encoded = json.dumps(upstream_arguments, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        return self._runner.run_json(["call-json", spec.upstream, encoded])

    def coverage_metadata(self) -> Any:
        """Call the upstream read-only coverage diagnostic behind public status."""

        # v1.6.20 documents `coverage` as a strict-read-only diagnostic, but
        # the default slim assistant profile does not advertise diagnostics
        # in `tools`. Runtime hashes are locked before this client is built,
        # and verify_catalog still attests the public nine-tool read surface.
        self.verify_catalog()
        return self._runner.run_json(["call-json", "coverage", "{}"])

    def source_inventory_metadata(self) -> dict[str, Any]:
        if self._config is None:
            raise ConfigError("source inventory configuration is unavailable")
        return source_inventory_metadata(
            self._config,
            project_root=Path(__file__).resolve().parents[1],
        )

    def call_internal(self, spec: ToolSpec, arguments: Mapping[str, Any]) -> Any:
        """Call a direct tool with adapter-private resolved anchors only."""

        if spec.name in LOCAL_TOOL_NAMES:
            raise UpstreamError("local tool has no upstream call")
        schema = self._upstream_schema_for(spec)
        _validate_schema_value(dict(arguments), schema)
        encoded = json.dumps(dict(arguments), ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        return self._runner.run_json(["call-json", spec.upstream, encoded])

    def _candidates(self) -> list[Any]:
        now = time.monotonic()
        if self._candidate_cache is None or now >= self._candidate_cache_expires_at:
            sessions = {
                "data": {
                    "sessions": self._all_resolver_source_rows(
                        TOOL_BY_NAME["sessions"],
                        ("sessions", "rows"),
                    )
                }
            }
            contacts = {
                "data": {
                    "contacts": self._all_resolver_source_rows(
                        TOOL_BY_NAME["contacts"],
                        ("contacts", "rows"),
                    )
                }
            }
            self._candidate_cache = candidates_from_sources(sessions, contacts)
            self._candidate_cache_expires_at = now + RESOLVER_CACHE_SECONDS
        return list(self._candidate_cache)

    def _all_resolver_source_rows(
        self,
        spec: ToolSpec,
        collection_names: tuple[str, ...],
    ) -> list[dict[str, Any]]:
        """Read a complete direct source in bounded, in-memory pages."""

        collected: list[dict[str, Any]] = []
        offset = 0
        while True:
            payload = self.call(spec, {"limit": RESOLVER_PAGE_SIZE, "offset": offset})
            page = _source_rows(payload, collection_names)
            has_more = _source_has_more(payload)
            if len(collected) + len(page) > MAX_RESOLVER_SOURCE_ROWS:
                raise UpstreamError("direct resolver source exceeded the bounded row limit")
            collected.extend(page)
            if not has_more:
                return collected
            if not page:
                raise UpstreamError("direct resolver pagination made no progress")
            offset += len(page)
            if offset > MAX_RESOLVER_SOURCE_ROWS:
                raise UpstreamError("direct resolver source exceeded the bounded row limit")

    def resolve_chat(self, query: str) -> Resolution:
        return resolve(self._candidates(), query)

    def call_scoped(
        self,
        spec: ToolSpec,
        arguments: Mapping[str, Any],
    ) -> tuple[Any, Resolution | None]:
        """Resolve an optional public chat label in-process before a direct read."""

        public_arguments = dict(arguments)
        _validate_schema_value(public_arguments, self.schema_for(spec))
        query = public_arguments.get("chat")
        direct_scope_key = "chatroom_id" if spec.name == "group_members" else "talker"
        direct_scope = public_arguments.get(direct_scope_key)
        if query is not None and direct_scope is not None:
            raise InvalidParams("chat label and direct chat identifier are mutually exclusive")

        resolution: Resolution | None = None
        if query is not None:
            if not isinstance(query, str):
                raise InvalidParams("chat must be a string")
            resolution = self.resolve_chat(query)
            if not resolution.ok or resolution.ambiguous or resolution.internal_chat is None:
                return {"ok": False, "error": {"code": "chat_not_uniquely_resolved"}}, resolution
            public_arguments.pop("chat", None)
            public_arguments[direct_scope_key] = resolution.internal_chat
        elif spec.name in REQUIRED_SCOPE_TOOLS and direct_scope is None:
            raise InvalidParams("a chat label or direct chat identifier is required")

        if spec.name == "message_context" and not any(
            key in public_arguments for key in MESSAGE_ANCHOR_KEYS
        ):
            raise InvalidParams("message_context requires a local or server message anchor")

        return self.call_internal(spec, public_arguments), resolution


def _source_rows(payload: Any, names: tuple[str, ...]) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    containers: list[Any] = [payload]
    if isinstance(payload.get("data"), dict):
        containers.append(payload["data"])
    for container in containers:
        if not isinstance(container, dict):
            continue
        for name in names:
            value = container.get(name)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def _source_has_more(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    containers: list[Any] = [payload]
    if isinstance(payload.get("data"), dict):
        containers.append(payload["data"])
    for container in containers:
        if not isinstance(container, dict):
            continue
        query = container.get("query")
        if isinstance(query, dict) and isinstance(query.get("has_more"), bool):
            return query["has_more"]
        if isinstance(container.get("has_more"), bool):
            return container["has_more"]
    return False


def _extract_tool_records(payload: Any) -> dict[str, dict[str, Any]]:
    containers: list[Any] = []
    if isinstance(payload, dict):
        for key in ("tools", "commands"):
            if key in payload:
                containers.append(payload[key])
        data = payload.get("data")
        if isinstance(data, dict):
            for key in ("tools", "commands"):
                if key in data:
                    containers.append(data[key])

    records: dict[str, dict[str, Any]] = {}
    for container in containers:
        if isinstance(container, dict):
            items: Iterable[Any] = (
                {"name": key, **value} if isinstance(value, dict) else {"name": key}
                for key, value in container.items()
                if isinstance(key, str)
            )
        elif isinstance(container, list):
            items = container
        else:
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            for key in ("name", "tool", "command"):
                value = item.get(key)
                if isinstance(value, str):
                    records.setdefault(value, item)
    return records


def _extract_input_schema(payload: Any) -> dict[str, Any]:
    candidates: list[Any] = []

    def add_from(container: Any) -> None:
        if not isinstance(container, dict):
            return
        for key in ("inputSchema", "input_schema", "parameters"):
            candidates.append(container.get(key))
        schema = container.get("schema")
        if isinstance(schema, dict):
            candidates.extend((schema.get("input"), schema.get("inputSchema"), schema))

    add_from(payload)
    if isinstance(payload, dict):
        add_from(payload.get("tool"))
        data = payload.get("data")
        add_from(data)
        if isinstance(data, dict):
            # v1.6.20's exact envelope is data.tool.inputSchema.
            add_from(data.get("tool"))
    candidates.append(payload)

    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        if candidate.get("type") == "object" or isinstance(candidate.get("properties"), dict):
            return copy.deepcopy(candidate)
    raise UpstreamError("upstream input schema could not be verified")


def _first_internal_message_anchor(payload: Any) -> dict[str, Any] | None:
    """Extract a private context anchor from one direct timeline result."""

    containers: list[Any] = [payload]
    if isinstance(payload, dict) and isinstance(payload.get("data"), dict):
        containers.append(payload["data"])
    for container in containers:
        if not isinstance(container, dict):
            continue
        messages = container.get("messages")
        if not isinstance(messages, list):
            continue
        for item in messages:
            if not isinstance(item, dict):
                continue
            anchor: dict[str, Any] = {}
            for source, target in (("local_id", "local_id"), ("server_id", "server_id"), ("server_id_str", "server_id_str")):
                value = item.get(source)
                if isinstance(value, (int, str)) and not isinstance(value, bool) and str(value).strip():
                    anchor[target] = value
            nested = item.get("id")
            if isinstance(nested, dict):
                for source, target in (("local_id", "local_id"), ("server_id", "server_id"), ("server_id_str", "server_id_str")):
                    value = nested.get(source)
                    if target not in anchor and isinstance(value, (int, str)) and not isinstance(value, bool) and str(value).strip():
                        anchor[target] = value
            if anchor:
                return {"include_anchor": True, **anchor}
    return None


def _harden_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Copy a schema and force object nodes to reject undeclared fields."""

    node = copy.deepcopy(schema)

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            # v1.6.20's published Windows artifact contains mojibake in many
            # Chinese schema descriptions. They are non-semantic annotations;
            # omit them while preserving names, types, enums, bounds, and
            # required fields so Codex receives a clean deterministic schema.
            value.pop("description", None)
            is_object = value.get("type") == "object" or isinstance(value.get("properties"), dict)
            if is_object:
                properties = value.get("properties", {})
                if not isinstance(properties, dict):
                    raise UpstreamError("upstream input schema has invalid properties")
                value["type"] = "object"
                value["additionalProperties"] = False
                for child in properties.values():
                    visit(child)
            items = value.get("items")
            if items is not None:
                visit(items)
            for keyword in ("allOf", "anyOf", "oneOf"):
                branches = value.get(keyword)
                if isinstance(branches, list):
                    for branch in branches:
                        visit(branch)
            definitions = value.get("$defs")
            if isinstance(definitions, dict):
                for child in definitions.values():
                    visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(node)
    if node.get("type") != "object":
        raise UpstreamError("upstream input schema must describe an object")
    return node


def _validate_schema_value(value: Any, schema: Mapping[str, Any]) -> None:
    """Validate the security-relevant, dependency-free subset of JSON Schema."""

    if "const" in schema and value != schema["const"]:
        raise InvalidParams("arguments do not match the tool schema")
    enum = schema.get("enum")
    if isinstance(enum, list) and value not in enum:
        raise InvalidParams("arguments do not match the tool schema")

    for keyword in ("allOf",):
        branches = schema.get(keyword)
        if isinstance(branches, list):
            for branch in branches:
                if isinstance(branch, dict):
                    _validate_schema_value(value, branch)

    any_of = schema.get("anyOf")
    if isinstance(any_of, list) and not _matches_any(value, any_of, require_one=False):
        raise InvalidParams("arguments do not match the tool schema")
    one_of = schema.get("oneOf")
    if isinstance(one_of, list) and not _matches_any(value, one_of, require_one=True):
        raise InvalidParams("arguments do not match the tool schema")

    expected = schema.get("type")
    if isinstance(expected, list):
        if not any(_is_json_type(value, item) for item in expected if isinstance(item, str)):
            raise InvalidParams("arguments do not match the tool schema")
    elif isinstance(expected, str) and not _is_json_type(value, expected):
        raise InvalidParams("arguments do not match the tool schema")

    if isinstance(value, dict):
        properties = schema.get("properties", {})
        if not isinstance(properties, dict):
            raise InvalidParams("arguments do not match the tool schema")
        unknown = set(value) - set(properties)
        if unknown:
            raise InvalidParams("arguments contain fields not declared by the tool schema")
        required = schema.get("required", [])
        if isinstance(required, list) and any(item not in value for item in required):
            raise InvalidParams("arguments are missing a required tool field")
        for key, child in value.items():
            child_schema = properties.get(key)
            if isinstance(child_schema, dict):
                _validate_schema_value(child, child_schema)
    elif isinstance(value, list):
        items = schema.get("items")
        if isinstance(items, dict):
            for child in value:
                _validate_schema_value(child, items)


def _matches_any(value: Any, branches: Sequence[Any], *, require_one: bool) -> bool:
    matches = 0
    for branch in branches:
        if not isinstance(branch, dict):
            continue
        try:
            _validate_schema_value(value, branch)
        except InvalidParams:
            continue
        matches += 1
    return matches == 1 if require_one else matches >= 1


def _is_json_type(value: Any, expected: str) -> bool:
    checks = {
        "object": lambda item: isinstance(item, dict),
        "array": lambda item: isinstance(item, list),
        "string": lambda item: isinstance(item, str),
        "integer": lambda item: isinstance(item, int) and not isinstance(item, bool),
        "number": lambda item: isinstance(item, (int, float)) and not isinstance(item, bool),
        "boolean": lambda item: isinstance(item, bool),
        "null": lambda item: item is None,
    }
    check = checks.get(expected)
    return bool(check and check(value))


_SAFE_FIELD_COMPONENT = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")
_SENSITIVE_FIELD_NAME = re.compile(
    r"(?i)(?:body|text|content|message|username|talker|wxid|sender|"
    r"(?:^|[_-])id$|(?:^|[_-])id(?:[_-])|path|key|token|cursor|"
    r"@chatroom|^[0-9a-f]{16,}$|^[A-Za-z0-9_-]{40,}$)"
)
_TIMESTAMP_PREFIX = re.compile(r"^(\d{4}-\d{2}-\d{2})(?:[T ][0-9:.+-Z]+)?$")
_SAFE_STATES = {
    "ok",
    "ready",
    "degraded",
    "blocked",
    "available",
    "missing",
    "supported",
    "unsupported",
    "windows",
    "amd64",
    "x86_64",
    "true",
    "false",
    "strict_read_only",
}

_PRIVATE_OUTPUT_FIELD = re.compile(
    r"(?i)(?:^id$|(?:^|[_-])id$|(?:^|[_-])id(?:[_-])|"
    r"^talker$|^wxid$|^username$|^sender_wxid$|^cursor$|"
    r"(?:^|[_-])path$|(?:^|[_-])path(?:[_-])|"
    r"(?:^|[_-])key$|(?:^|[_-])key(?:[_-])|^token$|"
    r"^raw_xml$|^xml$|^anchor_id$)"
)
_PATH_VALUE = re.compile(r"(?i)^(?:[a-z]:[\\/]|/|\\\\)")
_INTERNAL_OUTPUT_VALUE = re.compile(
    r"(?i)(?:wxid_[a-z0-9_.-]+|gh_[a-z0-9_.-]+|"
    r"[0-9]{7,}(?:@chatroom)?|.+@chatroom)"
)
_COVERAGE_CONTRACT_FIELDS = frozenset(
    {
        "name",
        "goal",
        "capability",
        "feature",
        "gate",
        "command",
        "contract",
        "data_policy",
        "status",
        "state",
        "result",
        "severity",
        "category",
        "reason",
        "local_file_write",
        "ok",
        "supported",
        "complete",
    }
)
CONTENT_TOOLS = frozenset(
    {"chat_timeline", "message_context", "search", "search_with_context", "read_events"}
)


def status_metadata_only(payload: Any) -> dict[str, Any]:
    """Reduce status output to field names, counts, booleans, dates, and states."""

    field_names: set[str] = set()
    collection_counts: dict[str, int] = {}
    numeric_counts: dict[str, int | float] = {}
    boolean_values: dict[str, bool] = {}
    timestamp_days: dict[str, str] = {}
    empty_fields: list[str] = []
    states: dict[str, str] = {}
    diagnostic_codes: dict[str, str] = {}
    item_budget = 512

    def walk(value: Any, parts: tuple[str, ...]) -> None:
        nonlocal item_budget
        if item_budget <= 0:
            return
        path = ".".join(parts) if parts else "$"
        if isinstance(value, dict):
            for key, child in value.items():
                if (
                    item_budget <= 0
                    or not isinstance(key, str)
                    or not _SAFE_FIELD_COMPONENT.fullmatch(key)
                    or _SENSITIVE_FIELD_NAME.search(key)
                ):
                    continue
                item_budget -= 1
                field_names.add(key)
                walk(child, (*parts, key))
        elif isinstance(value, list):
            collection_counts[path] = len(value)
            for index, child in enumerate(value[:128]):
                if item_budget <= 0:
                    break
                walk(child, (*parts, f"item_{index}"))
        elif isinstance(value, bool):
            boolean_values[path] = value
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            leaf = parts[-1].lower() if parts else ""
            if leaf.endswith(("count", "total", "size", "version", "build")) or leaf in {
                "source_dbs",
                "skipped_live_source",
                "covered_dbs",
                "missing_dbs",
                "failed_dbs",
                "usable_dbs",
                "configured_dbs",
            }:
                numeric_counts[path] = value
        elif value is None or value == "":
            empty_fields.append(path)
        elif isinstance(value, str):
            match = _TIMESTAMP_PREFIX.fullmatch(value)
            if match:
                timestamp_days[path] = match.group(1)
            elif value.lower() in _SAFE_STATES:
                states[path] = value.lower()
            elif (
                parts
                and parts[-1].lower() in {
                    "code",
                    "status",
                    "state",
                    "result",
                    "severity",
                    "category",
                    "reason",
                    "name",
                    "goal",
                    "capability",
                    "feature",
                    "gate",
                    "command",
                    "contract",
                }
                and re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,127}", value)
            ):
                diagnostic_codes[path] = value

    walk(payload, ())
    upstream_ok = payload.get("ok") if isinstance(payload, dict) and isinstance(payload.get("ok"), bool) else None
    return {
        "ok": upstream_ok,
        "tool": "status",
        "backend_used": BACKEND_NAME,
        "metadata_only": True,
        "metadata": {
            "field_names": sorted(field_names),
            "collection_counts": dict(sorted(collection_counts.items())),
            "numeric_counts": dict(sorted(numeric_counts.items())),
            "boolean_values": dict(sorted(boolean_values.items())),
            "timestamp_days": dict(sorted(timestamp_days.items())),
            "empty_fields": sorted(empty_fields),
            "states": dict(sorted(states.items())),
            "diagnostic_codes": dict(sorted(diagnostic_codes.items())),
            "truncated": item_budget <= 0,
        },
    }


def _boolean_by_key(payload: Any, key: str) -> bool | None:
    """Find one named boolean without serializing its surrounding payload."""

    budget = 2048

    def walk(value: Any) -> bool | None:
        nonlocal budget
        if budget <= 0:
            return None
        budget -= 1
        if isinstance(value, dict):
            direct = value.get(key)
            if isinstance(direct, bool):
                return direct
            for child in value.values():
                found = walk(child)
                if found is not None:
                    return found
        elif isinstance(value, list):
            for child in value[:256]:
                found = walk(child)
                if found is not None:
                    return found
        return None

    return walk(payload)


def coverage_contract_summary(payload: Any) -> dict[str, list[dict[str, Any]]]:
    """Expose static coverage labels and gates without operational details."""

    data = payload.get("data") if isinstance(payload, dict) and isinstance(payload.get("data"), dict) else {}
    result: dict[str, list[dict[str, Any]]] = {}
    for section in ("coverage", "quality_gates", "workflows"):
        source = data.get(section)
        if not isinstance(source, list):
            continue
        visible_items: list[dict[str, Any]] = []
        for index, item in enumerate(source[:128]):
            if not isinstance(item, dict):
                continue
            visible: dict[str, Any] = {"index": index}
            for key, value in item.items():
                if key not in _COVERAGE_CONTRACT_FIELDS:
                    continue
                if isinstance(value, bool) or (
                    isinstance(value, (int, float)) and not isinstance(value, bool)
                ):
                    visible[key] = value
                    continue
                if not isinstance(value, str):
                    continue
                cleaned = " ".join(value.split())
                if (
                    not cleaned
                    or len(cleaned) > 160
                    or _PATH_VALUE.match(cleaned)
                    or _INTERNAL_OUTPUT_VALUE.fullmatch(cleaned)
                    or re.fullmatch(r"(?i)[0-9a-f]{32,}", cleaned)
                ):
                    continue
                visible[key] = cleaned
            visible_items.append(visible)
        result[section] = visible_items
    return result


def _metadata_only(tool_name: str, payload: Any) -> dict[str, Any]:
    """Return counts, booleans, field names, states, and redacted dates only."""

    summary = status_metadata_only(payload)
    summary["tool"] = tool_name
    summary["backend_used"] = BACKEND_NAME
    summary["metadata_only"] = True
    if isinstance(payload, dict):
        data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
        if isinstance(data, dict):
            for key in ("messages", "events", "matches", "rows", "sessions", "contacts"):
                value = data.get(key)
                if isinstance(value, list):
                    summary["result_count"] = len(value)
                    break
            for key in ("match_count", "count", "count_returned", "returned", "total"):
                value = data.get(key)
                if isinstance(value, int) and not isinstance(value, bool):
                    summary["reported_count"] = value
                    break
    return summary


def _content_safe(tool_name: str, payload: Any) -> dict[str, Any]:
    """Keep user-visible message semantics while dropping implementation data."""

    def project(value: Any, key: str | None = None) -> Any:
        if isinstance(value, dict):
            visible: dict[str, Any] = {}
            for child_key, child in value.items():
                if not isinstance(child_key, str) or _PRIVATE_OUTPUT_FIELD.search(child_key):
                    continue
                projected = project(child, child_key)
                if projected is not None:
                    visible[child_key] = projected
            return visible
        if isinstance(value, list):
            return [project(child) for child in value]
        if isinstance(value, str):
            stripped = value.strip()
            if _PATH_VALUE.match(stripped):
                return None
            if key in {"sender", "display_name", "chat"} and (
                _INTERNAL_OUTPUT_VALUE.fullmatch(stripped) is not None
            ):
                return None
            return value
        if value is None or isinstance(value, (bool, int, float)):
            return value
        return None

    projected = project(payload)
    if not isinstance(projected, dict):
        projected = {}
    projected["tool"] = tool_name
    projected["backend_used"] = BACKEND_NAME
    projected["content_projection"] = "user_visible"
    projected["internal_identifiers_emitted"] = False
    projected["private_paths_emitted"] = False
    return projected


def _redacted_failure(tool_name: str, payload: Any) -> dict[str, Any]:
    code = "upstream_tool_error"
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            candidate = error.get("code")
            if isinstance(candidate, str) and re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,63}", candidate):
                code = candidate
    return {
        "ok": False,
        "tool": tool_name,
        "backend_used": BACKEND_NAME,
        "error": {
            "code": code,
            "message": "upstream tool reported an error; sensitive details were redacted",
            "details_redacted": True,
        },
    }


class McpServer:
    def __init__(self, client: WechatCliClient) -> None:
        self._client = client

    def dispatch(self, method: str, params: Any) -> dict[str, Any]:
        if params is None:
            params = {}
        if not isinstance(params, dict):
            raise InvalidParams("params must be an object")

        if method == "initialize":
            return {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "wechat-local-platform", "version": "0.1.0-dev"},
                "instructions": (
                    "Direct strict-read-only WeChat with upstream-compatible successful payloads. Every upstream call is "
                    "forced through WECHAT_CLI_STRICT_READ_ONLY=1 and "
                    "WECHAT_CLI_DISABLE_AUTO_REFRESH=1. No snapshot fallback exists. "
                    "resolve_chat uses only direct sessions and contacts in adapter memory. "
                    "Status and errors are redacted; successful reads may include normal upstream identifiers and cursors. "
                    "Debug/media-path inputs are not exposed. Existing readable media paths may appear in normal "
                    "successful payloads; content is not persisted and no fallback exists."
                ),
            }
        if method == "ping":
            return {}
        if method == "tools/list":
            if set(params) - {"cursor", "_meta"}:
                raise InvalidParams("tools/list contains unsupported fields")
            if params.get("cursor") not in (None, ""):
                raise InvalidParams("tools/list pagination is not supported")
            tools = []
            for spec in TOOL_SPECS:
                tools.append(
                    {
                        "name": spec.name,
                        "description": spec.description,
                        "inputSchema": self._client.schema_for(spec),
                        "annotations": {
                            "readOnlyHint": True,
                            "destructiveHint": False,
                            "idempotentHint": True,
                            "openWorldHint": False,
                        },
                    }
                )
            return {"tools": tools}
        if method == "tools/call":
            if set(params) - {"name", "arguments", "_meta"}:
                raise InvalidParams("tools/call contains unsupported fields")
            name = params.get("name")
            arguments = params.get("arguments", {})
            if not isinstance(name, str) or name not in TOOL_BY_NAME:
                raise InvalidParams("tool is not on the fixed read-only whitelist")
            if not isinstance(arguments, dict):
                raise InvalidParams("tool arguments must be an object")
            spec = TOOL_BY_NAME[name]
            if name == "resolve_chat":
                query = arguments.get("query")
                if not isinstance(query, str):
                    raise InvalidParams("query is required")
                _validate_schema_value(dict(arguments), LOCAL_RESOLVE_SCHEMA)
                resolution = self._client.resolve_chat(query)
                visible = resolution.public()
                return {
                    "content": [{"type": "text", "text": json.dumps(visible, ensure_ascii=False, separators=(",", ":"), sort_keys=True)}],
                    "isError": not resolution.ok or resolution.ambiguous,
                }
            if name in SCOPED_TOOL_NAMES:
                payload, resolution = self._client.call_scoped(spec, arguments)
                if resolution is not None and (not resolution.ok or resolution.ambiguous):
                    visible = resolution.public()
                    return {
                        "content": [{"type": "text", "text": json.dumps(visible, ensure_ascii=False, separators=(",", ":"), sort_keys=True)}],
                        "isError": True,
                    }
            else:
                payload = self._client.call(spec, arguments)
            upstream_failed = isinstance(payload, dict) and payload.get("ok") is False
            if name == "status":
                try:
                    coverage = self._client.coverage_metadata()
                except UpstreamError:
                    coverage = {"ok": False, "coverage_diagnostic_available": False}
                visible = status_metadata_only({"status": payload, "coverage": coverage})
                visible["ok"] = isinstance(payload, dict) and payload.get("ok") is True
                visible["backend_used"] = BACKEND_NAME
                visible["live_read_ok"] = _boolean_by_key(payload, "live_read_ok") is True
                visible["metadata_only"] = True
                visible["strict_read_only"] = True
                visible["auto_refresh_disabled"] = True
                visible["fallback_enabled"] = False
                visible["coverage_diagnostic_available"] = not (
                    isinstance(coverage, dict) and coverage.get("coverage_diagnostic_available") is False
                )
                if visible["coverage_diagnostic_available"]:
                    visible["coverage_contract"] = coverage_contract_summary(coverage)
                try:
                    visible["source_inventory"] = self._client.source_inventory_metadata()
                    visible["source_inventory_available"] = True
                except (ConfigError, OSError):
                    visible["source_inventory_available"] = False
            elif upstream_failed:
                visible = _redacted_failure(name, payload)
            else:
                visible = payload
            content = json.dumps(visible, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
            return {"content": [{"type": "text", "text": content}], "isError": upstream_failed}
        raise KeyError(method)

    def handle_request(self, request: Any) -> dict[str, Any] | None:
        if not isinstance(request, dict) or request.get("jsonrpc") != "2.0":
            return _jsonrpc_error(None, -32600, "Invalid Request")
        request_id_present = "id" in request
        request_id = request.get("id")
        method = request.get("method")
        if not isinstance(method, str):
            response = _jsonrpc_error(request_id, -32600, "Invalid Request")
        elif method == "notifications/initialized":
            response = None
        else:
            try:
                result = self.dispatch(method, request.get("params", {}))
                response = {"jsonrpc": "2.0", "id": request_id, "result": result}
            except InvalidParams as exc:
                response = _jsonrpc_error(request_id, -32602, str(exc))
            except UpstreamError as exc:
                response = _jsonrpc_error(request_id, -32000, str(exc))
            except KeyError:
                response = _jsonrpc_error(request_id, -32601, "Method not found")
            except Exception:
                # Never serialize exception details from paths, payloads, or subprocesses.
                response = _jsonrpc_error(request_id, -32603, "Internal error")
        return response if request_id_present else None


def _jsonrpc_error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def serve_jsonl(server: McpServer, input_stream: TextIO, output_stream: TextIO) -> int:
    for line in input_stream:
        if not line.strip():
            continue
        if len(line.encode("utf-8", errors="replace")) > MAX_JSON_LINE_BYTES:
            response = _jsonrpc_error(None, -32700, "Parse error")
        else:
            try:
                request = json.loads(line)
            except json.JSONDecodeError:
                response = _jsonrpc_error(None, -32700, "Parse error")
            else:
                response = server.handle_request(request)
        if response is not None:
            output_stream.write(json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n")
            output_stream.flush()
    return 0


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Strict read-only WeChat local platform MCP adapter")
    parser.add_argument(
        "--config",
        required=True,
        help="absolute path to the machine-private adapter JSON",
    )
    return parser


def configure_stdio_utf8() -> None:
    """MCP stdio is UTF-8 even when Windows' active code page is not."""
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="strict")


def main(argv: Sequence[str] | None = None) -> int:
    configure_stdio_utf8()
    args = build_argument_parser().parse_args(argv)
    try:
        ensure_windows_amd64()
        config = AdapterConfig.from_private_json(args.config)
        validate_locked_runtime(
            config,
            Path(__file__).resolve().parents[1] / "provenance" / "sources.lock.json",
        )
        runner = SubprocessRunner(
            config.wechat_cli_exe,
            config.timeout_seconds,
            fixed_environment=dict(config.upstream_environment),
        )
    except ConfigError as exc:
        # ConfigError messages are deliberately path-free and key-free.
        print(f"adapter configuration error: {exc}", file=sys.stderr)
        return 2
    server = McpServer(WechatCliClient(runner, config))
    return serve_jsonl(server, sys.stdin, sys.stdout)


if __name__ == "__main__":
    raise SystemExit(main())
