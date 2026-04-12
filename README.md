# Canva Fitness Design Agent

An AI agent that researches 2026 fitness influencer design trends and uses the
**Canva AI Connector** (MCP server) to create fully designed, ready-to-sell
Canva templates for Instagram — complete with pre-written Etsy listings.

## How It Works

1. **Researches** — searches the web for 2026 fitness design trends: color palettes,
   typography, Etsy SEO strategy, and Instagram best practices.

2. **Creates real designs** — connects to the Canva AI Connector MCP server and
   creates **fully designed Canva templates** from detailed natural language prompts.
   These are not blank canvases — they have background colors, motivational text,
   fonts, and layout elements specified from the research brief.

3. **Generates shareable links** — each design gets a Canva template URL that
   customers can click to copy the design to their own Canva account.

4. **Writes Etsy listings** — generates SEO-optimized titles, descriptions, and
   all 13 Etsy tags for every design.

## The Etsy Selling Model (Canva Templates)

This is the standard way Canva templates are sold on Etsy:

```
You (seller)                    Your customer (buyer)
──────────────────────────────────────────────────────
1. Agent creates design         1. Finds your Etsy listing
2. Get Canva template link      2. Purchases ($4.99–$9.99)
3. List on Etsy with link       3. Receives Canva template link
4. Deliver link as download     4. Clicks → copies to Canva account
                                5. Customizes text, colors, fonts
                                6. Downloads & posts to Instagram ✓
```

No design skills needed from your customer — Canva does all the editing.

## Prerequisites

- Python 3.11+
- A [Canva Developer](https://www.canva.com/developers/) account (free)
- An [Anthropic API key](https://console.anthropic.com/)
- *(Optional)* A [Brave Search](https://api.search.brave.com/) or
  [SerpAPI](https://serpapi.com/) key for live trend research
  (the agent works without one using built-in 2026 defaults)

## Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure your environment

```bash
cp .env.example .env
```

Open `.env` and fill in:

| Variable | Where to get it |
|---|---|
| `ANTHROPIC_API_KEY` | [console.anthropic.com](https://console.anthropic.com/) |
| `CANVA_CLIENT_ID` | Canva Developer portal (step below) |
| `CANVA_CLIENT_SECRET` | Canva Developer portal (step below) |
| `SEARCH_API_KEY` | Brave Search or SerpAPI *(optional)* |

### 3. Create a Canva Integration

1. Go to [canva.com/developers](https://www.canva.com/developers/) → **Create an integration**
2. Set **Redirect URI** → `http://localhost:8080/callback`
3. Enable scopes: `design:content:write`, `design:meta:read`, `asset:read`, `asset:write`
4. Copy **Client ID** and **Client Secret** into your `.env`

### 4. Authorize with Canva (one-time setup)

```bash
python agent.py --oauth
```

Your browser opens the Canva authorization page. After approving, tokens are
saved to `~/.canva_agent_tokens.json` and reused automatically.

## Running the Agent

```bash
# Default: 5 mixed-format designs
python agent.py

# Instagram Stories specifically
python agent.py --count 5 --format story

# Custom prompt
python agent.py "Create 4 dark luxury gym templates for female fitness coaches" --format post

# Square posts, 3 designs
python agent.py --count 3 --format post
```

### Format options

| `--format` | Dimensions | Instagram use |
|---|---|---|
| `story` | 1080 × 1920 px | Stories, Reels covers |
| `post` | 1080 × 1080 px | Square feed posts |
| `landscape` | 1080 × 608 px | Landscape feed posts |
| `mixed` | Both story + post | *(default)* |

## Output

After running, you get a table with for each design:

- **Canva Design URL** — open in your browser to view/edit the design
- **Template Link** — the `/copy` URL to share with Etsy customers
- **Etsy Title** — SEO-optimized (≤140 chars)
- **Tags** — all 13 Etsy tags
- **Price** — suggested price ($4.99–$9.99)

## Listing on Etsy

1. Open the **Canva Design URL** — confirm it looks great
2. In Canva: **Share → Share as Template** → copy the template link
3. Create an Etsy listing:
   - Paste the generated **title**, **description**, and **13 tags**
   - Upload a PNG screenshot of the design as your listing photo
   - Create a `.txt` file containing the Canva template link → upload as digital file
4. Price at **$4.99 per template** or bundle 5 templates for **$14.99**
5. Publish!

> **Tip:** Canva template bundles (5–10 designs) sell better than single templates
> on Etsy. Run the agent with `--count 10` to create a full bundle in one go.

## Project Structure

```
agent.py          Main agent loop + CLI (uses Canva MCP via Anthropic beta API)
tools.py          Custom tool schemas (research + Etsy) + ToolExecutor
research.py       DesignBrief dataclass, trend synthesis, 2026 defaults
canva_client.py   Canva OAuth 2.0 PKCE flow + token management
config.py         Environment variable management and constants
requirements.txt  Python dependencies
.env.example      Environment variable template
```

## How the Canva AI Connector Works

The agent uses the **Anthropic API's MCP client beta** to connect to Canva's
remote MCP server at `https://mcp.canva.com/mcp`. When Claude creates a design,
it calls the Canva MCP tools with a detailed natural language description:

```
"Create an Instagram Story (1080×1920) with:
 - Background: deep navy (#1A1A2E) to black gradient
 - Headline: 'NO DAYS OFF' in Bebas Neue, bold, white, centered, 120px
 - Subtext: 'Train hard. Stay consistent.' in Montserrat, 32px, #FF4500
 - Bottom: subtle horizontal divider line in orange"
```

Canva creates the design and returns a URL — a real, fully designed Canva
template that your customer can copy and customize.

## Troubleshooting

**`No Canva access token found`**
→ Run `python agent.py --oauth` to authorize.

**`Missing required config: ANTHROPIC_API_KEY`**
→ Copy `.env.example` to `.env` and fill in your keys.

**Search results empty**
→ Leave `SEARCH_API_KEY` blank — the agent uses built-in 2026 defaults and
  still produces complete designs and Etsy listings.

**Design URL in output is blank**
→ The Canva MCP may return the URL inside the conversation text.
  Check the agent output above the results table for design URLs.
