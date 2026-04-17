"""
Tool definitions and executor for the Canva Fitness Design Agent.

Five tools total:
  1. web_search_fitness_trends  — research current fitness design trends
  2. synthesize_design_brief    — turn research into a structured brief
  3. canva_create_design        — create a blank Canva canvas (REST API)
  4. generate_design_brief      — produce a detailed per-design creative brief
  5. generate_etsy_listing      — write a full Etsy listing for each design
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

import requests
from rich.console import Console

import config
import research
from canva_client import CanvaClient, CanvaAPIError, CanvaConnectionError


# ── Data models ───────────────────────────────────────────────────────────────

@dataclass
class DesignBrief:
    """Per-design creative brief telling the user exactly what to add in Canva."""
    background: str         # e.g. "Deep navy #1A1A2E to black gradient"
    headline: str           # e.g. "NO DAYS OFF"
    headline_font: str      # e.g. "Bebas Neue, 120px, white, centered"
    subtext: str            # e.g. "Train hard. Stay consistent."
    subtext_font: str       # e.g. "Montserrat, 32px, #FF4500"
    accent_elements: str    # e.g. "Thin orange divider line at 60% height"
    mood: str               # e.g. "Powerful, motivational, dark luxury"


@dataclass
class EtsyListing:
    title: str
    description: str
    tags: list[str]
    suggested_price_usd: float
    category: str


@dataclass
class DesignResult:
    title: str
    format: str
    width: int
    height: int
    design_id: str
    edit_url: str
    design_brief: DesignBrief | None = None
    etsy_listing: EtsyListing | None = None


# ── Tool schemas ──────────────────────────────────────────────────────────────

TOOLS: list[dict] = [
    {
        "name": "web_search_fitness_trends",
        "description": (
            "Search the web for current fitness influencer design trends, popular color "
            "palettes, typography, and what makes fitness designs sell on Etsy. "
            "Call at least TWICE with different queries before creating any designs."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Specific search query including year (2026) and aspect being researched.",
                },
                "focus": {
                    "type": "string",
                    "enum": ["color_trends", "typography", "etsy_strategy",
                             "instagram_specs", "competitor_analysis"],
                },
            },
            "required": ["query", "focus"],
        },
    },
    {
        "name": "synthesize_design_brief",
        "description": (
            "After at least 2 web searches, call this ONCE to synthesize all research "
            "into a DesignBrief: color palette, fonts, layout ideas, copy suggestions, "
            "and 13 Etsy tags. Use this brief for all subsequent design decisions."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "user_prompt":      {"type": "string"},
                "search_summaries": {"type": "array", "items": {"type": "string"}},
                "design_count":     {"type": "integer", "minimum": 1, "maximum": 10},
                "design_format":    {
                    "type": "string",
                    "enum": ["instagram_story", "instagram_post", "instagram_landscape", "mixed"],
                },
            },
            "required": ["user_prompt", "search_summaries", "design_count", "design_format"],
        },
    },
    {
        "name": "canva_create_design",
        "description": (
            "Creates a blank Canva canvas at the exact Instagram pixel dimensions. "
            "Returns a design_id and an edit_url to open the design in Canva. "
            "Story: width=1080 height=1920. Post: width=1080 height=1080. "
            "Landscape: width=1080 height=608."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "title":  {"type": "string", "description": "Unique, descriptive design title."},
                "width":  {"type": "integer", "minimum": 40, "maximum": 8000},
                "height": {"type": "integer", "minimum": 40, "maximum": 8000},
            },
            "required": ["title", "width", "height"],
        },
    },
    {
        "name": "generate_design_brief",
        "description": (
            "For each blank Canva design, generate a detailed step-by-step creative brief "
            "telling the user EXACTLY what to add in Canva: background color/gradient, "
            "headline text and font, subtext, accent elements. "
            "Base all choices on the synthesized DesignBrief from research. "
            "Each design should have a unique concept."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "design_title":   {"type": "string"},
                "design_format":  {
                    "type": "string",
                    "enum": ["instagram_story", "instagram_post", "instagram_landscape"],
                },
                "background":     {"type": "string", "description": "Background color or gradient with hex codes."},
                "headline":       {"type": "string", "description": "Main motivational text."},
                "headline_font":  {"type": "string", "description": "Font name, size, color, alignment."},
                "subtext":        {"type": "string", "description": "Supporting text line."},
                "subtext_font":   {"type": "string", "description": "Font name, size, color."},
                "accent_elements":{"type": "string", "description": "Shapes, lines, icons to add."},
                "mood":           {"type": "string", "description": "Overall mood and feel."},
            },
            "required": ["design_title", "design_format", "background",
                         "headline", "headline_font", "subtext", "accent_elements", "mood"],
        },
    },
    {
        "name": "generate_etsy_listing",
        "description": (
            "Generates a complete Etsy listing: SEO title (≤140 chars), "
            "keyword-rich description, all 13 tags, suggested price, category. "
            "Call once per design after creating it."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "design_title":    {"type": "string"},
                "design_format":   {
                    "type": "string",
                    "enum": ["instagram_story", "instagram_post", "instagram_landscape"],
                },
                "design_style":    {"type": "string"},
                "color_palette":   {"type": "array", "items": {"type": "string"}},
                "mood_keywords":   {"type": "array", "items": {"type": "string"}},
                "headline_used":   {"type": "string"},
                "canva_edit_url":  {"type": "string"},
            },
            "required": ["design_title", "design_format", "design_style"],
        },
    },
]


# ── Tool executor ─────────────────────────────────────────────────────────────

class ToolExecutor:
    def __init__(self, canva: CanvaClient, console: Console):
        self._canva   = canva
        self._console = console
        self._search_results: list[str] = []
        self._brief: research.DesignBrief | None = None
        self.design_results: list[DesignResult] = []

    def execute(self, tool_name: str, tool_input: dict) -> str:
        dispatch = {
            "web_search_fitness_trends": self._web_search,
            "synthesize_design_brief":   self._synthesize_brief,
            "canva_create_design":       self._create_design,
            "generate_design_brief":     self._generate_design_brief,
            "generate_etsy_listing":     self._generate_etsy_listing,
        }
        handler = dispatch.get(tool_name)
        if not handler:
            return json.dumps({"error": f"Unknown tool: {tool_name}"})
        try:
            return handler(tool_input)
        except CanvaAPIError as exc:
            self._console.print(f"[red]Canva error:[/red] {exc}")
            return json.dumps({"error": str(exc)})
        except CanvaConnectionError as exc:
            self._console.print(f"[red]Network error:[/red] {exc}")
            return json.dumps({"error": str(exc)})
        except Exception as exc:
            self._console.print(f"[red]Tool error in {tool_name}:[/red] {exc}")
            return json.dumps({"error": str(exc)})

    def _web_search(self, inp: dict) -> str:
        query = inp["query"]
        focus = inp.get("focus", "general")
        self._console.print(f"[cyan]Searching:[/cyan] {query}")
        results = _do_web_search(query)
        summary = f"[{focus.upper()}] {query}\n" + "\n".join(
            f"  • {r['title']}: {r['snippet']}" for r in results
        )
        self._search_results.append(summary)
        return json.dumps({"focus": focus, "results": results})

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
            f"[green]Brief ready:[/green] {brief.design_style} | "
            f"{brief.color_palette[:2]} | {brief.primary_font}"
        )
        return json.dumps(brief.to_dict())

    def _create_design(self, inp: dict) -> str:
        title  = inp["title"]
        width  = inp["width"]
        height = inp["height"]
        self._console.print(f"[cyan]Creating canvas:[/cyan] '{title}' ({width}×{height})")

        data      = self._canva.create_design(width=width, height=height, title=title)
        design    = data.get("design", {})
        design_id = design.get("id", "")
        edit_url  = design.get("urls", {}).get("edit_url", "")

        fmt = {(1080, 1920): "instagram_story",
               (1080, 1080): "instagram_post",
               (1080, 608):  "instagram_landscape"}.get((width, height), f"custom_{width}x{height}")

        result = DesignResult(
            title=title, format=fmt,
            width=width, height=height,
            design_id=design_id, edit_url=edit_url,
        )
        self.design_results.append(result)
        self._console.print(f"[green]Canvas created:[/green] {edit_url}")
        return json.dumps({"design_id": design_id, "edit_url": edit_url,
                           "title": title, "width": width, "height": height})

    def _generate_design_brief(self, inp: dict) -> str:
        title  = inp["design_title"]
        brief  = DesignBrief(
            background=inp.get("background", ""),
            headline=inp.get("headline", ""),
            headline_font=inp.get("headline_font", ""),
            subtext=inp.get("subtext", ""),
            subtext_font=inp.get("subtext_font", ""),
            accent_elements=inp.get("accent_elements", ""),
            mood=inp.get("mood", ""),
        )
        for dr in self.design_results:
            if dr.title == title:
                dr.design_brief = brief
                break
        self._console.print(f"[green]Brief:[/green] '{title}' → {brief.headline}")
        return json.dumps({
            "design_title": title,
            "background": brief.background,
            "headline": brief.headline,
            "headline_font": brief.headline_font,
            "subtext": brief.subtext,
            "accent_elements": brief.accent_elements,
            "mood": brief.mood,
        })

    def _generate_etsy_listing(self, inp: dict) -> str:
        title         = inp["design_title"]
        design_format = inp["design_format"]
        style         = inp.get("design_style", "bold minimalist")
        palette       = inp.get("color_palette", [])
        mood          = inp.get("mood_keywords", [])
        headline      = inp.get("headline_used", "")
        edit_url      = inp.get("canva_edit_url", "")

        brief    = self._brief
        etsy_tags = brief.etsy_tags if brief else research.TREND_DEFAULTS["etsy_fitness_tags"][:13]

        format_label = {
            "instagram_story":     "Instagram Story (1080×1920)",
            "instagram_post":      "Instagram Post (1080×1080)",
            "instagram_landscape": "Instagram Landscape (1080×608)",
        }.get(design_format, "Instagram Template")

        etsy_title = f"{title} | {style.title()} Canva {format_label} | Fitness Influencer"[:140]
        description = _build_description(title, format_label, style, palette, mood, headline, etsy_tags)

        listing = EtsyListing(
            title=etsy_title,
            description=description,
            tags=etsy_tags[:13],
            suggested_price_usd=4.99 if "story" in design_format else 3.99,
            category="Digital Downloads > Templates",
        )
        for dr in self.design_results:
            if dr.title == title:
                dr.etsy_listing = listing
                break

        self._console.print(f"[green]Etsy listing:[/green] \"{etsy_title[:55]}…\"")
        return json.dumps({"title": etsy_title, "tags": etsy_tags[:13],
                           "price": listing.suggested_price_usd,
                           "description_preview": description[:200]})


# ── Web search ────────────────────────────────────────────────────────────────

def _do_web_search(query: str) -> list[dict]:
    if not config.SEARCH_API_KEY:
        return [{"title": "Using built-in defaults",
                 "snippet": "No SEARCH_API_KEY set — using 2026 fitness trend defaults.",
                 "url": ""}]
    if config.SEARCH_PROVIDER == "brave":
        return _brave_search(query, config.SEARCH_API_KEY)
    return _serpapi_search(query, config.SEARCH_API_KEY)


def _brave_search(query: str, api_key: str) -> list[dict]:
    try:
        resp = requests.get(
            "https://api.search.brave.com/res/v1/web/search",
            headers={"Accept": "application/json", "X-Subscription-Token": api_key},
            params={"q": query, "count": 5}, timeout=10,
        )
        resp.raise_for_status()
        return [{"title": r.get("title",""), "snippet": r.get("description",""), "url": r.get("url","")}
                for r in resp.json().get("web",{}).get("results",[])[:5]]
    except Exception as exc:
        return [{"title": "Search error", "snippet": str(exc), "url": ""}]


def _serpapi_search(query: str, api_key: str) -> list[dict]:
    try:
        resp = requests.get("https://serpapi.com/search",
                            params={"q": query, "api_key": api_key, "num": 5}, timeout=10)
        resp.raise_for_status()
        return [{"title": r.get("title",""), "snippet": r.get("snippet",""), "url": r.get("link","")}
                for r in resp.json().get("organic_results",[])[:5]]
    except Exception as exc:
        return [{"title": "Search error", "snippet": str(exc), "url": ""}]


# ── Etsy description ──────────────────────────────────────────────────────────

def _build_description(title, format_label, style, palette, mood, headline, tags) -> str:
    palette_str = ", ".join(palette) if palette else "bold high-contrast colors"
    mood_str    = ", ".join(mood) if mood else "energetic and powerful"
    tag_line    = " | ".join(tags[:8])
    return f"""✨ {title} — Instant Canva Template

A professionally designed {format_label} Canva template in {style} style for fitness influencers.

✏️ Featured headline: "{headline}"

📐 WHAT YOU GET
• 1 fully editable Canva template
• Exact Instagram dimensions — no cropping needed
• Open via edit link, customize in Canva, download, post

🎨 DESIGN DETAILS
• Style: {style.title()}
• Colors: {palette_str}
• Mood: {mood_str}
• Works with FREE Canva accounts

💼 COMMERCIAL USE INCLUDED

🔍 Keywords: {tag_line}
""".strip()
