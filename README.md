# Session 5 Assignment — LLM Recommendation Tool

A small tool that takes a problem statement (plus optional constraints) and
recommends the most appropriate Large Language Model to use, with a structured
reasoning trace and a fallback recommendation.

The point of the assignment is the **prompt**, not the model: the prompt
forces step-by-step reasoning, separates reasoning from tool/lookup calls,
self-checks intermediate steps, and emits strict JSON that downstream code can
parse.

---

## Files

| File | What it is |
|------|------------|
| `llm-recommendation-prompt-v1.md` | The canonical system prompt. Organized into 7 numbered sections (inputs → reasoning protocol → tool use → loop → output schema → fallbacks → worked example). |
| `llm_recommender.py` | The runnable tool. Loads the prompt, injects a pre-resolved model catalog, calls either the V2 gateway or Anthropic directly, returns a typed `Recommendation`. |
| `run_gateway.ps1` | Windows PowerShell helper — one-shot launcher for the V2 gateway (creates venv, checks `.env`, starts on port 8100). |
| `assignment-prompt.txt` | The original assignment brief. |

---

## How the prompt maps to the rubric

The prompt is designed against the 9 criteria from the course's
`prompt_example.md` rubric (external to this repo — part of the
*School of AI · Session 5* course materials):

| # | Criterion | Where it's satisfied |
|---|-----------|----------------------|
| 1 | Explicit reasoning instructions | §2 — mandatory Phases A–E surface in `reasoning_trace`. |
| 2 | Structured output format | §5 — strict JSON schema, no prose outside it. |
| 3 | Reasoning vs. tool separation | §3 — `FUNCTION_CALL` lines stand alone, never mixed with reasoning. |
| 4 | Conversation loop support | §4 — `prior_turns` input + `delta_from_previous` output field. |
| 5 | Instructional framing | §7 — full worked few-shot example. |
| 6 | Internal self-checks | Phase D + a required `SELF_CHECK` reasoning-type tag. |
| 7 | Reasoning-type awareness | Every trace step carries a tag from a fixed vocabulary (`CLASSIFICATION`, `LOOKUP`, `ARITHMETIC`, `LOGIC`, `COMPARISON`, `SELF_CHECK`). |
| 8 | Error handling / fallbacks | §6 — clarification, tool failure, no-feasible-model, anti-hallucination rules. |
| 9 | Overall clarity & robustness | Numbered sections, single output schema, explicit "do not guess" rule. |

---

## Architecture

```
        ┌──────────────────────────────────┐
        │  llm_recommender.py              │
        │  ─ Pydantic input/output schemas │
        │  ─ MODEL_CATALOG (15 models)     │
        └──────┬───────────────────┬───────┘
               │                   │
   --backend gateway      --backend anthropic
   (default)              (uses ANTHROPIC_API_KEY)
               │                   │
               ▼                   ▼
   ┌────────────────────┐  ┌─────────────────────┐
   │ llm_gatewayV2      │  │ anthropic SDK       │
   │ localhost:8100     │  │ messages.create     │
   │ response_format=   │  │ + forced tool-use   │
   │  json_schema       │  │ for structured out  │
   └─────────┬──────────┘  └──────────┬──────────┘
             └──────────┬─────────────┘
                        ▼
            parsed → Recommendation (Pydantic)
```

Both backends produce identical `Recommendation` objects — pick by latency,
cost, or what keys you happen to have.

### v1 design choices

- **No `FUNCTION_CALL` loop in v1.** The runtime injects a pre-resolved model
  catalog (`MODEL_CATALOG` in `llm_recommender.py`) as §8 of the system prompt,
  so the model never needs to call `lookup_model_spec` for known models.
  Single LLM call, no parser, full JSON via `response_format`. The prompt
  still documents the `FUNCTION_CALL` protocol so a v2 can wire native
  tool-use through a multi-provider gateway.
- **One flat `Recommendation` schema** covers all three §5/§6 output shapes
  (`ok` / `needs_clarification` / `no_feasible_model`) via optional fields —
  cleaner with strict JSON schema than a discriminated union.
- **Catalog prices are coarse snapshots** as of writing. Refresh periodically;
  treat as v1 placeholders.

---

## Setup

Python 3.13+. Three dependencies — install with pip:

```powershell
pip install anthropic httpx pydantic
```

Now pick one of the two backends below.

### Option A — `--backend anthropic` (simplest, self-contained)

No gateway, no venv, no `.env` file. Just set `ANTHROPIC_API_KEY` in your
shell environment and run the tool.

```powershell
$env:ANTHROPIC_API_KEY = "sk-ant-..."          # or set it permanently via
                                                # Windows → Environment Variables
python llm_recommender.py --backend anthropic "your problem statement"
```

Defaults to `claude-sonnet-4-5`; override with `--model claude-opus-4-7`.

### Option B — `--backend gateway` (multi-provider, requires extra setup)

