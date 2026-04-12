"""
Tool definitions and executor for the Canva Fitness Design Agent.

TOOLS: list of tool schemas passed to the Anthropic API.
ToolExecutor: dispatches tool calls to their Python implementations.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import requests
from rich.console import Console

import config
import research
from canva_client import CanvaClient, CanvaAPIError, CanvaConnectionError, CanvaExportTimeoutError

if TYPE_CHECKING:
    pass


# ── Tool schemas (Claude sees these) ──────────────────────────────────────────

TOOLS: list[dict] = [
    {
        "name": "web_search_fitness_trends",
        "description": (
            "Search the web for current fitness influencer design trends, popular color "
            "palettes, typography, and what makes fitness designs sell on Etsy. "
            "Call this tool at least TWICE with different queries and focus areas "
            "before calling synthesize_design_brief or any canva_ tools."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "A precise search query. Include the year (2026), niche "
                        "(fitness influencer), and the specific aspect you are researching."
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
            "all research into a structured DesignBrief (color palette, fonts, layout, "
            "Etsy tags). Must be called after at least 2 web_search_fitness_trends calls "
            "and before any canva_ tools."
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
                    "description": "All raw text results from previous web_search_fitness_trends calls.",
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
                    "description": "Target Instagram format for the designs.",
                },
            },
            "required": ["user_prompt", "search_summaries", "design_count", "design_format"],
        },
    },
    {
        "name": "canva_create_design",
        "description": (
            "Creates a new blank design in Canva with specified pixel dimensions. "
            "Returns a design_id and an edit_url the user can open in Canva to customize. "
            "Instagram Story: width=1080 height=1920. "
            "Instagram Post (square): width=1080 height=1080. "
            "Instagram Landscape: width=1080 height=608. "
            "Space calls at least 3 seconds apart to respect rate limits."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": (
                        "Descriptive human-readable design title. Make it unique and "
                        "evocative of the design intent, e.g. 'BEAST MODE — Neon Story'."
                    ),
                },
                "width": {
                    "type": "integer",
                    "description": "Width in pixels (40–8000).",
                    "minimum": 40,
                    "maximum": 8000,
                },
                "height": {
                    "type": "integer",
                    "description": "Height in pixels (40–8000).",
                    "minimum": 40,
                    "maximum": 8000,
                },
                "design_brief_summary": {
                    "type": "string",
                    "description": (
                        "2-3 sentence summary of the design intent for this specific canvas "
                        "(color mood, layout idea, motivational copy to use). "
                        "This is logged for the user's reference."
                    ),
                },
            },
            "required": ["title", "width", "height"],
        },
    },
    {
        "name": "canva_export_design",
        "description": (
            "Exports a Canva design to PNG and returns download URLs. "
            "The export is asynchronous; this tool polls until complete (up to 120 seconds). "
            "Use format='png' for Etsy-ready high-resolution files. "
            "Only call after canva_create_design has returned a design_id."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "design_id": {
                    "type": "string",
                    "description": "The design_id returned by canva_create_design.",
                },
                "format": {
                    "type": "string",
                    "enum": ["png", "pdf", "jpg"],
                    "description": "Export format. Use 'png' for Etsy digital downloads.",
                    "default": "png",
                },
            },
            "required": ["design_id"],
        },
    },
    {
        "name": "generate_etsy_listing",
        "description": (
            "Generates a complete Etsy listing for a fitness design: "
            "SEO title (≤140 chars), keyword-rich description with bullet points, "
            "all 13 tags, suggested USD price, and Etsy category path. "
            "Call once per design after it has been exported."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "design_title": {
                    "type": "string",
                    "description": "The design title (same as used in canva_create_design).",
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
                    "description": "Hex codes of the design's primary colors.",
                },
                "mood_keywords": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Mood/feeling words that describe this design.",
                },
                "copy_suggestion": {
                    "type": "string",
                    "description": "The motivational text/headline intended for this design.",
                },
                "download_url": {
                    "type": "string",
                    "description": "The Canva export download URL for this design (if available).",
                },
            },
            "required": ["design_title", "design_format", "design_style"],
        },
    },
]


# ── Etsy listing data model ────────────────────────────────────────────────────

@dataclass
class EtsyListing:
    title: str
    description: str
    tags: list[str]
    suggested_price_usd: float
    category: str


# ── Design result data model ───────────────────────────────────────────────────

@dataclass
class DesignResult:
    design_id: str
    title: str
    format: str
    width: int
    height: int
    edit_url: str
    design_brief_summary: str
    download_urls: list[str] = field(default_factory=list)
    etsy_listing: EtsyListing | None = None


# ── Tool executor ─────────────────────────────────────────────────────────────

class ToolExecutor:
    def __init__(self, canva: CanvaClient, console: Console):
        self._canva = canva
        self._console = console
        # State accumulated across calls within a single agent run
        self._search_results: list[str] = []
        self._brief: research.DesignBrief | None = None
        self.design_results: list[DesignResult] = []

    def execute(self, tool_name: str, tool_input: dict) -> str:
        """
        Route a tool call by name, execute it, and return the result as a JSON string.
        On error, returns {"error": "..."} so Claude can reason about the failure.
        """
        dispatch = {
            "web_search_fitness_trends": self._web_search,
            "synthesize_design_brief":   self._synthesize_brief,
            "canva_create_design":       self._create_design,
            "canva_export_design":       self._export_design,
            "generate_etsy_listing":     self._generate_etsy_listing,
        }
        handler = dispatch.get(tool_name)
        if not handler:
            return json.dumps({"error": f"Unknown tool: {tool_name}"})
        try:
            return handler(tool_input)
        except CanvaAPIError as exc:
            self._console.print(f"[red]Canva API error:[/red] {exc}")
            return json.dumps({"error": str(exc)})
        except CanvaConnectionError as exc:
            self._console.print(f"[red]Network error:[/red] {exc}")
            return json.dumps({"error": str(exc)})
        except CanvaExportTimeoutError as exc:
            self._console.print(f"[yellow]Export timeout:[/yellow] {exc}")
            return json.dumps({"error": str(exc)})
        except Exception as exc:
            self._console.print(f"[red]Unexpected error in {tool_name}:[/red] {exc}")
            return json.dumps({"error": f"Unexpected error: {exc}"})

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

    def _create_design(self, inp: dict) -> str:
        title  = inp["title"]
        width  = inp["width"]
        height = inp["height"]
        brief_summary = inp.get("design_brief_summary", "")
        format_name = _dimensions_to_format(width, height)

        self._console.print(f"[cyan]Creating design:[/cyan] '{title}' ({width}×{height})")

        data = self._canva.create_design(width=width, height=height, title=title)
        design = data.get("design", {})
        design_id = design.get("id", "")
        urls = design.get("urls", {})
        edit_url = urls.get("edit_url", "")

        result = DesignResult(
            design_id=design_id,
            title=title,
            format=format_name,
            width=width,
            height=height,
            edit_url=edit_url,
            design_brief_summary=brief_summary,
        )
        self.design_results.append(result)

        self._console.print(f"[green]Created:[/green] {design_id} → {edit_url}")
        return json.dumps({
            "design_id": design_id,
            "edit_url": edit_url,
            "title": title,
            "width": width,
            "height": height,
        })

    def _export_design(self, inp: dict) -> str:
        design_id    = inp["design_id"]
        format_      = inp.get("format", "png")
        self._console.print(f"[cyan]Exporting:[/cyan] {design_id} as {format_}")

        job_data  = self._canva.create_export_job(design_id, format=format_)
        job_id    = job_data.get("job", {}).get("id", "")
        if not job_id:
            return json.dumps({"error": "No export job ID returned", "raw": job_data})

        self._console.print(f"[dim]Polling export job {job_id}…[/dim]")
        download_urls = self._canva.poll_export_until_done(job_id)

        # Attach download URLs to matching DesignResult
        for dr in self.design_results:
            if dr.design_id == design_id:
                dr.download_urls = download_urls
                break

        self._console.print(f"[green]Export ready:[/green] {len(download_urls)} file(s)")
        return json.dumps({"design_id": design_id, "download_urls": download_urls})

    def _generate_etsy_listing(self, inp: dict) -> str:
        design_title   = inp["design_title"]
        design_format  = inp["design_format"]
        design_style   = inp.get("design_style", "bold minimalist")
        color_palette  = inp.get("color_palette", [])
        mood_keywords  = inp.get("mood_keywords", [])
        copy_suggestion = inp.get("copy_suggestion", "")

        brief = self._brief
        etsy_tags = brief.etsy_tags if brief else research.TREND_DEFAULTS["etsy_fitness_tags"][:13]

        format_label = {
            "instagram_story":     "Instagram Story (1080×1920)",
            "instagram_post":      "Instagram Post (1080×1080)",
            "instagram_landscape": "Instagram Landscape (1080×608)",
        }.get(design_format, "Instagram Template")

        title = _build_etsy_title(design_title, design_style, format_label)
        description = _build_etsy_description(
            design_title, format_label, design_style,
            color_palette, mood_keywords, copy_suggestion, etsy_tags,
        )

        listing = EtsyListing(
            title=title,
            description=description,
            tags=etsy_tags[:13],
            suggested_price_usd=_suggest_price(design_format),
            category="Digital Downloads > Printable Art",
        )

        # Attach to matching DesignResult
        for dr in self.design_results:
            if dr.title == design_title:
                dr.etsy_listing = listing
                break

        self._console.print(f"[green]Etsy listing:[/green] \"{title[:60]}…\"")
        return json.dumps({
            "title": listing.title,
            "description": listing.description,
            "tags": listing.tags,
            "suggested_price_usd": listing.suggested_price_usd,
            "category": listing.category,
        })


# ── Web search backend ─────────────────────────────────────────────────────────

def _do_web_search(query: str) -> list[dict]:
    """
    Execute a web search using the configured provider.
    Returns a list of result dicts with 'title', 'snippet', 'url' keys.
    Falls back to an empty list if no API key is configured.
    """
    provider = config.SEARCH_PROVIDER
    api_key  = config.SEARCH_API_KEY

    if not api_key:
        # No API key — return a note so Claude can still proceed with defaults
        return [{"title": "No search API key configured",
                 "snippet": "Using built-in 2026 fitness design trend defaults.",
                 "url": ""}]

    if provider == "brave":
        return _brave_search(query, api_key)
    elif provider == "serpapi":
        return _serpapi_search(query, api_key)
    else:
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

def _build_etsy_title(design_title: str, style: str, format_label: str) -> str:
    raw = f"{design_title} | {style.title()} {format_label} | Fitness Printable Digital Download"
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
    copy_line   = f'\n📣 Featured quote: "{copy_}"\n' if copy_ else ""
    tag_line    = " | ".join(tags[:8])

    return f"""✨ {title} — Instant Digital Download

