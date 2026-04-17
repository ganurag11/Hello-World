"""
Canva Fitness Design Agent — Gemini + Canva Connect API edition.

Workflow:
  1. Gemini researches 2026 fitness design trends via web search
  2. Synthesizes a design brief (colors, fonts, copy, Etsy tags)
  3. Creates blank Canva canvases at exact Instagram dimensions (REST API)
  4. Generates a detailed per-design brief: what to add in Canva
  5. Writes full Etsy listings for every design

Usage:
    python agent.py --oauth                    # First-time Canva authorization
    python agent.py --count 3 --format story   # Create 3 story templates
    python agent.py "Create gym motivation designs" --count 5
"""

from __future__ import annotations

import argparse
import json
import sys
import time

from google import genai
from google.genai import errors as genai_errors
from google.genai import types
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box

import config
from canva_client import client_from_cache, load_cached_tokens, run_oauth_flow
from tools import TOOLS, ToolExecutor, DesignResult

console = Console()


# ── System prompt ─────────────────────────────────────────────────────────────

BASE_SYSTEM_PROMPT = """\
You are an expert fitness design consultant creating Instagram Canva templates
for fitness influencers to sell on Etsy.

You create blank Canva canvases at the correct dimensions, then provide a
detailed creative brief for each one so the user knows EXACTLY what to add.

## MANDATORY WORKFLOW — follow this order exactly

### Phase 1: Research
1. Call web_search_fitness_trends at least TWICE:
   - First call: focus "color_trends" or "typography"
   - Second call: focus "etsy_strategy" or "competitor_analysis"
2. Call synthesize_design_brief ONCE with all search summaries.

### Phase 2: Create Canvases
3. For each design, call canva_create_design with:
   - A unique, evocative title
   - Exact dimensions: Story=1080x1920, Post=1080x1080, Landscape=1080x608

### Phase 3: Design Briefs
4. For each canvas, call generate_design_brief with SPECIFIC details:
   - background: exact hex color or gradient (e.g. "#1A1A2E to #000000 gradient")
   - headline: the exact motivational text (e.g. "NO DAYS OFF")
   - headline_font: font name, size, color, position (e.g. "Bebas Neue, 120px, white, centered")
   - subtext: supporting line (e.g. "Train hard. Stay consistent.")
   - accent_elements: shapes/lines to add (e.g. "thin orange #FF4500 line at 65% height")
   - mood: overall feel (e.g. "dark, powerful, motivational")
   Each design must have a DIFFERENT concept — vary colors, copy, and mood.

### Phase 4: Etsy Listings
5. For each design, call generate_etsy_listing.

### Phase 5: Final Summary
6. Print a clear summary table with for each design:
   - Title and Canva edit URL
   - The complete design brief (what to add step by step)
   - Etsy listing title and tags

## Quality rules
- Use SPECIFIC hex colors from the research brief — not "dark blue"
- Each headline must be a real motivational fitness phrase
- Vary every design: different background, headline, mood
"""


# ── Agent ─────────────────────────────────────────────────────────────────────

class FitnessDesignAgent:
    def __init__(self):
        missing = config.validate()
        if missing:
            console.print(f"[red]Missing config:[/red] {', '.join(missing)}\n"
                          "Fill in your .env file.")
            sys.exit(1)

        self._gemini  = genai.Client(api_key=config.GEMINI_API_KEY)
        self._canva   = client_from_cache()
        self._executor = ToolExecutor(self._canva, console)

        declarations = [_to_declaration(t) for t in TOOLS]
        self._gen_config = types.GenerateContentConfig(
            system_instruction=BASE_SYSTEM_PROMPT,
            tools=[types.Tool(function_declarations=declarations)],
        )

    def run(self, user_prompt: str) -> list[DesignResult]:
        console.print(Panel(
            f"[bold cyan]Fitness Design Agent[/bold cyan] (Gemini + Canva)\n{user_prompt}",
            expand=False,
        ))

        chat     = self._gemini.chats.create(model=config.GEMINI_MODEL,
                                             config=self._gen_config)
        response = _send_with_retry(chat, user_prompt)
        iteration = 0

        while iteration < config.AGENT_MAX_ITERATIONS:
            iteration += 1

            fn_calls = [
                part.function_call
                for part in response.candidates[0].content.parts
                if part.function_call and part.function_call.name
            ]

            if not fn_calls:
                for part in response.candidates[0].content.parts:
                    if part.text:
                        console.print("\n" + part.text)
                break

            console.print(f"\n[dim]--- Turn {iteration} ({len(fn_calls)} tool(s)) ---[/dim]")

            response_parts = []
            for fc in fn_calls:
                console.print(f"[bold yellow]→[/bold yellow] {fc.name}")
                result_str = self._executor.execute(fc.name, dict(fc.args))
                response_parts.append(types.Part(
                    function_response=types.FunctionResponse(
                        name=fc.name,
                        response={"result": _safe_json(result_str)},
                    )
                ))

            response = _send_with_retry(chat, response_parts)

        if iteration >= config.AGENT_MAX_ITERATIONS:
            console.print("[red]Max iterations reached.[/red]")

        return self._executor.design_results


