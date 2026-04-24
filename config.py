"""
Configuration management for the Canva Fitness Design Agent.
Reads environment variables and exposes typed constants used across all modules.
"""

import os
from typing import NamedTuple

from dotenv import load_dotenv

load_dotenv()


class DesignSpec(NamedTuple):
    name: str
    width: int
    height: int


# ── Instagram design presets ──────────────────────────────────────────────────
DESIGN_SPECS: dict[str, DesignSpec] = {
    "instagram_story":     DesignSpec("Instagram Story",     1080, 1920),
    "instagram_post":      DesignSpec("Instagram Post",      1080, 1080),
    "instagram_landscape": DesignSpec("Instagram Landscape", 1080, 608),
}

# ── Canva OAuth scopes ────────────────────────────────────────────────────────
CANVA_SCOPES: list[str] = [
    "design:content:write",
    "design:meta:read",
    "asset:read",
    "asset:write",
]

# ── Canva API endpoints ───────────────────────────────────────────────────────
CANVA_API_BASE            = "https://api.canva.com/rest/v1"
CANVA_OAUTH_AUTHORIZE_URL = "https://www.canva.com/api/oauth/authorize"
CANVA_OAUTH_TOKEN_URL     = f"{CANVA_API_BASE}/oauth/token"
CANVA_MCP_URL             = "https://mcp.canva.com/mcp"

CANVA_MAX_RETRIES = 3

# ── Anthropic (Claude) ────────────────────────────────────────────────────────
ANTHROPIC_API_KEY: str = os.environ.get("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL        = "claude-3-5-sonnet-20241022"
AGENT_MAX_ITERATIONS   = 30

# ── Canva OAuth credentials ───────────────────────────────────────────────────
CANVA_CLIENT_ID:     str = os.environ.get("CANVA_CLIENT_ID", "")
CANVA_CLIENT_SECRET: str = os.environ.get("CANVA_CLIENT_SECRET", "")
CANVA_ACCESS_TOKEN:  str = os.environ.get("CANVA_ACCESS_TOKEN", "")
CANVA_REFRESH_TOKEN: str = os.environ.get("CANVA_REFRESH_TOKEN", "")
CANVA_REDIRECT_URI:  str = os.environ.get("CANVA_REDIRECT_URI", "http://127.0.0.1:8080/callback")

# ── Web search (optional) ─────────────────────────────────────────────────────
SEARCH_API_KEY:  str = os.environ.get("SEARCH_API_KEY", "")
SEARCH_PROVIDER: str = os.environ.get("SEARCH_PROVIDER", "brave")  # "brave" | "serpapi"

# ── Token cache ───────────────────────────────────────────────────────────────
TOKEN_CACHE_PATH: str = os.path.expanduser("~/.canva_agent_tokens.json")


def validate() -> list[str]:
    """Return a list of missing required config keys (empty list = all good)."""
    missing = []
    if not ANTHROPIC_API_KEY:
        missing.append("ANTHROPIC_API_KEY")
    if not CANVA_CLIENT_ID:
        missing.append("CANVA_CLIENT_ID")
    if not CANVA_CLIENT_SECRET:
        missing.append("CANVA_CLIENT_SECRET")
    return missing