Transform your fitness content with this professionally designed {format_label} template, crafted in a {style} style to help you stand out on Instagram.

{copy_line}
📐 WHAT YOU GET
• 1 high-resolution PNG file ({format_label})
• Ready to upload directly to Instagram
• Open in Canva via the included edit link to customize text, fonts, and colors
• Perfect for fitness coaches, gym influencers, and personal trainers

🎨 DESIGN DETAILS
• Style: {style.title()}
• Color palette: {palette_str}
• Mood: {mood_str}

📥 HOW IT WORKS
1. Purchase and download your PNG file
2. Use the Canva edit link (included in the file notes) to customize
3. Export from Canva and post directly to Instagram

💼 COMMERCIAL USE
This digital download is licensed for personal and small commercial use. Perfect for building your brand, client content packages, or reselling as part of a bundle.

🔍 Keywords: {tag_line}

⭐ Questions? Message me — I respond within 24 hours.

© {_current_year()} — Digital download, no physical product will be shipped.
""".strip()


def _suggest_price(format_: str) -> float:
    return {"instagram_story": 3.99, "instagram_post": 3.99, "instagram_landscape": 2.99}.get(format_, 3.99)


def _current_year() -> int:
    import datetime
    return datetime.datetime.now().year


def _format_search_results(query: str, focus: str, results: list[dict]) -> str:
    lines = [f"[{focus.upper()}] Search: {query}"]
    for r in results:
        lines.append(f"  • {r['title']}: {r['snippet']}")
    return "\n".join(lines)


def _dimensions_to_format(width: int, height: int) -> str:
    if width == 1080 and height == 1920:
        return "instagram_story"
    if width == 1080 and height == 1080:
        return "instagram_post"
    if width == 1080 and height == 608:
        return "instagram_landscape"
    return f"custom_{width}x{height}"
