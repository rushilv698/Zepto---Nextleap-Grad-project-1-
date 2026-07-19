# Zepto Discovery Engine — Frontend Spec (for Claude Design)

**Product** — A hosted dashboard that demonstrates a review-analysis workflow. It shows
what public user feedback we've collected across four Indian quick-commerce brands, how
we filtered it, what themes emerged, what insights survived a multi-layer validation
gate, and lets a reviewer (a) ask the engine free-form questions and (b) trigger a live
scrape with their own API key.

**Deliverable** — a live URL a reviewer can open and test. Used as Deliverable #1 of the
NextLeap Grad Project 1 submission.

**Strategic goal it answers** — *Increase the % of Monthly Active Zepto Customers who
buy from at least one new category every month.*

---

## 1 · Deployment shape

- **Frontend**: Next.js 15 (app router) + TypeScript + Tailwind CSS + shadcn/ui, deployed
  to Vercel.
- **Backend**: FastAPI (Python) + Postgres, deployed to Railway. Postgres has the
  existing schema already loaded from `pg_dump` (see backend spec below).
- **Data flow**: Frontend reads via typed fetches against `NEXT_PUBLIC_API_URL`.
  All heavy analysis is precomputed and stored in Postgres. The frontend only *reads*
  except for two endpoints (chat, scrape) which take reviewer-supplied API keys and
  proxy to third parties.

---

## 2 · Brand & visual language

