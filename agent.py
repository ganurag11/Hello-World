"""
Canva Fitness Design Agent — main entry point.

Usage:
    python agent.py --oauth                         # First-time Canva token setup
    python agent.py "Create 5 fitness stories"      # Run the agent
    python agent.py --count 3 --format post         # Explicit options
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

import anthropic
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box

import config
from canva_client import client_from_cache, run_oauth_flow
from tools import TOOLS, ToolExecutor, DesignResult
import research

console = Console()


# ── System prompt ─────────────────────────────────────────────────────────────

BASE_SYSTEM_PROMPT = """\
You are an expert fitness design consultant and Canva specialist.
Your mission: create stunning Instagram designs for fitness influencers to sell on Etsy.

## MANDATORY WORKFLOW — follow this order exactly, no exceptions

1. Call `web_search_fitness_trends` at least TWICE with DIFFERENT queries and focus areas.
   - First call: focus on color_trends or typography
   - Second call: focus on etsy_strategy or competitor_analysis
   - You may make additional searches for more detail.

2. Call `synthesize_design_brief` ONCE with ALL search result summaries combined.
   This produces the color palette, fonts, layout ideas, and Etsy tags you will use.

3. For EACH design requested:
   a. Call `canva_create_design` with the correct dimensions and a descriptive title.
      - Instagram Story: width=1080, height=1920
      - Instagram Post (square): width=1080, height=1080
      - Instagram Landscape: width=1080, height=608
   b. Call `canva_export_design` with the returned design_id to generate download URLs.
   c. Call `generate_etsy_listing` to create the Etsy title, description, and 13 tags.

4. After all designs are processed, provide a clear final summary with:
   - Each design's title, Canva edit URL, and download URL
   - Each design's Etsy listing title and tags
   - Next steps for the user (open edit_url → customize → download → list on Etsy)

## Design constraints
- Always use the exact pixel dimensions above — never approximate.
- Export format: always PNG for Etsy digital downloads.
- Make each design title unique and evocative (e.g. "BEAST MODE — Neon Story #1").
- Include a `design_brief_summary` in each canva_create_design call describing
  the intended color mood, layout, and motivational copy for that specific design.

## Error handling
- If a tool returns {"error": "..."}, describe the issue and retry with adjusted parameters.
- If export times out, call canva_export_design again for that design_id.
- Never skip a step — if a design fails, note it in the final summary.

