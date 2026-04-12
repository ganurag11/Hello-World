# Canva Fitness Design Agent

An AI agent that researches current fitness influencer design trends and creates
Instagram-ready designs via the Canva Connect API — complete with pre-written
Etsy listings so you can sell them as digital downloads.

## What It Does

1. **Researches** — searches the web for 2026 fitness design trends: color palettes,
   typography, Etsy SEO tags, and Instagram best practices.
2. **Creates designs** — calls the Canva Connect API to create blank canvases at the
   exact Instagram dimensions (stories, posts, or landscape).
3. **Exports** — downloads PNG files ready for Etsy.
4. **Writes Etsy listings** — generates SEO-optimized titles, descriptions, and all
   13 Etsy tags for every design.

> **Note:** The Canva Connect API creates *blank* designs at the correct dimensions.
> You open each design's edit link in Canva, add your text/images/colors, then
> download and list on Etsy. The agent handles research, sizing, batching, and
> all the Etsy copywriting.

## Prerequisites

- Python 3.11+
- A [Canva Developer](https://www.canva.com/developers/) account (free)
- An [Anthropic API key](https://console.anthropic.com/)
- *(Optional)* A [Brave Search](https://api.search.brave.com/) or
  [SerpAPI](https://serpapi.com/) key for live trend research
  (the agent works without one using built-in defaults)

## Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Create your `.env` file

```bash
cp .env.example .env
```

Open `.env` and fill in:

| Variable | Where to find it |
|---|---|
| `ANTHROPIC_API_KEY` | [console.anthropic.com](https://console.anthropic.com/) |
| `CANVA_CLIENT_ID` | Canva Developer portal (see below) |
| `CANVA_CLIENT_SECRET` | Canva Developer portal (see below) |
| `SEARCH_API_KEY` | Brave Search or SerpAPI (optional) |

### 3. Create a Canva Integration

1. Go to [canva.com/developers](https://www.canva.com/developers/) and sign in.
2. Click **Create an integration**.
3. Set the **Redirect URI** to `http://localhost:8080/callback`.
4. Enable these scopes: `design:content:write`, `design:meta:read`, `asset:read`, `asset:write`.
5. Copy the **Client ID** and **Client Secret** into your `.env`.

### 4. Authorize with Canva (first time only)

```bash
python agent.py --oauth
```

Your browser will open the Canva authorization page. After approving, tokens are
saved to `~/.canva_agent_tokens.json` and reused automatically on future runs.

## Running the Agent

```bash
# Default: 5 mixed-format designs
python agent.py

# Specific count and format
python agent.py --count 3 --format story

# Custom prompt
python agent.py "Create 4 bold gym motivation designs for female fitness coaches" --format post

# All options
python agent.py "Create 5 fitness designs" --count 5 --format mixed
```

### Format options

| `--format` | Dimensions | Instagram use |
|---|---|---|
| `story` | 1080×1920 | Stories, Reels covers |
| `post` | 1080×1080 | Square feed posts |
| `landscape` | 1080×608 | Landscape feed posts |
| `mixed` | Both story + post | (default) |

## Output

After running, you'll see a table with:

- **Design ID** — Canva's internal identifier
- **Edit URL** — open this in your browser to customize the design in Canva
- **Download URL** — direct PNG download link
- **Etsy Title** — SEO-optimized listing title (≤140 chars)
- **Price** — suggested Etsy price ($2.99–$4.99)
- **Tags** — all 13 Etsy tags

## Etsy Workflow

1. Open each design's **Edit URL** in your browser
2. Customize in Canva: add motivational text, fonts, colors, graphics
3. Download as PNG from Canva (File → Download → PNG)
4. On Etsy, create a new listing:
   - Paste the generated **title**, **description**, and **tags**
   - Upload the PNG as the digital file and as the listing photo
   - Set price to the suggested amount (adjust based on your market)
5. Publish!

## Project Structure

```
agent.py          Main agent loop and CLI entry point
canva_client.py   Canva Connect API REST wrapper + OAuth 2.0 PKCE
tools.py          Tool schemas for Claude + ToolExecutor dispatcher
research.py       DesignBrief dataclass, trend synthesis, defaults
config.py         Environment variable management and constants
requirements.txt  Python dependencies
.env.example      Template for your .env file
```

## Customizing

- **More designs per run:** `--count 10` (max 10 to respect Canva rate limits)
- **Different niche:** Change the prompt: `"Create yoga and wellness designs"`
- **Adjust Etsy pricing:** Edit `_suggest_price()` in `tools.py`
- **Change default trends:** Edit `TREND_DEFAULTS` in `research.py`
- **Add web search:** Set `SEARCH_API_KEY` in `.env` for live 2026 trend data

## Troubleshooting

**`Missing required config: ANTHROPIC_API_KEY`**
→ Make sure you copied `.env.example` to `.env` and filled in the key.

**`CanvaAuthError: No refresh token available — run --oauth again`**
→ Run `python agent.py --oauth` to get a fresh token.

**`Export job did not complete within 120s`**
→ Canva's export service is slow. Re-run the agent or call `canva_export_design`
again for the specific design_id.

**Search results are empty**
→ Leave `SEARCH_API_KEY` blank — the agent will use built-in 2026 trend defaults
and still produce valid designs and Etsy listings.
