"""
Canva Fitness Design Agent — Gemini edition.

Uses Google's Gemini API for reasoning and the Canva AI Connector MCP server
for actual design creation. Gemini calls our custom research tools AND the
Canva MCP tools (discovered dynamically) in a single agentic loop.

Usage:
    python agent.py --oauth                              # First-time Canva setup
    python agent.py                                      # Run with defaults
    python agent.py "Create 5 dark gym story templates" --count 5 --format story
"""

from __future__ import annotations

import argparse
import json
import sys

from google import genai
from google.genai import types
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box

import config
from canva_client import load_cached_tokens, run_oauth_flow
from canva_mcp import CanvaMCPClient, CanvaMCPConnectionError
from tools import TOOLS, ToolExecutor, DesignResult

console = Console()

# Names of tools we handle ourselves (everything else goes to Canva MCP)
OUR_TOOLS = {"web_search_fitness_trends", "synthesize_design_brief", "generate_etsy_listing"}


# ── System prompt ─────────────────────────────────────────────────────────────

BASE_SYSTEM_PROMPT = """\
You are an expert fitness design consultant and Canva template creator.
Your mission: create stunning, fully designed Instagram Canva templates for
fitness influencers to sell as digital downloads on Etsy.

Customers buy these on Etsy, receive a shareable Canva template link, click it
to copy the design to their own Canva account, then customize and post to Instagram.

## MANDATORY WORKFLOW — follow this order exactly

### Phase 1: Research (always do this first)
1. Call web_search_fitness_trends at least TWICE with different queries:
   - First: focus "color_trends" or "typography"
   - Second: focus "etsy_strategy" or "competitor_analysis"
2. Call synthesize_design_brief ONCE with ALL search result summaries.
   Use the returned brief (colors, fonts, copy) for every design decision below.

### Phase 2: Create Designs (use Canva tools)
3. For EACH design, call the Canva tools to create a FULLY DESIGNED template.
   Describe every visual detail in your tool call:
   - Exact pixel dimensions: Story=1080x1920, Post=1080x1080, Landscape=1080x608
   - Background color or gradient (from the brief's color_palette)
   - Motivational headline text (large, bold — from brief's copy_suggestions)
   - Font name and style (from brief's primary_font)
   - Any supporting elements (subtext, icons, dividers, shapes)
   - Overall mood and theme

4. After Canva creates each design you will receive a design URL — save it.

### Phase 3: Etsy Listings
5. For EACH design call generate_etsy_listing with:
   - The design's title, format, style details
   - The canva_design_url returned by Canva

### Phase 4: Final Summary
6. List every design with its Canva URL, Etsy title, tags, and price.
   Include instructions: open URL → Share as Template in Canva → list on Etsy.

## Design quality rules
- Each design must look COMPLETE — no placeholder text
- Vary the concept for each template (different mood, layout, headline)
- Bold and high-contrast — must grab attention in Instagram feeds
- Use SPECIFIC colors from the DesignBrief (not generic "dark colors")
"""


# ── Agent ─────────────────────────────────────────────────────────────────────

