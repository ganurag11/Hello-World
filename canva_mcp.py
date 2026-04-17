"""
Canva AI Connector MCP client.

Connects to the Canva MCP server (https://mcp.canva.com/mcp) using the
Model Context Protocol over HTTP (JSON-RPC). Handles tool discovery,
tool calling, and converts MCP tool schemas to Gemini function declarations.
"""

from __future__ import annotations

import json
import time
from typing import Any

import requests


MCP_URL = "https://mcp.canva.com/mcp"
MCP_PROTOCOL_VERSION = "2024-11-05"


class CanvaMCPClient:
    """
    Synchronous HTTP client for the Canva AI Connector MCP server.

    Implements the JSON-RPC subset of the Model Context Protocol needed
    to list and call Canva's design tools.
    """

    def __init__(self, access_token: str):
        self._token   = access_token
        self._session = requests.Session()
        self._session.headers.update({
            "Authorization": f"Bearer {access_token}",
            "Content-Type":  "application/json",
            "Accept":        "application/json",
        })
        self._req_id  = 0
        self._initialized = False
        self._tools: list[dict] = []

    # ── JSON-RPC helpers ──────────────────────────────────────────────────────

    def _next_id(self) -> int:
        self._req_id += 1
        return self._req_id

    def _rpc(self, method: str, params: dict | None = None) -> Any:
        """Send a JSON-RPC request and return the 'result' field."""
        body = {
            "jsonrpc": "2.0",
            "method":  method,
            "params":  params or {},
            "id":      self._next_id(),
        }
        try:
            resp = self._session.post(MCP_URL, json=body, timeout=30)
            resp.raise_for_status()
        except requests.ConnectionError as exc:
            raise CanvaMCPConnectionError(f"Cannot reach Canva MCP server: {exc}") from exc
        except requests.HTTPError as exc:
            raise CanvaMCPError(f"HTTP {resp.status_code}: {resp.text}") from exc

        data = resp.json()
        if "error" in data:
            raise CanvaMCPError(f"MCP error {data['error'].get('code')}: {data['error'].get('message')}")
        return data.get("result")

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def initialize(self) -> None:
        """Perform the MCP initialize handshake."""
        self._rpc("initialize", {
            "protocolVersion": MCP_PROTOCOL_VERSION,
            "capabilities":    {},
            "clientInfo":      {"name": "fitness-design-agent", "version": "1.0"},
        })
        self._rpc("notifications/initialized")   # acknowledge
        self._initialized = True

    def _ensure_initialized(self) -> None:
        if not self._initialized:
            self.initialize()

    # ── Tool discovery ────────────────────────────────────────────────────────

    def list_tools(self) -> list[dict]:
        """
        Fetch the list of tools from the Canva MCP server.
        Caches the result for the lifetime of the client instance.
        """
        self._ensure_initialized()
        if self._tools:
            return self._tools

        result = self._rpc("tools/list")
        self._tools = result.get("tools", [])
        return self._tools

    def to_gemini_functions(self) -> list[dict]:
        """
        Convert Canva MCP tool schemas to Gemini function declaration dicts.
        Gemini accepts tools in the same JSON Schema format that MCP uses,
        so this is mostly a structural mapping.
        """
        tools = self.list_tools()
        gemini_functions = []
        for t in tools:
            fn: dict = {
                "name":        t["name"],
                "description": t.get("description", ""),
            }
            schema = t.get("inputSchema", {})
            if schema:
                # Gemini expects "parameters" key with JSON Schema
                fn["parameters"] = _clean_schema(schema)
            gemini_functions.append(fn)
        return gemini_functions

    # ── Tool execution ────────────────────────────────────────────────────────

    def call_tool(self, tool_name: str, arguments: dict) -> str:
        """
        Call a Canva MCP tool and return the result as a JSON string.
        Returns {"error": "..."} on failure so the LLM can reason about it.
        """
        self._ensure_initialized()
        try:
            result = self._rpc("tools/call", {
                "name":      tool_name,
                "arguments": arguments,
            })
            # MCP tool results are a list of content blocks
            content = result.get("content", [])
            text_parts = [
                block.get("text", "")
                for block in content
                if block.get("type") == "text"
            ]
            return json.dumps({"result": "\n".join(text_parts), "raw": content})
        except CanvaMCPError as exc:
            return json.dumps({"error": str(exc)})
        except Exception as exc:
            return json.dumps({"error": f"Unexpected error calling {tool_name}: {exc}"})


# ── Exceptions ────────────────────────────────────────────────────────────────

class CanvaMCPError(Exception):
    pass


class CanvaMCPConnectionError(CanvaMCPError):
    pass


# ── Schema helpers ────────────────────────────────────────────────────────────

def _clean_schema(schema: dict) -> dict:
    """
    Strip any JSON Schema keys Gemini doesn't accept and ensure
    'type' is always present at the top level.
    """
    cleaned = {k: v for k, v in schema.items() if k not in ("$schema", "$id", "definitions")}
    cleaned.setdefault("type", "object")

    # Recursively clean nested property schemas
    if "properties" in cleaned:
        cleaned["properties"] = {
            k: _clean_schema(v) for k, v in cleaned["properties"].items()
        }
    return cleaned
