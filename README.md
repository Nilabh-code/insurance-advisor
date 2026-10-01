# Insurance Conversational Advisor

Conversational insurance advisor for the Indian market. User describes themselves
in plain language (age, city, vehicle, health history, budget), the system builds a
structured risk profile, then surfaces plan options with real numbers and a plain-English
explanation of what each plan actually covers — and what it quietly doesn't.

## Status

Engine scaffolded. Slot-filling profile schema + deterministic motor premium engine with
unit tests.

See `docs/ARCHITECTURE.md` for the core design decision (and why we are **not**
building the OTP relay).

## Layout

```
insurance/
  docs/ARCHITECTURE.md   core design + the OTP-relay decision
  config.py              LLM endpoint config (reads env, no secrets in repo)
  core/
    profile.py           structured risk profile object + slot filling
    schemas.py           pluggable per-product slot schemas
    llm.py               OpenAI-compatible chat client
  engine/
    motor.py             deterministic motor premium engine
    tables.py            public reference data (depreciation, NCB)
  tests/
    test_motor.py        premium engine unit tests
    test_profile.py      slot-filling unit tests
  data/                  reference data (JSON/CSV)
  prompts/               system prompts
```

## Running

```bash
export NINEROUTER_API_KEY=sk-...
export LLM_BASE_URL=https://rdd784u.abc-tunnel.us/v1
export LLM_MODEL=Unsloth/HauhauCS/Qwen3.6-35B-A3B-Uncensored-HauhauCS-Aggressive

python -m pytest tests/ -q
```

## The short version

The product value is **decision support**, not price scraping. A chatbot that decodes
exclusions, computes IDV correctly, and tells you *which cover to drop* is worth far
more to a customer than one that returns a price table it scraped illegally.

Pricing is computed from **public rule data** (depreciation grids, NCB slabs, IRDAI
public disclosures, policy wordings) — all of which are legal to use. Binding quotes
are handed off to the insurer's own flow on the insurer's own domain, where the
customer completes OTP themselves.