class FitnessDesignAgent:
    def __init__(self):
        missing = config.validate()
        if missing:
            console.print(
                f"[red]Missing required config:[/red] {', '.join(missing)}\n"
                "Copy .env.example to .env and fill in the values."
            )
            sys.exit(1)

        # Configure Gemini client (new google-genai SDK)
        self._client = genai.Client(api_key=config.GEMINI_API_KEY)

        # Load Canva token
        self._canva_token = self._load_canva_token()

        # Connect to Canva MCP server and discover its tools
        console.print("[dim]Connecting to Canva AI Connector…[/dim]")
        self._canva_mcp = CanvaMCPClient(self._canva_token)
        try:
            canva_functions = self._canva_mcp.to_gemini_functions()
            console.print(f"[green]Canva MCP connected:[/green] {len(canva_functions)} tools available")
        except CanvaMCPConnectionError as exc:
            console.print(f"[red]Cannot connect to Canva MCP:[/red] {exc}")
            console.print("Run [bold]python agent.py --oauth[/bold] to authorize with Canva first.")
            sys.exit(1)

        # Executor for our custom tools
        self._executor = ToolExecutor(console)

        # Build tool declarations for Gemini
        our_declarations    = [_schema_to_declaration(t) for t in TOOLS]
        canva_declarations  = [_dict_to_declaration(f) for f in canva_functions]
        all_declarations    = our_declarations + canva_declarations

        self._gemini_tools = [types.Tool(function_declarations=all_declarations)]
        self._gen_config   = types.GenerateContentConfig(
            system_instruction=BASE_SYSTEM_PROMPT,
            tools=self._gemini_tools,
        )

    def _load_canva_token(self) -> str:
        tokens = load_cached_tokens()
        if tokens and tokens.get("access_token"):
            return tokens["access_token"]
        if config.CANVA_ACCESS_TOKEN:
            return config.CANVA_ACCESS_TOKEN
        console.print(
            "[yellow]No Canva access token found.[/yellow]\n"
            "Run [bold]python agent.py --oauth[/bold] first."
        )
        sys.exit(1)

    def run(self, user_prompt: str) -> list[DesignResult]:
        """Agentic loop using Gemini function calling (google-genai SDK)."""
        console.print(Panel(
            f"[bold cyan]Fitness Design Agent[/bold cyan] (Gemini)\n{user_prompt}",
            expand=False,
        ))

        chat     = self._client.chats.create(
            model=config.GEMINI_MODEL,
            config=self._gen_config,
        )
        response  = chat.send_message(user_prompt)
        iteration = 0

        while iteration < config.AGENT_MAX_ITERATIONS:
            iteration += 1

            # Collect all function call parts
            fn_calls = [
                part.function_call
                for part in response.candidates[0].content.parts
                if part.function_call and part.function_call.name
            ]

            if not fn_calls:
                # No tool calls — print final text and stop
                for part in response.candidates[0].content.parts:
                    if part.text:
                        console.print("\n" + part.text)
                break

            console.print(f"\n[dim]--- Turn {iteration} ({len(fn_calls)} tool call(s)) ---[/dim]")

            # Execute all function calls and build response parts
            response_parts = []
            for fc in fn_calls:
                name      = fc.name
                arguments = dict(fc.args)

                if name in OUR_TOOLS:
                    console.print(f"[bold yellow]→ Tool:[/bold yellow] {name}")
                    result_str = self._executor.execute(name, arguments)
                    result_obj = _safe_json(result_str)
                else:
                    console.print(f"[bold magenta]→ Canva:[/bold magenta] {name}")
                    result_str = self._canva_mcp.call_tool(name, arguments)
                    result_obj = _safe_json(result_str)

                response_parts.append(
                    types.Part(
                        function_response=types.FunctionResponse(
                            name=name,
                            response={"result": result_obj},
                        )
                    )
                )

            response = chat.send_message(response_parts)

        if iteration >= config.AGENT_MAX_ITERATIONS:
            console.print("[red]Max iterations reached — agent stopped.[/red]")

        return self._executor.design_results


# ── Output ────────────────────────────────────────────────────────────────────

