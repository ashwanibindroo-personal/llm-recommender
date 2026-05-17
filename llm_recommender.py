"""
llm_recommender.py — Session 5 assignment tool.

Reads `llm-recommendation-prompt-v1.md` as the system prompt, accepts a
problem statement (+ optional constraints), and returns a structured
recommendation using the same llm_gatewayV2 client agent5.py uses.

Run:
    python llm_recommender.py "Summarize 300-page legal contracts on-prem"
    python llm_recommender.py --problem "..." --on-prem --min-context 200000
    python llm_recommender.py --interactive

Backends:
    --backend gateway   (default) → llm_gatewayV2 on http://localhost:8100
                                    (start with ../llm_gatewayV2/run.sh or
                                    run_gateway.ps1; same as agent5.py)
    --backend anthropic            → Anthropic SDK direct, reads ANTHROPIC_API_KEY
                                    from env; defaults to claude-sonnet-4-6
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

# Reuse the V2 client — same path trick agent5.py uses.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "llm_gatewayV2"))
from client import LLM  # noqa: E402


# ────────────────────────────────────────────────────────────────────────────
# Pydantic schemas — mirror the prompt's §5 output contract
# ────────────────────────────────────────────────────────────────────────────

ReasoningType = Literal[
    "CLASSIFICATION", "LOOKUP", "ARITHMETIC", "LOGIC", "COMPARISON", "SELF_CHECK"
]


class ReasoningStep(BaseModel):
    step: str
    reasoning_type: ReasoningType
    note: str


class Candidate(BaseModel):
    model: str
    kept: bool
    score: Optional[float] = None
    eliminated_by: Optional[str] = None
    notes: Optional[str] = None


class Pick(BaseModel):
    model: str
    provider: str
    why: str


class RecommendationBlock(BaseModel):
    primary: Pick
    fallback: Pick


class Recommendation(BaseModel):
    """Single flat schema covering all three §5/§6 output shapes."""
    status: Literal["ok", "needs_clarification", "no_feasible_model"] = "ok"

    # ok branch
    restated_problem: Optional[str] = None
    task_family: Optional[str] = None
    reasoning_trace: Optional[list[ReasoningStep]] = None
    candidates_considered: Optional[list[Candidate]] = None
    recommendation: Optional[RecommendationBlock] = None
    assumptions: Optional[list[str]] = None
    confidence: Optional[Literal["high", "medium", "low"]] = None
    delta_from_previous: Optional[str] = None

    # needs_clarification branch
    question: Optional[str] = None

    # no_feasible_model branch
    violated_constraints: Optional[list[str]] = None
    closest_alternative: Optional[Pick] = None


class Constraints(BaseModel):
    max_cost_per_1k_tokens_usd: Optional[float] = None
    max_latency_ms: Optional[int] = None
    must_be_open_source: Optional[bool] = None
    must_run_on_prem: Optional[bool] = None
    required_modalities: Optional[list[Literal["text", "vision", "audio", "code"]]] = None
    min_context_tokens: Optional[int] = None


class RecommenderInput(BaseModel):
    problem_statement: str
    constraints: Constraints = Field(default_factory=Constraints)
    prior_turns: list[dict[str, Any]] = Field(default_factory=list)


# ────────────────────────────────────────────────────────────────────────────
# Pre-resolved model catalog (v1: injected into system context so the LLM
# does NOT need to emit FUNCTION_CALL: lookup_model_spec for these models).
# Prices/context windows are coarse snapshots — refresh periodically.
# ────────────────────────────────────────────────────────────────────────────

MODEL_CATALOG: list[dict[str, Any]] = [
    # Anthropic
    {"model": "claude-opus-4-7",      "provider": "anthropic",   "context_tokens": 1_000_000, "modalities": ["text","vision","code"], "in_usd_per_1k": 0.015, "out_usd_per_1k": 0.075, "latency_tier": "medium",  "open_source": False, "on_prem": False, "strengths": ["frontier reasoning","agentic","long context"]},
    {"model": "claude-sonnet-4-6",    "provider": "anthropic",   "context_tokens": 200_000,   "modalities": ["text","vision","code"], "in_usd_per_1k": 0.003, "out_usd_per_1k": 0.015, "latency_tier": "medium",  "open_source": False, "on_prem": False, "strengths": ["balanced","coding","tool use"]},
    {"model": "claude-haiku-4-5",     "provider": "anthropic",   "context_tokens": 200_000,   "modalities": ["text","vision","code"], "in_usd_per_1k": 0.001, "out_usd_per_1k": 0.005, "latency_tier": "fast",    "open_source": False, "on_prem": False, "strengths": ["cheap","fast","high-volume"]},
    # OpenAI
    {"model": "gpt-4o",               "provider": "openai",      "context_tokens": 128_000,   "modalities": ["text","vision","audio","code"], "in_usd_per_1k": 0.0025, "out_usd_per_1k": 0.010, "latency_tier": "medium", "open_source": False, "on_prem": False, "strengths": ["multimodal","realtime audio","general"]},
    {"model": "gpt-4o-mini",          "provider": "openai",      "context_tokens": 128_000,   "modalities": ["text","vision","code"], "in_usd_per_1k": 0.00015, "out_usd_per_1k": 0.0006, "latency_tier": "fast", "open_source": False, "on_prem": False, "strengths": ["very cheap","fast","good enough"]},
    {"model": "o3",                   "provider": "openai",      "context_tokens": 200_000,   "modalities": ["text","code"],          "in_usd_per_1k": 0.010,  "out_usd_per_1k": 0.040, "latency_tier": "slow", "open_source": False, "on_prem": False, "strengths": ["deep reasoning","math","planning"]},
    # Google
    {"model": "gemini-2.5-pro",       "provider": "google",      "context_tokens": 2_000_000, "modalities": ["text","vision","audio","code"], "in_usd_per_1k": 0.00125, "out_usd_per_1k": 0.010, "latency_tier": "medium", "open_source": False, "on_prem": False, "strengths": ["2M context","multimodal","long video"]},
    {"model": "gemini-2.5-flash",     "provider": "google",      "context_tokens": 1_000_000, "modalities": ["text","vision","audio","code"], "in_usd_per_1k": 0.00015, "out_usd_per_1k": 0.0006, "latency_tier": "fast", "open_source": False, "on_prem": False, "strengths": ["cheap long context","fast"]},
    # Open-weights (on-prem capable)
    {"model": "llama-3.3-70b",        "provider": "meta",        "context_tokens": 128_000,   "modalities": ["text","code"],          "in_usd_per_1k": 0.0006,  "out_usd_per_1k": 0.0009, "latency_tier": "medium", "open_source": True, "on_prem": True, "strengths": ["strong open model","tool use"]},
    {"model": "llama-3.1-405b",       "provider": "meta",        "context_tokens": 128_000,   "modalities": ["text","code"],          "in_usd_per_1k": 0.0035,  "out_usd_per_1k": 0.0035, "latency_tier": "slow",   "open_source": True, "on_prem": True, "strengths": ["frontier open","heavy reasoning"]},
    {"model": "qwen2.5-72b-1m",       "provider": "alibaba",     "context_tokens": 1_000_000, "modalities": ["text","code"],          "in_usd_per_1k": 0.0009,  "out_usd_per_1k": 0.0009, "latency_tier": "medium", "open_source": True, "on_prem": True, "strengths": ["1M context open","multilingual"]},
    {"model": "mixtral-8x22b",        "provider": "mistral",     "context_tokens": 64_000,    "modalities": ["text","code"],          "in_usd_per_1k": 0.002,   "out_usd_per_1k": 0.006,  "latency_tier": "fast",   "open_source": True, "on_prem": True, "strengths": ["MoE fast inference","open"]},
    {"model": "mistral-large-2",      "provider": "mistral",     "context_tokens": 128_000,   "modalities": ["text","code"],          "in_usd_per_1k": 0.002,   "out_usd_per_1k": 0.006,  "latency_tier": "medium", "open_source": True, "on_prem": True, "strengths": ["European","strong reasoning"]},
    {"model": "deepseek-v3",          "provider": "deepseek",    "context_tokens": 128_000,   "modalities": ["text","code"],          "in_usd_per_1k": 0.00014, "out_usd_per_1k": 0.00028, "latency_tier": "medium", "open_source": True, "on_prem": True, "strengths": ["very cheap","strong coding"]},
    {"model": "command-r-plus",       "provider": "cohere",      "context_tokens": 128_000,   "modalities": ["text","code"],          "in_usd_per_1k": 0.003,   "out_usd_per_1k": 0.015,  "latency_tier": "medium", "open_source": True, "on_prem": True, "strengths": ["RAG-tuned","tool use"]},
]


# ────────────────────────────────────────────────────────────────────────────
# Prompt + system context assembly
# ────────────────────────────────────────────────────────────────────────────

PROMPT_PATH = Path(__file__).with_name("llm-recommendation-prompt-v1.md")


def load_system_prompt() -> str:
    base = PROMPT_PATH.read_text(encoding="utf-8")
    catalog_block = (
        "\n\n---\n\n## 8. PRE-RESOLVED MODEL CATALOG (runtime-injected)\n\n"
        "The following catalog has been pre-resolved by the runtime. You do "
        "NOT need to call `lookup_model_spec` for any model listed here — its "
        "context window, modalities, pricing, latency tier, and "
        "open-source / on-prem capability are authoritative for this turn.\n\n"
        "```json\n" + json.dumps(MODEL_CATALOG, indent=2) + "\n```\n"
    )
    return base + catalog_block


# ────────────────────────────────────────────────────────────────────────────
# The tool
# ────────────────────────────────────────────────────────────────────────────

def recommend(
    payload: RecommenderInput,
    *,
    backend: str = "gateway",
    provider: Optional[str] = None,
    model: Optional[str] = None,
    reasoning: str = "medium",
) -> Recommendation:
    """Single-call recommendation. `backend` ∈ {'gateway','anthropic'}."""
    if backend == "anthropic":
        return _recommend_anthropic(payload, model=model or "claude-sonnet-4-5")
    return _recommend_gateway(payload, provider=provider, reasoning=reasoning)


def _recommend_gateway(
    payload: RecommenderInput,
    *,
    provider: Optional[str] = None,
    reasoning: str = "medium",
) -> Recommendation:
    """Recommendation via llm_gatewayV2 structured output."""
    llm = LLM()
    system = load_system_prompt()
    user_message = payload.model_dump(exclude_none=False)
    schema = Recommendation.model_json_schema()

    reply = llm.chat(
        prompt=json.dumps(user_message, indent=2),
        system=system,
        cache_system=True,
        response_format={
            "type": "json_schema",
            "schema": schema,
            "name": "Recommendation",
            "strict": True,
        },
        reasoning=reasoning,
        provider=provider,
        temperature=0,
        max_tokens=2048,
    )

    if reply.get("parsed"):
        return Recommendation.model_validate(reply["parsed"])

    text = (reply.get("text") or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:].lstrip()
    return Recommendation.model_validate(json.loads(text))


def _recommend_anthropic(
    payload: RecommenderInput,
    *,
    model: str = "claude-sonnet-4-5",
) -> Recommendation:
    """Recommendation via Anthropic SDK direct. Uses forced tool-use as the
    structured-output mechanism: we expose a single tool whose input_schema
    is the Recommendation JSON schema, and force the model to call it. The
    tool's `input` IS the validated structured output.
    """
    import anthropic

    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from env
    system = load_system_prompt()
    user_message = payload.model_dump(exclude_none=False)

    response = client.messages.create(
        model=model,
        max_tokens=4096,
        system=system,
        messages=[{"role": "user", "content": json.dumps(user_message, indent=2)}],
        tools=[{
            "name": "emit_recommendation",
            "description": "Emit the final LLM recommendation as structured data.",
            "input_schema": Recommendation.model_json_schema(),
        }],
        tool_choice={"type": "tool", "name": "emit_recommendation"},
    )

    for block in response.content:
        if getattr(block, "type", None) == "tool_use":
            return Recommendation.model_validate(block.input)

    raise RuntimeError(
        f"Anthropic backend returned no tool_use block. "
        f"stop_reason={response.stop_reason!r}, content={response.content!r}"
    )


# ────────────────────────────────────────────────────────────────────────────
# CLI
# ────────────────────────────────────────────────────────────────────────────

def _parse_modalities(s: str) -> list[str]:
    """argparse type for --modalities. Comma-separated, validated against the
    fixed set; using a custom type (not nargs='+') avoids greedy-consumption
    of the positional problem statement."""
    valid = {"text", "vision", "audio", "code"}
    items = [m.strip() for m in s.split(",") if m.strip()]
    bad = [m for m in items if m not in valid]
    if bad:
        raise argparse.ArgumentTypeError(
            f"invalid modalities {bad}; choose from {sorted(valid)}"
        )
    return items


def _build_input_from_args(args: argparse.Namespace) -> RecommenderInput:
    return RecommenderInput(
        problem_statement=args.problem,
        constraints=Constraints(
            max_cost_per_1k_tokens_usd=args.max_cost,
            max_latency_ms=args.max_latency_ms,
            must_be_open_source=args.open_source,
            must_run_on_prem=args.on_prem,
            required_modalities=args.modalities,
            min_context_tokens=args.min_context,
        ),
    )


def _print_recommendation(rec: Recommendation) -> None:
    print("\n" + "═" * 78)
    print("LLM RECOMMENDATION")
    print("═" * 78)

    if rec.status == "needs_clarification":
        print(f"  STATUS    : needs_clarification")
        print(f"  QUESTION  : {rec.question}")
        return

    if rec.status == "no_feasible_model":
        print(f"  STATUS    : no_feasible_model")
        print(f"  VIOLATED  : {rec.violated_constraints}")
        if rec.closest_alternative:
            ca = rec.closest_alternative
            print(f"  CLOSEST   : {ca.model} ({ca.provider}) — {ca.why}")
        return

    print(f"  STATUS       : ok")
    print(f"  TASK FAMILY  : {rec.task_family}")
    print(f"  RESTATED     : {rec.restated_problem}")
    print(f"  CONFIDENCE   : {rec.confidence}")
    if rec.delta_from_previous:
        print(f"  DELTA        : {rec.delta_from_previous}")

    if rec.recommendation:
        p, f = rec.recommendation.primary, rec.recommendation.fallback
        print(f"\n  PRIMARY      : {p.model}  ({p.provider})")
        print(f"                 {p.why}")
        print(f"  FALLBACK     : {f.model}  ({f.provider})")
        print(f"                 {f.why}")

    if rec.candidates_considered:
        print("\n  CANDIDATES   :")
        for c in rec.candidates_considered:
            mark = "✓" if c.kept else "✗"
            extra = f"score={c.score}" if c.kept else f"dropped: {c.eliminated_by}"
            print(f"    {mark} {c.model:<22} {extra}")

    if rec.reasoning_trace:
        print("\n  REASONING TRACE:")
        for s in rec.reasoning_trace:
            print(f"    [{s.step}] {s.reasoning_type:<14} {s.note}")

    if rec.assumptions:
        print("\n  ASSUMPTIONS  :")
        for a in rec.assumptions:
            print(f"    - {a}")

    print("\n" + "═" * 78)


def main() -> None:
    p = argparse.ArgumentParser(description="LLM Recommendation tool (Session 5).")
    p.add_argument("problem", nargs="?", help="Problem statement (positional).")
    p.add_argument("--problem", dest="problem_kw", help="Problem statement (named).")
    p.add_argument("--max-cost", type=float, default=None,
                   help="Max $/1k tokens (combined in+out, soft).")
    p.add_argument("--max-latency-ms", type=int, default=None)
    p.add_argument("--open-source", action="store_true", default=None,
                   help="Require open-weights model.")
    p.add_argument("--on-prem", action="store_true", default=None,
                   help="Require on-prem deployable model.")
    p.add_argument("--min-context", type=int, default=None,
                   help="Minimum context window in tokens.")
    p.add_argument("--modalities", type=_parse_modalities, default=None,
                   metavar="LIST",
                   help="Comma-separated subset of {text,vision,audio,code}, e.g. 'text,vision'.")
    p.add_argument("--backend", default="gateway",
                   choices=["gateway", "anthropic"],
                   help="Where to send the request. 'anthropic' uses ANTHROPIC_API_KEY directly.")
    p.add_argument("--model", default=None,
                   help="Backend-specific model id (e.g. claude-sonnet-4-5 for anthropic).")
    p.add_argument("--provider", default=None,
                   help="Gateway provider override (e.g. 'gr'). None = auto. Ignored if --backend anthropic.")
    p.add_argument("--reasoning", default="medium",
                   choices=["off", "low", "medium", "high"])
    p.add_argument("--json", action="store_true",
                   help="Print raw JSON instead of the formatted view.")
    p.add_argument("--interactive", action="store_true",
                   help="Multi-turn refinement loop (prior_turns passed back in).")
    args = p.parse_args()

    args.problem = args.problem or args.problem_kw
    if not args.interactive and not args.problem:
        p.error("Provide a problem statement, or use --interactive.")

    if args.interactive:
        prior: list[dict[str, Any]] = []
        while True:
            try:
                statement = input("\nProblem (blank to quit) > ").strip()
            except EOFError:
                break
            if not statement:
                break
            payload = RecommenderInput(problem_statement=statement, prior_turns=prior)
            rec = recommend(payload, backend=args.backend, provider=args.provider,
                            model=args.model, reasoning=args.reasoning)
            if args.json:
                print(json.dumps(rec.model_dump(exclude_none=True), indent=2))
            else:
                _print_recommendation(rec)
            prior.append({"user": statement, "assistant": rec.model_dump(exclude_none=True)})
        return

    payload = _build_input_from_args(args)
    rec = recommend(payload, backend=args.backend, provider=args.provider,
                    model=args.model, reasoning=args.reasoning)
    if args.json:
        print(json.dumps(rec.model_dump(exclude_none=True), indent=2))
    else:
        _print_recommendation(rec)


if __name__ == "__main__":
    main()