## Important note about Canva
The Canva Connect API creates BLANK designs at the correct dimensions.
The user will open each design's edit_url to add text, colors, and images in Canva's UI.
Your role is: research → correct sizing → batch creation → Etsy copywriting.
"""


# ── Agent class ───────────────────────────────────────────────────────────────

class FitnessDesignAgent:
    def __init__(self):
        missing = config.validate()
        if missing:
            console.print(
                f"[red]Missing required config:[/red] {', '.join(missing)}\n"
                "Copy .env.example to .env and fill in the values."
            )
            sys.exit(1)

        self._client  = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
        self._canva   = client_from_cache()
        self._executor = ToolExecutor(self._canva, console)
        self._messages: list[dict] = []

    def run(self, user_prompt: str, brief_prefix: str = "") -> list[DesignResult]:
        """
        Run the agentic loop for the given user prompt.
        Returns all DesignResult objects created during the session.
        """
        system = BASE_SYSTEM_PROMPT
        if brief_prefix:
            system = brief_prefix + "\n\n" + system

        self._messages = [{"role": "user", "content": user_prompt}]

        console.print(Panel(f"[bold cyan]Fitness Design Agent[/bold cyan]\n{user_prompt}",
                            expand=False))

        iteration = 0
        while iteration < config.AGENT_MAX_ITERATIONS:
            iteration += 1
            console.print(f"\n[dim]--- Turn {iteration} ---[/dim]")

            response = self._client.messages.create(
                model=config.CLAUDE_MODEL,
                max_tokens=config.AGENT_MAX_TOKENS,
                system=system,
                tools=TOOLS,
                messages=self._messages,
            )

            # Append assistant turn
            self._messages.append({
                "role": "assistant",
                "content": response.content,
            })

            if response.stop_reason == "end_turn":
                # Print final text response
                for block in response.content:
                    if hasattr(block, "text"):
                        console.print("\n" + block.text)
                break

            if response.stop_reason == "tool_use":
                tool_results = []
                for block in response.content:
                    if block.type == "tool_use":
                        console.print(
                            f"[bold yellow]→ Tool:[/bold yellow] {block.name}"
                        )
                        result_str = self._executor.execute(block.name, block.input)
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": result_str,
                        })

                self._messages.append({
                    "role": "user",
                    "content": tool_results,
                })
                continue

            # Unexpected stop reason
            console.print(f"[yellow]Unexpected stop_reason:[/yellow] {response.stop_reason}")
            break

        if iteration >= config.AGENT_MAX_ITERATIONS:
            console.print("[red]Max iterations reached — agent stopped.[/red]")

        return self._executor.design_results


# ── CLI output ────────────────────────────────────────────────────────────────

def print_results_table(results: list[DesignResult]) -> None:
    if not results:
        console.print("[yellow]No designs were created.[/yellow]")
        return

    console.print("\n")
    console.rule("[bold green]Design Results[/bold green]")

    for i, dr in enumerate(results, 1):
        table = Table(
            title=f"Design {i}: {dr.title}",
            box=box.ROUNDED,
            show_header=False,
            padding=(0, 1),
        )
        table.add_column("Field", style="cyan", no_wrap=True)
        table.add_column("Value", style="white")

        table.add_row("Design ID",  dr.design_id)
        table.add_row("Format",     f"{dr.format} ({dr.width}×{dr.height})")
        table.add_row("Edit URL",   dr.edit_url or "[dim]not available[/dim]")
        if dr.download_urls:
            for j, url in enumerate(dr.download_urls, 1):
                table.add_row(f"Download {j}", url)
        else:
            table.add_row("Download", "[dim]not exported[/dim]")
        if dr.design_brief_summary:
            table.add_row("Brief", dr.design_brief_summary[:120])
        if dr.etsy_listing:
            el = dr.etsy_listing
            table.add_row("Etsy Title", el.title[:80] + "…" if len(el.title) > 80 else el.title)
            table.add_row("Price",      f"${el.suggested_price_usd:.2f}")
            table.add_row("Tags",       ", ".join(el.tags[:6]) + "…")

        console.print(table)
        console.print()

    console.rule("[bold green]Next Steps[/bold green]")
    console.print(
        "1. Open each [cyan]Edit URL[/cyan] in your browser to customize the design in Canva\n"
        "2. Add your chosen fonts, colors, motivational text, and images\n"
        "3. Download as PNG from Canva\n"
        "4. List on Etsy using the generated titles, descriptions, and tags\n"
        "5. Price each design at $2.99–$4.99 for competitive Etsy positioning\n"
    )


# ── CLI entry point ───────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Canva Fitness Design Agent — create Instagram designs to sell on Etsy",
    )
    parser.add_argument(
        "prompt",
        nargs="?",
        default=None,
        help="Design request (e.g. 'Create 5 fitness motivation post designs')",
    )
    parser.add_argument(
        "--format",
        choices=["story", "post", "landscape", "mixed"],
        default="mixed",
        help="Instagram format: story (1080×1920), post (1080×1080), landscape (1080×608), or mixed",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=5,
        help="Number of designs to create (1–10, default: 5)",
    )
    parser.add_argument(
        "--oauth",
        action="store_true",
        help="Run the Canva OAuth 2.0 PKCE flow to obtain a fresh access token",
    )
    args = parser.parse_args()

    if args.oauth:
        console.print("[bold cyan]Starting Canva OAuth flow…[/bold cyan]")
        tokens = run_oauth_flow()
        console.print(f"[green]Success![/green] Access token obtained.")
        console.print(f"Tokens saved to: {config.TOKEN_CACHE_PATH}")
        console.print("You can now run the agent without --oauth.")
        return

    format_map = {
        "story":     "instagram_story",
        "post":      "instagram_post",
        "landscape": "instagram_landscape",
        "mixed":     "mixed",
    }
    design_format = format_map[args.format]

    count = max(1, min(10, args.count))

    if args.prompt:
        prompt = args.prompt
    else:
        format_label = {
            "story":     "Instagram Stories (1080×1920)",
            "post":      "Instagram Posts (1080×1080)",
            "landscape": "Instagram Landscape posts (1080×608)",
            "mixed":     "a mix of Instagram Stories and Posts",
        }[args.format]
        prompt = (
            f"Create {count} fitness influencer designs for {format_label}. "
            f"The designs should be bold, motivational, and ready to sell as digital downloads on Etsy. "
            f"Research current 2026 fitness design trends first, then create the designs."
        )

    agent = FitnessDesignAgent()
    results = agent.run(prompt)
    print_results_table(results)


if __name__ == "__main__":
    main()