# ── Output ────────────────────────────────────────────────────────────────────

def print_results(results: list[DesignResult]) -> None:
    if not results:
        console.print("\n[yellow]No designs recorded — check output above for details.[/yellow]")
        return

    console.rule("[bold green]Your Designs[/bold green]")

    for i, dr in enumerate(results, 1):
        table = Table(title=f"Design {i}: {dr.title}",
                      box=box.ROUNDED, show_header=False, padding=(0, 1))
        table.add_column("Field", style="cyan", no_wrap=True)
        table.add_column("Value", style="white")

        table.add_row("Format",   f"{dr.format} ({dr.width}×{dr.height})")
        table.add_row("Edit URL", dr.edit_url or "[dim]not available[/dim]")

        if dr.design_brief:
            b = dr.design_brief
            table.add_row("─── In Canva, add:", "─────────────────────────────")
            table.add_row("Background",  b.background)
            table.add_row("Headline",    f'"{b.headline}" — {b.headline_font}')
            table.add_row("Subtext",     f'"{b.subtext}" — {b.subtext_font}')
            table.add_row("Accents",     b.accent_elements)
            table.add_row("Mood",        b.mood)

        if dr.etsy_listing:
            el = dr.etsy_listing
            table.add_row("─── Etsy Listing:", "─────────────────────────────")
            title_disp = el.title[:75] + "…" if len(el.title) > 75 else el.title
            table.add_row("Title",  title_disp)
            table.add_row("Price",  f"${el.suggested_price_usd:.2f}")
            table.add_row("Tags",   ", ".join(el.tags[:7]) + "…")

        console.print(table)
        console.print()

    console.rule("[bold green]Next Steps[/bold green]")
    console.print(
        "For each design:\n"
        "1. Click the [cyan]Edit URL[/cyan] to open in Canva\n"
        "2. Follow the brief above to add background, text, and accents\n"
        "3. Download as PNG from Canva\n"
        "4. List on Etsy using the generated title, description, and tags\n"
        "5. Upload the PNG as both the listing photo and digital download file\n"
    )


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Canva Fitness Design Agent — create Instagram templates for Etsy"
    )
    parser.add_argument("prompt",   nargs="?", default=None)
    parser.add_argument("--format", choices=["story","post","landscape","mixed"], default="mixed")
    parser.add_argument("--count",  type=int, default=5)
    parser.add_argument("--oauth",  action="store_true",
                        help="Authorize with Canva (run once before first use)")
    args = parser.parse_args()

    if args.oauth:
        console.print("[bold cyan]Starting Canva OAuth…[/bold cyan]")
        run_oauth_flow()
        console.print(f"\n[green]Done![/green] Tokens saved to {config.TOKEN_CACHE_PATH}")
        return

    format_label = {
        "story":     "Instagram Stories (1080×1920)",
        "post":      "square Instagram Posts (1080×1080)",
        "landscape": "Instagram Landscape posts (1080×608)",
        "mixed":     "a mix of Instagram Stories and Posts",
    }[args.format]

    count  = max(1, min(10, args.count))
    prompt = args.prompt or (
        f"Research 2026 fitness influencer design trends, then create {count} "
        f"Canva templates for {format_label}. Make them bold, motivational, "
        f"and ready to sell as digital templates on Etsy."
    )

    agent   = FitnessDesignAgent()
    results = agent.run(prompt)
    print_results(results)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _to_declaration(tool: dict) -> types.FunctionDeclaration:
    schema = tool.get("input_schema", {})
    return types.FunctionDeclaration(
        name=tool["name"],
        description=tool["description"],
        parameters=_build_schema(schema) if schema else None,
    )


def _build_schema(schema: dict) -> types.Schema:
    type_map = {
        "object": types.Type.OBJECT, "string": types.Type.STRING,
        "integer": types.Type.INTEGER, "number": types.Type.NUMBER,
        "boolean": types.Type.BOOLEAN, "array": types.Type.ARRAY,
    }
    properties = {k: _build_schema(v) for k, v in schema.get("properties", {}).items()} or None
    items = _build_schema(schema["items"]) if "items" in schema else None
    return types.Schema(
        type=type_map.get(schema.get("type", "object"), types.Type.OBJECT),
        description=schema.get("description", ""),
        properties=properties,
        required=schema.get("required"),
        items=items,
        enum=schema.get("enum"),
    )


def _send_with_retry(chat, message, max_retries: int = 4) -> object:
    """Send a Gemini message, retrying on 429 rate-limit errors."""
    for attempt in range(max_retries):
        try:
            return chat.send_message(message)
        except genai_errors.ClientError as exc:
            if exc.status_code == 429 and attempt < max_retries - 1:
                wait = 30 * (attempt + 1)   # 30s, 60s, 90s
                console.print(f"[yellow]Rate limited — waiting {wait}s…[/yellow]")
                time.sleep(wait)
            else:
                raise
    raise RuntimeError("Max retries exceeded")


def _safe_json(s: str) -> dict | str:
    try:
        return json.loads(s)
    except Exception:
        return s


if __name__ == "__main__":
    main()