### Palette
| Token | Hex | Usage |
|---|---|---|
| `--zepto-purple` | `#7C21DA` | Primary accent, links, focus rings, primary buttons |
| `--zepto-purple-deep` | `#5C0DBA` | Hover, gradient stop, deep headings |
| `--zepto-purple-soft` | `#F6F0FE` | Active tab background, subtle chip backgrounds |
| `--zepto-pink` | `#EE1B7C` | "Add" / CTA buttons only (matches Zepto's product-card CTA) |
| `--zepto-green` | `#16A34A` | Success, price/save annotations |
| `--surface` | `#FFFFFF` | Card background |
| `--surface-muted` | `#F8FAFC` | Page background |
| `--text` | `#171717` | Body |
| `--text-muted` | `#525252` | Secondary text, captions |
| `--border` | `#EDE7F6` | Card borders (soft violet) |

### Typography
- Font stack: `-apple-system, BlinkMacSystemFont, "Inter", "Segoe UI", "Helvetica Neue", Arial, sans-serif`
- H1: 32px, weight 800, `letter-spacing: -0.01em`
- H2: 24px, weight 700
- H3: 18px, weight 600
- Body: 14px, weight 400, line-height 1.5

### Spacing & elevation
- Card border-radius: **14px**
- Card border: `1px solid var(--border)`
- Card shadow: none by default; brand-bar gets `0 6px 24px rgba(124, 33, 218, 0.18)`
- Grid gutter: 16px on mobile, 24px on desktop
- Section spacing: 32px vertical between sections

### Brand bar (top of every page)
Purple gradient banner, 100% width, 72px tall:
- Left: white rounded pill (10px radius) containing the **zepto** wordmark in
  `var(--zepto-purple)`. Use the SVG from `/public/logo.svg` (I'll ship one; falls back
  to bold "zepto" text if missing).
- Right of pill: title "AI-Powered Discovery Engine" (20px, weight 800, white) with
  subtitle "Zepto Growth · Category expansion research" (13px, opacity 0.9).
- Background: `linear-gradient(135deg, #7C21DA 0%, #5C0DBA 100%)`

Below the brand bar is a horizontal tab nav (see section 3).

---

## 3 · Navigation & page structure

Horizontal top nav with **7 pages**. Active pill: `background: var(--zepto-purple-soft)`,
`color: var(--zepto-purple)`. Inactive: text-only, muted gray.

| # | Path | Label | Purpose |
|---|---|---|---|
| 1 | `/` | Dashboard | The story in one page — top-line + ranked usable insights |
| 2 | `/data-sources` | Data Sources | Where the data came from + live-scrape button |
| 3 | `/themes` | Themes | Evolving taxonomy — parent buckets and leaf themes |
| 4 | `/insights` | Insights | All insights the engine generated (usable + shelved) |
| 5 | `/quality` | Quality Validated | Multi-layer validation matrix + funnel |
| 6 | `/ask` | Ask the Engine | Chatbot — 8 seed prompts + free-form RAG |
| 7 | `/settings` | Settings | Reviewer's OpenAI/Groq/Apify API keys |

Below the tab strip on every page: an optional "Demo mode" info banner if no backend
data is loaded (dev/preview state).

---

## 4 · Reusable component library

### `<KpiCard label value delta hint />`
Small card, 14px radius, 20px padding.
- **Label**: 12px, uppercase-tracked, `var(--zepto-purple-deep)`, weight 600.
- **Value**: 32px, weight 800, `var(--text)`.
- **Delta** (optional): 12px, green if positive / red if negative, arrow icon.
- **Hint** (optional): 12px muted with a small `?` icon (lucide `HelpCircle`) → tooltip.

### `<InsightCard rank title rating status source claim implication experiment probe details />`
The core content card. 14px radius, `border: 1px solid var(--border)`.
- **Header row**: `#{rank} · {title} · <RatingBadge rating /> · <StatusBadge status />`
  - Rank prefix optional; hide if not passed.
  - Title in 18px semibold.
- **Source line**: 12px muted, "Source: Working hypothesis" or "Evidence-linked", with
  a small info tooltip explaining the source type.
- **Body**:
  - **Claim** — bold label + body text.
  - **Implication for Zepto** — only for working hypotheses.
  - **Suggested 2-week experiment** — only if provided.
  - **Interview probe** — italic quote block.
- **Two collapsibles**:
  - "Why this rating?" (auto-generated per-check breakdown for evidence-linked
    insights, or one-paragraph explanation for working hypotheses).
  - "More detail" — free-form details from the API.

### `<RatingBadge rating />`
Pill, 6px vertical / 10px horizontal padding, 999px radius.
- `high` → 🟢 High (background `#DCFCE7`, text `#166534`)
- `medium` → 🟡 Medium (background `#FEF3C7`, text `#92400E`)
- `low` → 🔴 Low (background `#FEE2E2`, text `#991B1B`)

### `<StatusBadge status />`
Same shape as RatingBadge, different colors:
- `confirmed` → 🟢 Confirmed (`#DCFCE7`)
- `exploratory` → 🟡 Exploratory (`#FEF3C7`)
- `revising` → 🟠 Needs revision (`#FED7AA`)
- `shelved` → 🔴 Shelved (`#FEE2E2`)

### `<SourceCard source brand snippet_count last_run samples onLiveRun />`
- 14px radius card. Icon on left based on source (`lucide-react`):
  Play Store → `Smartphone`, Reddit → `MessageSquare`, YouTube → `Youtube`, News → `Newspaper`.
- Below: brand pills (chips) + snippet_count + last_run relative time.
- "View last 5 captures" collapsible with verbatim quotes.
- **Live Run button**: purple, `zepto-purple-soft` background. Disabled if reviewer
  hasn't added an Apify key in Settings. Tooltip on hover: "Add your Apify key in Settings
  to trigger a live scrape".

### `<Funnel steps />`
Simple SVG funnel chart, no chart library needed. Each step is a trapezoid getting
smaller. Steps are labeled with count + percent-of-first.

### `<ChatMessage role content citations />`
- `role=user`: right-aligned bubble, `background: var(--zepto-purple-soft)`.
- `role=assistant`: left-aligned bubble, white with soft border, streamed text.
- Citations render as small `InsightCard`s below the assistant message, muted background.

### `<Expander title children expanded={false} />`
Basic HTML `<details>` styled with chevron icon and 12px muted label.

---

## 5 · Page-by-page spec

### 5.1 Dashboard (`/`)

**Above the fold — KPI row (4 cards):**
- **Total snippets** — `raw_totals.raw`. Hint: "Public user feedback we've collected from all sources"
- **Themes discovered** — count from `themes` where `taxonomy_version=2` and `merged_into IS NULL`
- **Usable insights** — count from `insights_v2` where `taxonomy_version=2` and `validation_status != 'shelved'` **plus** count from `corpus_hypotheses`
- **Sources** — count of distinct `raw_counts.source` (currently 4)

**Below KPI row — top-line reading banner** (soft violet background box, purple-deep icon):
> **Top-line reading of the corpus:** *{corpus_hypotheses[0].top_line_read}*

**Below banner — "All usable insights, ranked by confidence" section:**
- Section H2 + caption explaining that insights are grouped by rating.
- List of `InsightCard`s, ordered High → Medium → Low.
- Insights come from combined query — see `GET /api/insights?filter=usable`.
- Show rank `#1`, `#2`, ... on each card.

Below list — small footer link to `/quality` explaining the drop-off.

### 5.2 Data Sources (`/data-sources`)

Grid of 4 `<SourceCard>`s (Play Store, Reddit, YouTube, News). Data from `GET /api/sources`.

**Above the grid — a "How data was obtained" expander** (collapsed by default) with
the source method for each channel + the "Why did we collect competitor data" explainer
(paste from the existing Streamlit tab — same content).

**Live scrape flow**:
1. User clicks "Live Run" on a card.
2. If no Apify key in localStorage → toast + link to `/settings`.
3. If key present → button shows spinner, calls `POST /api/scrape { source, apifyKey, max_items: 25 }`.
4. Response `{ items: [{ text, author, url }] }` — append to the card's sample list under a new "Live run · {timestamp}" section.
5. On error, show inline error red text with the API error message. No retries.

Below the grid — the sources × brand breakdown pivot table + Zepto vs competitor pie
chart (Recharts).

### 5.3 Themes (`/themes`)

- Top: 4 KpiCards — Total themes / Parent buckets / Leaf themes / Emergent themes (data via `GET /api/themes/stats`)
- "How to read this — parent buckets vs leaf themes" expander (collapsed)
- **Horizontal bar chart** of top-20 leaf themes by member count. Bars colored by parent bucket.
- **Hierarchy view**: for each parent bucket, an `<Expander>` (auto-expanded when total members > 15) that lists its leaf themes with badge (`seed` or `emergent`), member count, and definition below.

### 5.4 Insights (`/insights`)

- "How these insights were generated" expander at top (content in section 8 below).
- Row of 2 KpiCards: **Total themes** and **Insights with supporting evidence ≥ 5**.
- Expander: "Why don't ALL themes get an insight?" — full explanation including "Why 5" and the hypothetical ≥3 analysis (content in section 8).
- **Ranked list** of all evidence-linked insights (from `GET /api/insights?filter=all`), showing #1, #2, #3.
- Below the list, a section: **"N themes rejected before generation"** — a compact card per rejected theme showing name + `🔴 Rejected before generation` + definition + `Reason: only X supporting review(s) — minimum 5 required.`

### 5.5 Quality Validated (`/quality`)

- Expander at top: "How validation works" — full text including the 5 checks (content in section 8).
- **Validation funnel** — an SVG funnel showing: *Insights generated → Passed LLM Critic → Usable on Dashboard*. Data from `GET /api/quality/funnel`.
- **The five automated checks** — a simple 3-column table (Layer / Rule / Enforcement) rendered from a static JS constant, since this content doesn't change.
- **Per-insight validation matrix** — a table (rank, theme, rating, confidence_score, status, evidence, cross_source, sources, brands, statistical, unique_authors, cluster_quality, intra_cosine, critic). Data from `GET /api/quality/matrix`. Sortable columns.
- **Usability summary** — 4 KpiCards: Total insights / Confirmed / Exploratory / Shelved.
- **Yellow "Honest gaps" callout** — Behavioural + Business validation aren't evaluated because they need Zepto internal analytics. Text in section 8.
- **"How to use these insights"** — 3 colored bands (green/blue/red) with the confirmed/exploratory/shelved usage guidance.

### 5.6 Ask the Engine (`/ask`)

Two vertical sections.

**Section A — Seed prompts (top)**
Grid of 8 pill buttons for the mission questions. Data from `GET /api/seed-prompts`.
Buttons are the question text; on click, the answer streams into a chat message below.

| # | Question |
|---|---|
| 1 | Why do users repeatedly buy from the same categories? |
| 2 | What prevents users from exploring new categories? |
| 3 | How do users discover products today? |
| 4 | What role do habits play in shopping behavior? |
| 5 | What information do users need before trying a new category? |
| 6 | What frustrations emerge repeatedly? |
| 7 | Which user segments are more likely to experiment? |
| 8 | What unmet needs emerge consistently? |

Seed prompt answers are **precomputed and served by the backend** (no API keys needed).
Clicking a seed prompt appends a `<ChatMessage>` with the canned answer + citations.

**Section B — Free-form chat (below)**
- Chat message list (last N messages, scroll-to-bottom).
- Textarea + Send button.
- On send: check that reviewer has BOTH Groq and OpenAI keys in Settings.
  - If missing: toast with link to `/settings`, don't send.
  - If present: `POST /api/ask { message, openaiKey, groqKey }` → backend embeds the query, retrieves top-8 snippets, calls Groq Llama-3.3-70B with a "cite your evidence" system prompt, streams the response.
- Response streams into a new assistant `<ChatMessage>`. When done, citations render as compact InsightCards below.

Top-right corner: "Clear conversation" button.

### 5.7 Settings (`/settings`)

Simple form with 3 sections:
- **OpenAI API key** — needed for embedding user queries in the chatbot. Password-style input with show/hide toggle. Placeholder: `sk-...`. Small "Test key" button that calls `POST /api/keys/test { openaiKey }`.
- **Groq API key** — needed for the chatbot's answer generation. Same treatment. Placeholder: `gsk_...`.
- **Apify API token** — needed for live scraping. Same treatment. Placeholder: `apify_api_...`.

All keys stored in `localStorage` via a Zustand store; **never sent to Zepto Discovery's own backend** — only proxied through per-request. Bottom of page: a "🔒 Keys never leave your browser except when you use them" reassurance note.

**Save button** at the bottom (saves the Zustand state, since Zustand persists to localStorage automatically).

---

## 6 · Data shapes (TypeScript types)

Put these in `types/api.ts`. They mirror the backend Pydantic schemas.

```ts
// KPIs
export type Totals = {
  raw: number;
  filtered: number;
  extracted: number;
  cards: number;
};

// Insight cards
export type SourceType = "Working hypothesis" | "Evidence-linked";
export type Rating = "high" | "medium" | "low";
export type Status = "confirmed" | "exploratory" | "revising" | "shelved" | null;
export type CriticVerdict = "pass" | "revise" | "reject" | null;

export type ConfidenceBreakdown = {
  evidence_pass?: boolean;
  cross_source_pass?: boolean;
  statistical_pass?: boolean;
  cluster_quality_pass?: boolean;
  critic_verdict?: CriticVerdict;
  evidence_source_count?: number;
  evidence_brand_count?: number;
  unique_authors?: number;
  intra_cluster_sim?: number;
  theme_fraction?: number;
  behavioural_validation?: "not_evaluated";
  business_experiment_validation?: "deferred_to_part_4";
};

export type Insight = {
  id: string;              // stable
  title: string;
  rating: Rating;
  source_type: SourceType;
  claim: string;
  implication: string | null;
  interview_probe: string | null;
  detailed: string | null;
  suggested_experiment: string | null;
  status: Status;
  critic_verdict: CriticVerdict;
  confidence_score: number | null;   // 0–100 for evidence-linked; null for working hypotheses
  confidence_breakdown: ConfidenceBreakdown | null;
  passes_gate: boolean;    // used by the Dashboard filter
  rank: number;            // 1-indexed position in the overall ranked list
};

// Themes
export type ThemeStatus = "seed" | "promoted" | "merged" | "archived";
export type Theme = {
  id: number;
  name: string;
  definition: string | null;
  status: ThemeStatus;
  parent_id: number | null;
  parent_name: string | null;
  members: number;
};

// Sources
export type SourceCard = {
  source: "play_store" | "reddit" | "youtube" | "news";
  display_name: string;
  brands: string[];
  snippet_count: number;
  last_run: string;          // ISO 8601
  sample_snippets: {
    text: string;
    author: string | null;
    url: string | null;
    posted_at: string | null;
  }[];
};

// Quality
export type QualityMatrixRow = {
  rank: number;
  insight_id: string;
  theme: string;
  rating: Rating;
  confidence_score: number;
  status: Status;
  evidence_pass: boolean;
  cross_source_pass: boolean;
  n_sources: number;
  n_brands: number;
  statistical_pass: boolean;
  unique_authors: number;
  cluster_quality_pass: boolean;
  intra_cosine: number | null;
  critic_verdict: CriticVerdict;
};

export type FunnelStep = { label: string; count: number };

// Chatbot
export type ChatMessage = {
  role: "user" | "assistant";
  content: string;
  citations?: Insight[];
};

export type SeedPrompt = {
  id: number;
  question: string;
  answer: string;
  citations: string[];    // insight IDs
};
```

---

## 7 · API contract (FastAPI)

Base URL: `NEXT_PUBLIC_API_URL` (e.g. `https://zepto-engine-backend.railway.app`).

CORS must allow the Vercel frontend origin.

### Public read endpoints (no keys)

| Method | Path | Returns |
|---|---|---|
| GET | `/api/totals` | `Totals` |
| GET | `/api/insights?filter=usable\|all\|shelved` | `Insight[]` |
| GET | `/api/insights/:id` | `Insight` |
| GET | `/api/themes` | `Theme[]` |
| GET | `/api/themes/stats` | `{ total, parents, leaves, emergent }` |
| GET | `/api/sources` | `SourceCard[]` |
| GET | `/api/quality/matrix` | `QualityMatrixRow[]` |
| GET | `/api/quality/funnel` | `FunnelStep[]` |
| GET | `/api/quality/summary` | `{ confirmed, exploratory, shelved }` |
| GET | `/api/rejected-themes` | `{ id, name, members, definition }[]` |
| GET | `/api/corpus-hypothesis-toplineread` | `{ text: string }` |
| GET | `/api/seed-prompts` | `SeedPrompt[]` |

### Key-scoped endpoints (reviewer supplies key in body)

| Method | Path | Body | Returns |
|---|---|---|---|
| POST | `/api/keys/test` | `{ openaiKey?, groqKey?, apifyKey? }` | `{ openai: bool, groq: bool, apify: bool }` |
| POST | `/api/scrape` | `{ source, apifyKey, max_items }` | `{ items: {text, author, url}[] }` (stream JSON lines) |
| POST | `/api/ask` (SSE stream) | `{ message, openaiKey, groqKey }` | Server-sent events: `token` events with `{ chunk }`, then a final `citations` event with `Insight[]` |

### Error shape
All errors return JSON:
```json
{ "error": "friendly message", "code": "SNAKE_CASE_CODE" }
```

---

## 8 · Content library

The following text lives in the frontend as constants (won't change often). Paste
verbatim.

### 8.1 Dashboard "How to read this" (collapsed by default)
> This page shows the **final usable output** of the pipeline — insights that either
> (a) come from whole-corpus reasoning, or (b) passed the 5-check validation gate + LLM Critic.
>
> - **Top-line reading** — a single sentence describing what the corpus tells us about the goal.
> - **Ranking** — insights are listed by confidence (High → Medium → Low). #1 is the most confident.
> - **Source badge** on each card: *Working hypothesis* (reasoned across the whole corpus) or *Evidence-linked* (grounded in specific cited quotes).
> - **Rating** = confidence in the hypothesis. **Status** (evidence-linked only) = where the insight sits after the validation gate.
>
> Insights the critic rejected are hidden here but visible in the Insights and Quality Validated tabs.

### 8.2 Data Sources · "How this data was obtained"
> The engine gathers public user feedback at scale from four independent surface areas:
>
> 1. App-store reviews — direct product feedback, high volume, complaint-shaped.
> 2. Reddit posts and comments — organic discussion, richer mental-model signals.
> 3. YouTube comments — reactions to ads, hauls, comparison videos, founder interviews.
> 4. News/business media — analyst framing that shapes consumer perception.
>
> **Multi-brand**: Zepto (primary) + 3 competitors as control (Blinkit, BigBasket, Swiggy Instamart).
> **Multi-language**: English, Hindi, Hinglish captured. No translation — LLMs handle all three natively.

### 8.3 Data Sources · "Why did we collect competitor data?"
> The project is about Zepto — most of the snippets are Zepto. The remainder are from three
> competitors (Blinkit, BigBasket, Swiggy Instamart) and serve a specific analytical purpose:
>
> 1. **Cross-source validation** — the confirmed-status quality check requires evidence
>    across ≥ 2 brands. Without competitor data, this check is meaningless.
> 2. **Zepto-specific vs industry-wide separation** — same complaint on all four apps
>    = industry friction, not a Zepto opportunity.
> 3. **Real user comparison behavior** — exploration mental models like "I use Zepto for
>    X but Blinkit for Y" only surface when both brands are named.
>
> Competitor data is a **control group**, deliberately smaller than the Zepto primary set.

### 8.4 Themes · "How to read this — parent buckets vs leaf themes"
> Themes are organized in a **two-level hierarchy** so both high-level reporting and
> detailed behavioral nuances stay visible:
>
> - **Parent buckets** — the big categories (organizing shelves). Example:
>   "Value Perception & Price Sensitivity".
> - **Leaf themes** — specific behaviours nested under a parent. Example:
>   "Price Sensitivity and Perceived Value" or "Repeat-Order Habit Loops".
>
> Two types of leaf themes:
> - **seed** — one of the original 12 themes generated by the LLM from a 200-review sample.
> - **emergent** — discovered later by the DBSCAN clustering step from snippets that didn't
>   fit any existing theme. These are the new patterns the pipeline surfaced without being
>   told to look for them.
>
> **Insights are generated per leaf theme, not per parent bucket** — the parent is just
> an organizing shelf, the leaf is where the story lives.

### 8.5 Insights · "How these insights were generated"
> Each insight is one hypothesis per leaf theme with 5+ supporting reviews.
>
> - **Sampling** — up to 15 highest-quality supporting reviews per theme.
> - **Generation** — DeepSeek reads that sample and produces a causal claim, cites
>   specific review IDs as supporting evidence, and drafts a suggested 2-week experiment
>   plus an open-ended interview probe.
> - **Output per theme** — one insight card containing: hypothesis · one-line · detailed
>   reasoning · suggested experiment · interview probe.
>
> This tab shows **every insight the engine generated — before any validation was applied**.
> The Quality Validated tab then puts each through the 5-check matrix + LLM Critic.

### 8.6 Insights · "Why don't ALL themes get an insight?"
> The engine generates **one insight per leaf theme**, but only when the theme has at
> least 5 supporting reviews. This threshold exists to avoid fabricated-sounding claims
> — a hypothesis inferred from 1 or 2 quotes is speculation, not signal.
>
> Of the themes shown in the Themes tab:
> - Parent buckets — organizing shelves with no direct member reviews. Their children carry the reviews.
> - Leaf themes with 1–4 supporting reviews — the engine skips them because the LLM Critic would reject any insight from them anyway (evidence check needs ≥ 5 citations).
> - Leaf themes with ≥ 5 supporting reviews — these qualified for insight generation.
>
> ---
>
> **Why 5 and not some other number?** Three overlapping reasons:
>
> 1. **The downstream Evidence check needs ≥ 5 citations.** The 5-check validation gate
>    (see the Quality Validated tab) has an Evidence rule: "≥ 5 supporting review IDs
>    must be cited by the generator." A theme with only 4 supporting reviews can only cite
>    4 quotes — the Evidence check would fail automatically, and the insight would be
>    shelved regardless.
> 2. **PDF methodology guidance.** The methodology doc suggests ≥ 8 members for theme
>    promotion. 5 is the softened compromise so v2.1 (only 546 expansion-relevant snippets)
>    produces any insights at all.
> 3. **LLM hallucination risk.** Below ~5 quotes the generator starts extrapolating —
>    "these 3 users said X, therefore users in general believe Y" — producing
>    confident-sounding but fabricated claims.
>
> ---
>
> **What would happen if we lowered the threshold to ≥ 3?** Five themes would newly
> qualify (including Category Exploration and Expansion — the on-goal theme). But the
> validation gate would almost certainly shelve all five: Evidence check would fail (3–4
> cited quotes < 5 required), Statistical check would fail (3–4 unique authors << 20
> required), Cross-source rarely spans ≥ 2 sources AND ≥ 2 brands. Documented here as a
> considered tradeoff, not run.

### 8.7 Quality · "How validation works"
> The PDF methodology says: "insights generated by AI should never be accepted without
> validation." This tab implements that philosophy — every theme-level insight is
> subjected to **five automated checks + an independent LLM Critic Agent** before it's
> tagged usable.
>
> **The five checks each insight must clear:**
>
> 1. **Evidence** — the generator must have cited ≥ 5 supporting review IDs. Insufficient citations → shelved.
> 2. **Cross-source** — the cited evidence must span ≥ 2 different sources (e.g., Play Store, Reddit, YouTube) AND ≥ 2 different brands (Zepto + at least one competitor). Same-source evidence gets marked *exploratory* rather than *confirmed*.
> 3. **Statistical** — the underlying theme cluster must have ≥ 20 unique authors AND cover at least 0.5% of the filtered corpus. Prevents fringe hypotheses from a handful of loud voices.
> 4. **Cluster quality** — the theme's supporting reviews must have a mean intra-cluster cosine similarity ≥ 0.70. Ensures the reviews are actually about the same thing.
> 5. **LLM Critic Agent** — GPT-4.1 (a different model from the generator) reads the insight plus the supporting evidence plus 10 retrieved counter-evidence snippets, and returns a verdict: **pass** / **revise** / **reject**. Rejects are shelved from the Dashboard.
>
> **Two design choices worth calling out**
>
> - The Critic is a DIFFERENT model from the generator (DeepSeek generates; GPT-4.1 critiques). Same model marking its own homework is worthless.
> - The Critic sees retrieved counter-evidence, not just supporting quotes.
>
> **Two validation layers from the PDF are honestly not evaluated** — Behavioural Validation
> (recommendation CTR, category visits, repeat purchases) and Business & Experiment Validation
> (A/B uplift) both require Zepto internal analytics we don't have. They're flagged
> `not_evaluated` and `deferred_to_part_4` in every insight's confidence breakdown — never
> silently skipped.

### 8.8 Quality · Honest gaps callout (yellow)
> **Honest gaps:** Behavioural validation (recommendation CTR, category visits, repeat
> purchases) and Business & Experiment validation (A/B uplift) require Zepto internal
> analytics we don't have. They're flagged `not_evaluated` and `deferred_to_part_4` in
> every insight's confidence breakdown — not silently skipped.

### 8.9 Ask the Engine · empty-state hint (top of chat)
> Click a question below for an instant precomputed answer, or type your own question.
> Free-form questions need your OpenAI + Groq keys in Settings — set up once, then chat
> anonymously.

### 8.10 Settings · reassurance note
> 🔒 Your keys never leave your browser except when you use them. They're stored in
> `localStorage` and sent as a one-shot header on the specific API call that needs them.
> We do not persist, log, or share them.

---

## 9 · Behaviors & polish

- **Loading states**: every page shows skeleton cards while data is loading. Use
  Tailwind's `animate-pulse` on placeholder blocks.
- **Empty states**: if `insights.length === 0`, show a friendly empty state suggesting
  the reviewer wait for the backend to seed data.
- **Error states**: on API failure, show an inline error card with the message + a
  "Retry" button.
- **Streaming**: use `EventSource` for `/api/ask`. Show a blinking cursor while
  streaming, tokenizing on newlines.
- **Responsive**: mobile-first. Single column below 768px. KPI cards stack. Chat is
  always full width on mobile.
- **A11y**: all inputs labeled, focus outlines use `var(--zepto-purple)`, contrast passes
  WCAG AA.
- **Meta**: `<title>Zepto Discovery Engine</title>`. Purple favicon.

---

## 10 · Repo layout Claude Design should produce

```
web/
├── app/
│   ├── layout.tsx
│   ├── page.tsx                     Dashboard
│   ├── data-sources/page.tsx
│   ├── themes/page.tsx
│   ├── insights/page.tsx
│   ├── quality/page.tsx
│   ├── ask/page.tsx
│   ├── settings/page.tsx
│   └── globals.css                  brand tokens + tailwind directives
├── components/
│   ├── BrandBar.tsx
│   ├── TabNav.tsx
│   ├── KpiCard.tsx
│   ├── InsightCard.tsx
│   ├── RatingBadge.tsx
│   ├── StatusBadge.tsx
│   ├── SourceCard.tsx
│   ├── Funnel.tsx
│   ├── ChatMessage.tsx
│   ├── Expander.tsx
│   └── ui/                          shadcn generated
├── hooks/
│   ├── useKeys.ts                   Zustand store, localStorage-backed
│   └── useApi.ts                    generic fetch hook (typed)
├── lib/
│   ├── api.ts                       typed fetch to NEXT_PUBLIC_API_URL
│   ├── content.ts                   the string constants from section 8
│   └── config.ts                    brand tokens as JS
├── types/
│   └── api.ts                       types from section 6
├── public/
│   ├── logo.svg
│   └── favicon.ico
├── tailwind.config.ts
├── package.json
├── next.config.js
└── .env.example                     NEXT_PUBLIC_API_URL=
```

---

## 11 · Env vars

| Var | Value | Where used |
|---|---|---|
| `NEXT_PUBLIC_API_URL` | `https://your-backend.railway.app` | All API fetches |

No other env vars needed on the frontend — all third-party keys come from the reviewer at runtime.

---

## 12 · What Claude Design does NOT need to do

- Build the backend (separate FastAPI project).
- Manage database migrations.
- Generate seed answers (backend precomputes them and serves via `/api/seed-prompts`).
- Cache results (browser HTTP cache is fine).
- Handle authentication (there is none by design).

If any endpoint is missing at build time, mock its response with a static JSON file in
`public/mock/` and add a `USE_MOCK=true` env flag that the api client honors.