def print_results_table(results: list[DesignResult]) -> None:
    if not results:
        console.print(
            "\n[yellow]No designs recorded via generate_etsy_listing.[/yellow]\n"
            "Check the output above for Canva design URLs."
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

        table.add_row("Format",        dr.format)
        table.add_row("Canva URL",     dr.canva_design_url or "[dim]see output above[/dim]")
        table.add_row("Template Link", dr.canva_template_url or "[dim]set after Share as Template[/dim]")

        if dr.etsy_listing:
            el = dr.etsy_listing
            title_disp = el.title[:80] + "…" if len(el.title) > 80 else el.title
            table.add_row("Etsy Title", title_disp)
            table.add_row("Price",      f"${el.suggested_price_usd:.2f}")
            table.add_row("Tags",       ", ".join(el.tags[:6]) + "…")

        console.print(table)
        console.print()

    console.rule("[bold green]Etsy Selling Steps[/bold green]")
    console.print(
        "1. Open each [cyan]Canva URL[/cyan] in your browser\n"
        "2. Canva → [bold]Share → Share as Template[/bold] → copy the link\n"
        "3. Create Etsy listing with the generated title, description, tags\n"
        "4. Add template link as a .txt digital download file\n"
        "5. Screenshot the design as your listing photo\n"
    )


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Canva Fitness Design Agent (Gemini) — create Instagram Canva templates for Etsy",
    )
    parser.add_argument("prompt", nargs="?", default=None,
                        help="Design request in plain English")
    parser.add_argument("--format", choices=["story", "post", "landscape", "mixed"],
                        default="mixed")
    parser.add_argument("--count", type=int, default=5,
                        help="Number of designs (1–10, default 5)")
    parser.add_argument("--oauth", action="store_true",
                        help="Authorize with Canva (run once before first use)")
    args = parser.parse_args()

    if args.oauth:
        console.print("[bold cyan]Starting Canva OAuth flow…[/bold cyan]")
        run_oauth_flow()
        console.print(f"\n[green]Done![/green] Tokens saved to: {config.TOKEN_CACHE_PATH}")
        console.print("You can now run the agent without [bold]--oauth[/bold].")
        return

    format_label = {
        "story":     "Instagram Stories (1080x1920)",
        "post":      "square Instagram Posts (1080x1080)",
        "landscape": "Instagram Landscape posts (1080x608)",
        "mixed":     "a mix of Instagram Stories and Posts",
    }[args.format]

    format_key = {
        "story": "instagram_story", "post": "instagram_post",
        "landscape": "instagram_landscape", "mixed": "mixed",
    }[args.format]

    count = max(1, min(10, args.count))

    prompt = args.prompt or (
        f"Research 2026 fitness influencer design trends, then create {count} "
        f"fully designed Canva templates for {format_label}. "
        f"Make them bold, motivational, and ready to sell as Canva templates on Etsy. "
        f"Design format: {format_key}."
    )

    agent   = FitnessDesignAgent()
    results = agent.run(prompt)
    print_results_table(results)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _schema_to_declaration(tool: dict) -> types.FunctionDeclaration:
    """Convert our tool schema dict to a Gemini FunctionDeclaration."""
    schema = tool.get("input_schema", {})
    params = _build_schema(schema) if schema else None
    return types.FunctionDeclaration(
        name=tool["name"],
        description=tool["description"],
        parameters=params,
    )


def _dict_to_declaration(fn: dict) -> types.FunctionDeclaration:
    """Convert a Canva MCP function dict to a Gemini FunctionDeclaration."""
    params = _build_schema(fn["parameters"]) if fn.get("parameters") else None
    return types.FunctionDeclaration(
        name=fn["name"],
        description=fn.get("description", ""),
        parameters=params,
    )


def _build_schema(schema: dict) -> types.Schema:
    """Recursively convert a JSON Schema dict to a Gemini Schema object."""
    type_map = {
        "object":  types.Type.OBJECT,
        "string":  types.Type.STRING,
        "integer": types.Type.INTEGER,
        "number":  types.Type.NUMBER,
        "boolean": types.Type.BOOLEAN,
        "array":   types.Type.ARRAY,
    }
    gemini_type = type_map.get(schema.get("type", "object"), types.Type.OBJECT)

    properties = None
    if "properties" in schema:
        properties = {k: _build_schema(v) for k, v in schema["properties"].items()}

    items = None
    if "items" in schema:
        items = _build_schema(schema["items"])

    return types.Schema(
        type=gemini_type,
        description=schema.get("description", ""),
        properties=properties,
        required=schema.get("required"),
        items=items,
        enum=schema.get("enum"),
    )


def _safe_json(s: str) -> dict | str:
    try:
        return json.loads(s)
    except Exception:
        return s


if __name__ == "__main__":
    main()
