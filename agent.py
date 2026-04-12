"""
Canva Fitness Design Agent — main entry point.

Uses the Anthropic API's built-in MCP client support to connect to the
Canva AI Connector (https://mcp.canva.com/mcp), which creates ACTUAL designed
Canva templates from natural language prompts — not blank canvases.

The agent:
  1. Researches 2026 fitness influencer design trends via web search
  2. Synthesizes a design brief (colors, fonts, layout, copy)
  3. Uses Canva MCP tools to create fully designed Instagram templates
  4. Generates complete Etsy listings with shareable Canva template links

Customers buy on Etsy → receive a Canva template link → click to copy the
design to their own Canva account → customize and download → post to Instagram.

Usage:
    python agent.py --oauth                              # First-time Canva setup
    python agent.py "Create 5 fitness stories"          # Run the agent
    python agent.py --count 3 --format post             # Explicit options
"""

from __future__ import annotations

import argparse
import json
import sys

import anthropic
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box

import config
from canva_client import client_from_cache, run_oauth_flow, load_cached_tokens
from tools import TOOLS, ToolExecutor, DesignResult

console = Console()


# ── System prompt ─────────────────────────────────────────────────────────────

BASE_SYSTEM_PROMPT = """\
You are an expert fitness design consultant and Canva template creator.
Your mission: create stunning, fully designed Instagram Canva templates for
fitness influencers to sell as digital downloads on Etsy.

Customers buy these on Etsy, receive a shareable Canva template link, click it
to copy the design to their own Canva account, then customize and post to Instagram.

## MANDATORY WORKFLOW — follow this order exactly

### Phase 1: Research (do this FIRST, always)
1. Call `web_search_fitness_trends` at least TWICE with different queries:
   - First call: focus on "color_trends" or "typography" (2026 fitness aesthetics)
   - Second call: focus on "etsy_strategy" or "competitor_analysis"
   - Optional third call for "instagram_specs" or deeper research.

2. Call `synthesize_design_brief` ONCE with ALL search result summaries.
   This gives you the color palette, fonts, layout ideas, copy suggestions,
   and 13 Etsy tags. Use this brief for ALL subsequent design decisions.

### Phase 2: Design Creation (use Canva MCP tools)
3. For EACH design, use the Canva tools (from the Canva MCP server) to create
   a FULLY DESIGNED template — not a blank canvas. You must specify:
   - Exact pixel dimensions (see below)
   - Background color / gradient from the design brief palette
   - Motivational text from the brief's copy_suggestions (bold, large)
   - Font style: bold display font (e.g. from brief's primary_font)
   - Any supporting elements: icons, shapes, dividers, subtext
   - Overall design theme and mood

   Instagram dimensions (use exactly these):
   - Story:     1080 × 1920 px
   - Post:      1080 × 1080 px
   - Landscape: 1080 × 608 px

4. After Canva creates each design, you will receive a Canva design URL.
   Note that URL — you'll pass it to `generate_etsy_listing`.

### Phase 3: Etsy Listings
5. For EACH design, call `generate_etsy_listing` with:
   - The design title, format, style details from the brief
   - The `canva_design_url` returned by the Canva tool
   This generates an SEO-optimized Etsy title, description, and 13 tags.

### Phase 4: Final Summary
6. Present a clear summary table with for each design:
   - Design title
   - Canva template URL (for sharing)
   - Etsy listing title
   - Tags and price
   - Next steps: how to set the design as a template in Canva and list on Etsy

## Design quality guidelines
- Each design must look COMPLETE and PROFESSIONAL — no placeholder text
- Use the SPECIFIC colors from the DesignBrief color_palette
- Choose motivational quotes that resonate with gym-goers and fitness coaches
- Vary the design concept for each template (different mood, layout, copy)
- Make designs bold and high-contrast — they must grab attention in Instagram feeds

## Error handling
- If a Canva tool call fails, note the error and try a slightly different approach
- If export or creation fails, log it in the final summary rather than stopping
- Never skip the research phase — always search first
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

        self._anthropic = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
        self._executor  = ToolExecutor(console)

        # Load Canva access token for MCP server authentication
        self._canva_token = self._load_canva_token()
        self._messages: list[dict] = []

    def _load_canva_token(self) -> str:
        """Load the Canva OAuth access token from cache or env vars."""
        tokens = load_cached_tokens()
        if tokens and tokens.get("access_token"):
            return tokens["access_token"]
        if config.CANVA_ACCESS_TOKEN:
            return config.CANVA_ACCESS_TOKEN
        console.print(
            "[yellow]No Canva access token found.[/yellow]\n"
            "Run [bold]python agent.py --oauth[/bold] to authorize with Canva first."
        )
        sys.exit(1)

    def run(self, user_prompt: str) -> list[DesignResult]:
        """
        Run the agentic loop.

        Uses the Anthropic API's MCP client beta to connect to the Canva AI
        Connector. The API handles all Canva MCP tool execution transparently —
        we only need to dispatch our own custom tools (research, Etsy listing).
        """
        self._messages = [{"role": "user", "content": user_prompt}]

        console.print(Panel(
            f"[bold cyan]Fitness Design Agent[/bold cyan]\n{user_prompt}",
            expand=False,
        ))

        iteration = 0
        while iteration < config.AGENT_MAX_ITERATIONS:
            iteration += 1
            console.print(f"\n[dim]--- Turn {iteration} ---[/dim]")

            response = self._anthropic.beta.messages.create(
                model=config.CLAUDE_MODEL,
                max_tokens=config.AGENT_MAX_TOKENS,
                system=BASE_SYSTEM_PROMPT,
                betas=["mcp-client-2025-04-04"],
                mcp_servers=[
                    {
                        "type": "url",
                        "url": config.CANVA_MCP_URL,
                        "name": "canva",
                        "authorization_token": self._canva_token,
                    }
                ],
                tools=TOOLS,
                messages=self._messages,
            )

            # Append assistant turn to conversation history
            self._messages.append({
                "role": "assistant",
                "content": response.content,
            })

            if response.stop_reason == "end_turn":
                for block in response.content:
                    if hasattr(block, "text"):
                        console.print("\n" + block.text)
                break

            if response.stop_reason == "tool_use":
                tool_results = []
                for block in response.content:
                    if block.type != "tool_use":
                        continue

                    tool_name = block.name

                    # Only dispatch our custom tools — Canva MCP tool calls
                    # are handled automatically by the Anthropic API before
                    # returning a stop_reason of "tool_use" to us.
                    if tool_name not in ("web_search_fitness_trends",
                                         "synthesize_design_brief",
                                         "generate_etsy_listing"):
                        # Unknown tool — return an empty result to keep the
                        # conversation flowing (shouldn't normally happen)
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": json.dumps({"note": f"Tool {tool_name!r} not handled client-side"}),
                        })
                        continue

                    console.print(f"[bold yellow]→ Tool:[/bold yellow] {tool_name}")
                    result_str = self._executor.execute(tool_name, block.input)
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

            # Unexpected stop reason (max_tokens, etc.)
            console.print(f"[yellow]Stop reason:[/yellow] {response.stop_reason}")
            if response.stop_reason == "max_tokens":
                console.print("[yellow]Response truncated — continuing…[/yellow]")
                # Continue the loop so Claude can finish
                self._messages.append({"role": "user", "content": "Please continue."})
                continue
            break

        if iteration >= config.AGENT_MAX_ITERATIONS:
            console.print("[red]Max iterations reached — agent stopped.[/red]")

        return self._executor.design_results


# ── Terminal output ────────────────────────────────────────────────────────────

def print_results_table(results: list[DesignResult]) -> None:
    if not results:
        console.print(
            "\n[yellow]No designs were recorded via generate_etsy_listing.[/yellow]\n"
            "Check the output above for Canva design URLs created during the session."
        )
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

        table.add_row("Format",            f"{dr.format}")
        table.add_row("Canva Design URL",  dr.canva_design_url or "[dim]not available[/dim]")
        table.add_row("Template Link",     dr.canva_template_url or "[dim]not set[/dim]")

        if dr.etsy_listing:
            el = dr.etsy_listing
            title_disp = el.title[:80] + "…" if len(el.title) > 80 else el.title
            table.add_row("Etsy Title",    title_disp)
            table.add_row("Price",         f"${el.suggested_price_usd:.2f}")
            table.add_row("Tags",          ", ".join(el.tags[:6]) + "…")

        console.print(table)
        console.print()

    console.rule("[bold green]Etsy Selling Instructions[/bold green]")
    console.print(
        "1. Open each [cyan]Canva Design URL[/cyan] in your browser\n"
        "2. In Canva: click [bold]Share → Share as Template[/bold] to get a copyable link\n"
        "   (or use the [cyan]Template Link[/cyan] above if already set)\n"
        "3. On Etsy: create a listing using the generated title, description, and tags\n"
        "4. Add the template link as a TXT file in the digital download\n"
        "5. Set price to $4.99–$9.99 for Canva template bundles\n"
        "6. Use a screenshot of the design as your Etsy listing photo\n"
    )


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Canva Fitness Design Agent — create Instagram Canva templates to sell on Etsy",
    )
    parser.add_argument(
        "prompt",
        nargs="?",
        default=None,
        help="Design request (e.g. 'Create 5 bold fitness motivation story templates')",
    )
    parser.add_argument(
        "--format",
        choices=["story", "post", "landscape", "mixed"],
        default="mixed",
        help="Instagram format: story (1080×1920), post (1080×1080), landscape (1080×608), mixed",
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
        run_oauth_flow()
        console.print(f"\n[green]Success![/green] Tokens saved to: {config.TOKEN_CACHE_PATH}")
        console.print("You can now run the agent without [bold]--oauth[/bold].")
        return

    format_label = {
        "story":     "Instagram Stories (1080×1920)",
        "post":      "square Instagram Posts (1080×1080)",
        "landscape": "Instagram Landscape posts (1080×608)",
        "mixed":     "a mix of Instagram Stories and Posts",
    }[args.format]

    format_key = {
        "story":     "instagram_story",
        "post":      "instagram_post",
        "landscape": "instagram_landscape",
        "mixed":     "mixed",
    }[args.format]

    count = max(1, min(10, args.count))

    if args.prompt:
        prompt = args.prompt
    else:
        prompt = (
            f"Research 2026 fitness influencer design trends, then create {count} "
            f"fully designed Canva templates for {format_label}. "
            f"Each template should be bold, motivational, and ready to sell as a "
            f"digital Canva template on Etsy. Design format: {format_key}."
        )

    agent = FitnessDesignAgent()
    results = agent.run(prompt)
    print_results_table(results)


if __name__ == "__main__":
    main()