> Not bundled with this repo. The gateway is a separate FastAPI service from
> the wider *Session 5* course project. The launcher (`run_gateway.ps1`) and
> the recommender's gateway client assume the gateway lives at
> `..\llm_gatewayV2\` relative to this folder — if you want to use this path,
> clone or place that gateway project alongside this repo.

Routes through `llm_gatewayV2` so you can use Groq, Gemini, NVIDIA, GitHub
Models, Cerebras, OpenRouter, or Ollama.

**1. Provider keys.** Create `..\.env` (in the parent folder, next to the
gateway) with at least one provider key — Groq is the easiest (free tier,
fast, native tool-use):

```ini
GROQ_API_KEY=gsk_...
# optional extras:
GEMINI_API_KEY=...
GITHUB_ACCESS_TOKEN=...
OPEN_ROUTER_API_KEY=...
NVIDIA_API_KEY=...
CEREBRAS_API_KEY=...
OLLAMA_MODEL=llama3.2:latest
```

Get a Groq key at https://console.groq.com/keys.

**2. Start the gateway.**

Windows (recommended) — use the bundled launcher:

```powershell
.\run_gateway.ps1                 # creates a Windows venv on first run, starts on :8100
.\run_gateway.ps1 -Port 8200      # override port
.\run_gateway.ps1 -Reinstall      # rebuild the venv from scratch
```

If PowerShell blocks the script with an execution-policy error, run once per
user: `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`.

macOS / Linux — invoke the gateway's own launcher from inside its folder.

Verify in a second shell:

```powershell
curl http://localhost:8100/v1/capabilities
```

---

## Usage

### One-shot

```powershell
python llm_recommender.py --backend anthropic "Summarize 300-page legal contracts on-prem, < 5s/page" --on-prem --min-context 200000

python llm_recommender.py --backend anthropic --max-cost 0.001 "Classify 10M short product reviews per day"

python llm_recommender.py --backend anthropic --modalities text,vision --json "Vision QA on UI screenshots"
```

### Multi-turn refinement

```powershell
python llm_recommender.py --backend anthropic --interactive
```

Each turn is fed back via `prior_turns`, and the model emits a
`delta_from_previous` describing what changed.

### CLI flags

| Flag | Effect |
|------|--------|
| `--backend {gateway,anthropic}` | Where to send the request. Default `gateway`. `anthropic` uses `ANTHROPIC_API_KEY` directly — no gateway needed. |
| `--model STR`             | Backend-specific model id (e.g. `claude-opus-4-7` for anthropic). |
| `--max-cost FLOAT`        | Soft cap on \$ / 1k tokens. |
| `--max-latency-ms INT`    | Soft cap on per-request latency. |
| `--open-source`           | Require open-weights model. |
| `--on-prem`               | Require on-prem deployable model. |
| `--min-context INT`       | Minimum context window in tokens. |
| `--modalities LIST`       | Comma-separated subset of `text,vision,audio,code` (e.g. `text,vision`). |
| `--provider STR`          | Override gateway provider (e.g. `gr`). Default = auto-failover. Ignored if `--backend anthropic`. |
| `--reasoning {off,low,medium,high}` | Reasoning budget on the executor call. Default `medium`. Gateway backend only. |
| `--json`                  | Print raw JSON instead of the formatted view. |
| `--interactive`           | Multi-turn refinement loop. |

---

## Output shape

A successful call returns a `Recommendation`:

```json
{
  "status": "ok",
  "restated_problem": "...",
  "task_family": "long-context document summarization",
  "reasoning_trace": [
    {"step": "A1", "reasoning_type": "CLASSIFICATION", "note": "..."},
    {"step": "B1", "reasoning_type": "LOOKUP",         "note": "..."},
    {"step": "C2", "reasoning_type": "ARITHMETIC",     "note": "..."},
    {"step": "D2", "reasoning_type": "SELF_CHECK",     "note": "..."}
  ],
  "candidates_considered": [
    {"model": "qwen2.5-72b-1m", "kept": true,  "score": 4.5},
    {"model": "llama-3.1-70b",  "kept": false, "eliminated_by": "min_context_tokens"}
  ],
  "recommendation": {
    "primary":  {"model": "qwen2.5-72b-1m", "provider": "self-hosted", "why": "..."},
    "fallback": {"model": "mixtral-8x22b",  "provider": "self-hosted", "why": "..."}
  },
  "assumptions": ["..."],
  "confidence": "medium",
  "delta_from_previous": null
}
```

Two alternative shapes are also valid (see §6 of the prompt):

- `status: "needs_clarification"` + `question`
- `status: "no_feasible_model"` + `violated_constraints` + `closest_alternative`

---

## Extending

- **Add a model**: append a dict to `MODEL_CATALOG` in `llm_recommender.py`.
  Fields: `model`, `provider`, `context_tokens`, `modalities`, `in_usd_per_1k`,
  `out_usd_per_1k`, `latency_tier`, `open_source`, `on_prem`, `strengths`.
- **Enable real lookups**: wire native tool-use through a multi-provider
  gateway — define `lookup_model_spec` and `benchmark_search` as tools, then
  run an agent loop that dispatches calls and feeds results back. The
  prompt's §3 already documents the call shape.
- **Persist sessions**: store `prior_turns` to disk between invocations
  instead of keeping them in memory in `--interactive`.

---

## Origin

This repo is the deliverable for *The School of AI · Session 5 — Planning
and Reasoning with Language Models*. The wider course project (not in this
repo) contains:

- A reference agent (`agent5.py`) with a native tool-use loop — the pattern
  this tool borrows for its `--backend gateway` path.
- `llm_gatewayV2/` — a 7-provider FastAPI gateway (Groq, Gemini, NVIDIA,
  Cerebras, OpenRouter, GitHub Models, Ollama) referenced by Option B above.
- `prompt_example.md` — the 9-criteria rubric used to design the prompt in
  this repo.

This repo is intentionally self-contained around **Option A**
(`--backend anthropic`). Option B is documented for users who clone the
wider course project alongside.
