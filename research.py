"""
Design research utilities.

Provides the DesignBrief dataclass and helpers to aggregate web search results
into a structured brief that drives Canva design creation and Etsy listing copy.
Falls back to hardcoded 2026 trend defaults when search results are sparse.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field


# ── Data model ────────────────────────────────────────────────────────────────

@dataclass
class DesignBrief:
    color_palette: list[str] = field(default_factory=list)      # hex codes
    primary_font: str = ""                                        # e.g. "Bebas Neue"
    secondary_font: str = ""                                      # e.g. "Montserrat"
    design_style: str = ""                                        # e.g. "bold minimalist"
    mood_keywords: list[str] = field(default_factory=list)
    layout_suggestions: list[str] = field(default_factory=list)
    background_treatment: str = ""                                # e.g. "dark gradient"
    copy_suggestions: list[str] = field(default_factory=list)    # motivational phrases
    etsy_tags: list[str] = field(default_factory=list)           # exactly 13
    trend_summary: str = ""                                       # 2-3 sentence narrative

    def to_dict(self) -> dict:
        return {
            "color_palette": self.color_palette,
            "primary_font": self.primary_font,
            "secondary_font": self.secondary_font,
            "design_style": self.design_style,
            "mood_keywords": self.mood_keywords,
            "layout_suggestions": self.layout_suggestions,
            "background_treatment": self.background_treatment,
            "copy_suggestions": self.copy_suggestions,
            "etsy_tags": self.etsy_tags,
            "trend_summary": self.trend_summary,
        }


# ── 2026 trend defaults ────────────────────────────────────────────────────────
# Used as fallback when search results are empty or insufficient.

TREND_DEFAULTS: dict = {
    "color_palettes": [
        ["#FF4500", "#1A1A2E", "#FFFFFF"],   # neon orange + deep navy + white
        ["#00FF88", "#0D0D0D", "#F0F0F0"],   # neo-mint + black + off-white
        ["#D4A853", "#2C2C2C", "#F5F0EB"],   # earthy gold + charcoal + cream
        ["#E63946", "#457B9D", "#F1FAEE"],   # bold red + slate blue + ivory
    ],
    "fonts": {
        "display": ["Bebas Neue", "Anton", "Oswald", "Impact", "Black Han Sans"],
        "body":    ["Montserrat", "Open Sans", "Lato", "Nunito"],
    },
    "design_styles": [
        "bold minimalist",
        "neon retro",
        "earthy athletic",
        "clean high-contrast",
        "dark luxury gym",
    ],
    "mood_keywords": [
        "energetic", "powerful", "clean", "motivational",
        "bold", "athletic", "determined", "fierce",
    ],
    "layout_suggestions": [
        "large text overlay centered on dark background",
        "split panel: image left, bold quote right",
        "full-bleed background with bottom-anchored text block",
        "minimal top logo + massive central headline",
        "diagonal stripe accent with high-contrast text",
    ],
    "background_treatments": [
        "dark gradient (charcoal to black)",
        "solid neon accent color",
        "textured concrete or gym floor",
        "blurred sports photography",
        "flat color with geometric overlay",
    ],
    "copy_suggestions": [
        "NO DAYS OFF",
        "BUILT NOT BORN",
        "EARN IT EVERY DAY",
        "SWEAT IS JUST FAT CRYING",
        "YOUR ONLY LIMIT IS YOU",
        "TRAIN INSANE OR REMAIN THE SAME",
        "WAKE UP. WORK OUT. SLAY.",
        "STRONGER EVERY DAY",
        "PUSH YOUR LIMITS",
        "PAIN IS TEMPORARY. PRIDE IS FOREVER.",
    ],
    "etsy_fitness_tags": [
        "fitness printable",
        "gym motivation poster",
        "instagram story template",
        "workout quote print",
        "fitness planner",
        "gym wall art",
        "motivational print",
        "digital download fitness",
        "instagram post template",
        "exercise tracker printable",
        "wellness printable",
        "personal trainer marketing",
        "healthy lifestyle print",
    ],
}


# ── Query generation ───────────────────────────────────────────────────────────

def build_search_queries(user_prompt: str) -> list[dict]:
    """
    Return a list of dicts with 'query' and 'focus' keys targeting different
    aspects of fitness design research.
    """
    return [
        {
            "query": "fitness influencer Instagram design trends color palette typography 2026",
            "focus": "color_trends",
        },
        {
            "query": "best selling fitness digital download Etsy SEO tags 2026",
            "focus": "etsy_strategy",
        },
        {
            "query": "gym motivation quote Instagram story design best practices",
            "focus": "instagram_specs",
        },
        {
            "query": "top fitness influencer Instagram post aesthetic fonts layout 2026",
            "focus": "typography",
        },
        {
            "query": "Etsy fitness printable competitor analysis pricing digital downloads",
            "focus": "competitor_analysis",
        },
    ]


# ── Synthesis ──────────────────────────────────────────────────────────────────

def synthesize_design_brief(
    search_summaries: list[str],
    user_prompt: str,
    design_count: int = 5,
    design_format: str = "mixed",
) -> DesignBrief:
    """
    Build a DesignBrief from aggregated web search result text.
    Falls back to TREND_DEFAULTS for any fields not inferable from the summaries.

    Args:
        search_summaries: Raw text strings from web search tool calls.
        user_prompt: The original user request (for context).
        design_count: Number of designs to plan for.
        design_format: "instagram_story" | "instagram_post" | "instagram_landscape" | "mixed"
    """
    combined_text = "\n".join(search_summaries).lower()
    defaults = TREND_DEFAULTS

    # ── Color palette ─────────────────────────────────────────────────────────
    # Look for hex codes mentioned in search results; fall back to defaults.
    hex_codes = re.findall(r"#[0-9a-fA-F]{6}", " ".join(search_summaries))
    if len(hex_codes) >= 3:
        color_palette = hex_codes[:3]
    else:
        color_palette = defaults["color_palettes"][0]

    # ── Fonts ─────────────────────────────────────────────────────────────────
    primary_font = _detect_font(combined_text, defaults["fonts"]["display"], "Bebas Neue")
    secondary_font = _detect_font(combined_text, defaults["fonts"]["body"], "Montserrat")

    # ── Design style ──────────────────────────────────────────────────────────
    design_style = _detect_keyword(
        combined_text, defaults["design_styles"], "bold minimalist"
    )

    # ── Mood keywords (pick up to 5 from text, pad from defaults) ─────────────
    mood_keywords = _extract_mood_keywords(combined_text, defaults["mood_keywords"])

    # ── Layout suggestions ────────────────────────────────────────────────────
    layout_suggestions = defaults["layout_suggestions"][:3]

    # ── Background treatment ──────────────────────────────────────────────────
    background_treatment = _detect_keyword(
        combined_text,
        defaults["background_treatments"],
        "dark gradient (charcoal to black)",
    )

    # ── Copy suggestions ──────────────────────────────────────────────────────
    # Pull motivational phrases from search text, fall back to curated list.
    copy_suggestions = _extract_copy_suggestions(combined_text, defaults["copy_suggestions"])

    # ── Etsy tags (always exactly 13) ─────────────────────────────────────────
    etsy_tags = defaults["etsy_fitness_tags"][:13]

    # ── Trend summary ─────────────────────────────────────────────────────────
    trend_summary = _build_trend_summary(
        design_style, color_palette, primary_font, design_count, design_format
    )

    return DesignBrief(
        color_palette=color_palette,
        primary_font=primary_font,
        secondary_font=secondary_font,
        design_style=design_style,
        mood_keywords=mood_keywords,
        layout_suggestions=layout_suggestions,
        background_treatment=background_treatment,
        copy_suggestions=copy_suggestions,
        etsy_tags=etsy_tags,
        trend_summary=trend_summary,
    )


# ── Internal helpers ───────────────────────────────────────────────────────────

def _detect_font(text: str, candidates: list[str], fallback: str) -> str:
    for font in candidates:
        if font.lower() in text:
            return font
    return fallback


def _detect_keyword(text: str, candidates: list[str], fallback: str) -> str:
    for kw in candidates:
        if kw.lower() in text:
            return kw
    return fallback


def _extract_mood_keywords(text: str, defaults: list[str]) -> list[str]:
    found = [kw for kw in defaults if kw in text]
    if len(found) >= 3:
        return found[:5]
    # Pad with defaults up to 5
    combined = found + [kw for kw in defaults if kw not in found]
    return combined[:5]


def _extract_copy_suggestions(text: str, defaults: list[str]) -> list[str]:
    # Return defaults — LLM search text rarely preserves exact motivational phrases
    return defaults[:6]


def _build_trend_summary(
    style: str,
    palette: list[str],
    font: str,
    count: int,
    format_: str,
) -> str:
    palette_str = ", ".join(palette[:3]) if palette else "bold saturated tones"
    return (
        f"2026 fitness design trends favor a {style} aesthetic with "
        f"high-contrast palettes ({palette_str}) and bold display fonts like {font}. "
        f"Designs should feel powerful and athletic, optimized for Instagram "
        f"{'Stories (1080×1920)' if format_ == 'instagram_story' else 'Posts (1080×1080)'} "
        f"and priced competitively as digital downloads on Etsy."
    )


# ── Formatting for Claude's system prompt ─────────────────────────────────────

def format_brief_for_system_prompt(brief: DesignBrief) -> str:
    """Render a DesignBrief as a text block to prepend to Claude's system prompt."""
    return f"""
## Current Design Brief (from trend research)

**Trend Summary:** {brief.trend_summary}

**Color Palette:** {", ".join(brief.color_palette)}
**Primary Font:** {brief.primary_font}
**Secondary Font:** {brief.secondary_font}
**Design Style:** {brief.design_style}
**Background:** {brief.background_treatment}
**Mood:** {", ".join(brief.mood_keywords)}

**Layout Ideas:**
{chr(10).join(f"  - {s}" for s in brief.layout_suggestions)}

**Motivational Copy to Try:**
{chr(10).join(f"  - {c}" for c in brief.copy_suggestions)}

**Etsy Tags (use all 13):** {", ".join(brief.etsy_tags)}
""".strip()
