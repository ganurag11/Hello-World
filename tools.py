"""
Tool definitions and executor for the Canva Fitness Design Agent.

TOOLS: our custom tool schemas passed to the Anthropic API alongside the
       Canva MCP server tools (which the API discovers automatically).
ToolExecutor: dispatches our custom tool calls only.

Canva design creation is handled by the Canva AI Connector MCP server
(https://mcp.canva.com/mcp), which creates actual designed content from
natural language prompts and returns shareable Canva template links.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field

import requests
from rich.console import Console

import config
import research


# ── Data models ───────────────────────────────────────────────────────────────

@dataclass
class EtsyListing:
    title: str                      # ≤140 chars, SEO-optimized
    description: str                # bullet-pointed, keyword-rich
    tags: list[str]                 # exactly 13
    suggested_price_usd: float
    category: str
    canva_template_url: str         # the shareable Canva link sold to customers


@dataclass
class DesignResult:
    title: str
    format: str                     # "instagram_story" | "instagram_post" | etc.
    canva_design_url: str           # Canva view/edit URL from MCP
    canva_template_url: str         # shareable "copy this template" link for Etsy
    design_brief_summary: str
    etsy_listing: EtsyListing | None = None


# ── Custom tool schemas (Claude sees these alongside Canva MCP tools) ─────────
#
# We define ONLY our research and Etsy tools here.
# Canva design creation tools come automatically from the Canva MCP server.

TOOLS: list[dict] = [
    {
        "name": "web_search_fitness_trends",
        "description": (
            "Search the web for current fitness influencer design trends, popular color "
            "palettes, typography, and what makes fitness designs sell on Etsy. "
            "Call this tool at least TWICE with different queries and focus areas "
            "before creating any designs. Returns raw search result text."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "A precise search query. Include the year (2026), niche "
                        "(fitness influencer), and the specific aspect being researched."
                    ),
                },
                "focus": {
                    "type": "string",
                    "enum": [
                        "color_trends",
                        "typography",
                        "etsy_strategy",
                        "instagram_specs",
                        "competitor_analysis",
                    ],
                    "description": "Categorizes this search for downstream synthesis.",
                },
            },
            "required": ["query", "focus"],
        },
    },
    {
        "name": "synthesize_design_brief",
        "description": (
            "After collecting web search results, call this tool ONCE to synthesize "
            "all research into a structured DesignBrief: color palette, fonts, layout "
            "ideas, motivational copy suggestions, and 13 Etsy tags. "
            "Must be called after at least 2 web_search_fitness_trends calls "
            "and before creating any designs."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "user_prompt": {
                    "type": "string",
                    "description": "The original user request verbatim.",
                },
                "search_summaries": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "All raw result text from previous web_search_fitness_trends calls.",
                },
                "design_count": {
                    "type": "integer",
                    "description": "Number of designs to create.",
                    "minimum": 1,
                    "maximum": 10,
                },
                "design_format": {
                    "type": "string",
                    "enum": [
                        "instagram_story",
                        "instagram_post",
                        "instagram_landscape",
                        "mixed",
                    ],
                    "description": "Target Instagram format.",
                },
            },
            "required": ["user_prompt", "search_summaries", "design_count", "design_format"],
        },
    },
    {
        "name": "generate_etsy_listing",
        "description": (
            "Generates a complete Etsy listing for a finished Canva design: "
            "SEO title (≤140 chars), keyword-rich bullet-point description, "
            "all 13 Etsy tags, suggested USD price, and Etsy category. "
            "The listing explains that customers receive a shareable Canva template link "
            "they can copy to their own Canva account and customize. "
            "Call once per design after Canva has created it and returned a design URL."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "design_title": {
                    "type": "string",
                    "description": "The design title (as given to the Canva tool).",
                },
                "design_format": {
                    "type": "string",
                    "enum": ["instagram_story", "instagram_post", "instagram_landscape"],
                },
                "design_style": {
                    "type": "string",
                    "description": "e.g. 'bold minimalist', 'neon retro', 'earthy athletic'.",
                },
                "color_palette": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Hex codes or color names used in the design.",
                },
                "mood_keywords": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Mood/feeling words that describe this design.",
                },
                "copy_used": {
                    "type": "string",
                    "description": "The motivational text/headline used in this design.",
                },
                "canva_design_url": {
                    "type": "string",
                    "description": (
                        "The Canva URL returned by the Canva tool for this design. "
                        "This becomes the template link shared with Etsy customers."
                    ),
                },
            },
            "required": ["design_title", "design_format", "design_style", "canva_design_url"],
        },
    },
]


# ── Tool executor ─────────────────────────────────────────────────────────────

class ToolExecutor:
    """
    Executes our custom tool calls (research + Etsy listing generation).
    Canva design tool calls are handled transparently by the Anthropic API
    via the Canva MCP server — we never see those in the dispatch loop.
    """

    def __init__(self, console: Console):
        self._console = console
        self._search_results: list[str] = []
        self._brief: research.DesignBrief | None = None
        self.design_results: list[DesignResult] = []

    def execute(self, tool_name: str, tool_input: dict) -> str:
        """
        Route a custom tool call by name and return the result as a JSON string.
        Returns {"error": "..."} on failure so Claude can reason about it.
        """
        dispatch = {
            "web_search_fitness_trends": self._web_search,
            "synthesize_design_brief":   self._synthesize_brief,
            "generate_etsy_listing":     self._generate_etsy_listing,
        }
        handler = dispatch.get(tool_name)
        if not handler:
            return json.dumps({"error": f"Unknown tool: {tool_name}"})
        try:
            return handler(tool_input)
        except Exception as exc:
            self._console.print(f"[red]Tool error in {tool_name}:[/red] {exc}")
            return json.dumps({"error": f"Tool error: {exc}"})

    # ── Tool implementations ───────────────────────────────────────────────────

    def _web_search(self, inp: dict) -> str:
        query = inp["query"]
        focus = inp.get("focus", "general")
        self._console.print(f"[cyan]Searching:[/cyan] {query}")

        results = _do_web_search(query)
        summary = _format_search_results(query, focus, results)
        self._search_results.append(summary)
        return json.dumps({"focus": focus, "query": query, "results": results})

    def _synthesize_brief(self, inp: dict) -> str:
        summaries = inp.get("search_summaries") or self._search_results
        brief = research.synthesize_design_brief(
            search_summaries=summaries,
            user_prompt=inp["user_prompt"],
            design_count=inp.get("design_count", 5),
            design_format=inp.get("design_format", "mixed"),
        )
        self._brief = brief
        self._console.print(
            f"[green]Design brief synthesized:[/green] {brief.design_style} | "
            f"palette {brief.color_palette[:2]} | font {brief.primary_font}"
        )
        return json.dumps(brief.to_dict())

    def _generate_etsy_listing(self, inp: dict) -> str:
        design_title    = inp["design_title"]
        design_format   = inp["design_format"]
        design_style    = inp.get("design_style", "bold minimalist")
        color_palette   = inp.get("color_palette", [])
        mood_keywords   = inp.get("mood_keywords", [])
        copy_used       = inp.get("copy_used", "")
        canva_url       = inp.get("canva_design_url", "")

        brief = self._brief
        etsy_tags = brief.etsy_tags if brief else research.TREND_DEFAULTS["etsy_fitness_tags"][:13]

        format_label = {
            "instagram_story":     "Instagram Story (1080×1920)",
            "instagram_post":      "Instagram Post (1080×1080)",
            "instagram_landscape": "Instagram Landscape (1080×608)",
        }.get(design_format, "Instagram Template")

        title       = _build_etsy_title(design_title, design_style, format_label)
        description = _build_etsy_description(
            design_title, format_label, design_style,
            color_palette, mood_keywords, copy_used, etsy_tags,
        )

        # Build shareable template URL (Canva's /copy suffix makes it copyable)
        template_url = _make_template_url(canva_url)

        listing = EtsyListing(
            title=title,
            description=description,
            tags=etsy_tags[:13],
            suggested_price_usd=_suggest_price(design_format),
            category="Digital Downloads > Templates",
            canva_template_url=template_url,
        )

        # Store result
        result = DesignResult(
            title=design_title,
            format=design_format,
            canva_design_url=canva_url,
            canva_template_url=template_url,
            design_brief_summary=inp.get("design_style", ""),
            etsy_listing=listing,
        )
        self.design_results.append(result)

        self._console.print(f"[green]Etsy listing:[/green] \"{title[:60]}…\"")
        return json.dumps({
            "title": listing.title,
            "description": listing.description,
            "tags": listing.tags,
            "suggested_price_usd": listing.suggested_price_usd,
            "category": listing.category,
            "canva_template_url": listing.canva_template_url,
        })


# ── Web search backend ─────────────────────────────────────────────────────────

def _do_web_search(query: str) -> list[dict]:
    provider = config.SEARCH_PROVIDER
    api_key  = config.SEARCH_API_KEY

    if not api_key:
        return [{"title": "No search API key configured",
                 "snippet": "Using built-in 2026 fitness design trend defaults.",
                 "url": ""}]
    if provider == "brave":
        return _brave_search(query, api_key)
    elif provider == "serpapi":
        return _serpapi_search(query, api_key)
    return [{"title": f"Unknown search provider: {provider}",
             "snippet": "Set SEARCH_PROVIDER=brave or serpapi in .env",
             "url": ""}]


def _brave_search(query: str, api_key: str) -> list[dict]:
    try:
        resp = requests.get(
            "https://api.search.brave.com/res/v1/web/search",
            headers={"Accept": "application/json", "X-Subscription-Token": api_key},
            params={"q": query, "count": 5},
            timeout=10,
        )
        resp.raise_for_status()
        results = resp.json().get("web", {}).get("results", [])
        return [
            {"title": r.get("title", ""), "snippet": r.get("description", ""), "url": r.get("url", "")}
            for r in results[:5]
        ]
    except Exception as exc:
        return [{"title": "Search error", "snippet": str(exc), "url": ""}]


def _serpapi_search(query: str, api_key: str) -> list[dict]:
    try:
        resp = requests.get(
            "https://serpapi.com/search",
            params={"q": query, "api_key": api_key, "num": 5},
            timeout=10,
        )
        resp.raise_for_status()
        results = resp.json().get("organic_results", [])
        return [
            {"title": r.get("title", ""), "snippet": r.get("snippet", ""), "url": r.get("link", "")}
            for r in results[:5]
        ]
    except Exception as exc:
        return [{"title": "Search error", "snippet": str(exc), "url": ""}]


# ── Etsy copy helpers ──────────────────────────────────────────────────────────

def _make_template_url(canva_url: str) -> str:
    """
    Convert a Canva design URL to a shareable template link.
    Canva template links use the /copy path, which lets anyone copy
    the design to their own Canva account when clicked.
    If the URL already has /copy or is empty, return as-is.
    """
    if not canva_url:
        return ""
    if "/copy" in canva_url:
        return canva_url
    # Strip trailing slashes and query strings, append /copy
    base = canva_url.split("?")[0].rstrip("/")
    return base + "/copy"


def _build_etsy_title(design_title: str, style: str, format_label: str) -> str:
    raw = f"{design_title} | {style.title()} Canva {format_label} Template | Fitness Influencer"
    return raw[:140]


def _build_etsy_description(
    title: str,
    format_label: str,
    style: str,
    palette: list[str],
    mood: list[str],
    copy_: str,
    tags: list[str],
) -> str:
    palette_str = ", ".join(palette) if palette else "bold, high-contrast colors"
    mood_str    = ", ".join(mood) if mood else "energetic and powerful"
    copy_line   = f'\n✏️ Featured quote: "{copy_}"\n' if copy_ else ""
    tag_line    = " | ".join(tags[:8])

    return f"""✨ {title} — Instant Canva Template

