"""Strict read-only stdio MCP adapter for the pinned Windows wechat-cli."""

from .server import AdapterConfig, McpServer, SubprocessRunner, WechatCliClient

__all__ = ["AdapterConfig", "McpServer", "SubprocessRunner", "WechatCliClient"]