Elevate your fitness brand with this professionally designed {format_label} Canva template, crafted in a {style} style for maximum impact on Instagram.

{copy_line}
🎨 HOW IT WORKS (no design skills needed!)
1. Purchase this listing
2. You'll receive a link to your Canva template
3. Click the link → Canva opens → click "Use template"
4. The design is copied to YOUR Canva account
5. Edit the text, colors, and fonts to match your brand
6. Download and post to Instagram — done!

✅ WHAT'S INCLUDED
• 1 fully editable Canva template
• Format: {format_label}
• Style: {style.title()}
• Color palette: {palette_str}
• Mood: {mood_str}
• Compatible with FREE Canva accounts

📐 DESIGN DETAILS
• Correct Instagram dimensions — no cropping needed
• High-resolution output when downloaded from Canva
• All fonts used are available in Canva's free library
• Fully customizable: text, colors, images, fonts

💼 COMMERCIAL USE INCLUDED
Use this template for your own content, for clients, or include it in content packages. Reselling the template file itself is not permitted.

📩 DELIVERY
Your Canva template link is delivered instantly via Etsy's digital download system.

🔍 Keywords: {tag_line}

⭐ Questions? Message me — I respond within 24 hours.
""".strip()


def _suggest_price(format_: str) -> float:
    return {
        "instagram_story":     4.99,
        "instagram_post":      4.99,
        "instagram_landscape": 3.99,
    }.get(format_, 4.99)


def _format_search_results(query: str, focus: str, results: list[dict]) -> str:
    lines = [f"[{focus.upper()}] Search: {query}"]
    for r in results:
        lines.append(f"  • {r['title']}: {r['snippet']}")
    return "\n".join(lines)
