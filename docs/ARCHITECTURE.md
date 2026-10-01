# Architecture

## Build status

| Layer | Module | State |
|---|---|---|
| Slot schemas (motor/health/life/travel) | `core/schemas.py` | done, 4 product lines |
| Structured profile + slot filling | `core/profile.py` | done, coercion tested |
| LLM client | `core/llm.py` | done, live against the Pi gateway |
| Turn orchestration | `core/advisor.py` | done, live-tested end to end |
| Motor premium engine | `engine/motor.py` + `tables.py` | done, 95 unit tests |
| Health / life / travel engines | — | schemas only, nothing prices them yet |
| Policy wording RAG | — | not started |

Design rules the code actually enforces:

- **No LLM in the number path.** Premiums are arithmetic over published rule data
  in `engine/`, one unit test per rule. The model explains a number it did not
  compute; it never produces one.
- **An empty list is an answer.** "I have no pre-existing conditions" fills the
  slot with `[]`. Counting that as missing would loop the conversation forever
  asking about conditions the customer already ruled out.
- **Estimate, then hand off.** Binding quotes go to the insurer's own flow on the
  insurer's own domain. Never ours.

## The OTP relay: rejected, and here is why

The original pitch was: scrape insurer pricing, and where the price sits behind an OTP
wall, ask the customer for their phone number, relay the OTP, and pull the quote.

Rejected. Three independent reasons, any one of which is disqualifying.

### 1. It asks customers to hand a credential to a third party

The customer gives an OTP that grants access to their identity-linked insurer account.
We then hold a live credential against someone's actual policy. That is not a data point,
that is a key. And the failure modes are bad: a bug in our session handling leaks it, a
compromised deployment leaks it, an employee with prod access can use it. IRDAI-regulated
insurers treat this as account takeover. Asking a user for it converts us from "a tool"
into "the thing that ruined their insurance relationship."

There is also no honest framing where this is fine. Even if it works, it is built on the
customer not understanding what they handed over.

### 2. The premium is not a page, so "scraping" it does not generalise

The OTP wall exists because the price is **per-customer, per-session, computed
server-side**. A premium depends on age, city zone, vehicle make/model/year, NCB slab,
claim history, sum-insured chosen, and add-on selection — resolved into a unique number
at request time on a session tied to that person.

Consequences:

- There is no static URL holding "the price" to scrape. The number exists only inside
  one person's authenticated session.
- A scraper built this way works **once, for one customer**, and breaks for the next.
- It is therefore not a data pipeline. It is a UI robot with a login prompt. It cannot
  scale to two customers, let alone thousands.

So the plan quietly is not "build a product." It is "build a bot that impersonates
customers one at a time." The scrape layer was doing less work than it appeared to.

### 3. Licensing

Insurance comparison and broking in India is a regulated activity under IRDAI. An
intermediary that generates and recommends policies carries obligations. Any entity doing
this commercially needs to deal with that. Pulling quotes by relaying customer OTPs is
the opposite of the posture regulators look for.

## What we build instead

Same user experience, legal data, no credentials.

### Layer 1 — Conversation engine with a structured profile

The chat is not freeform-only. Every turn runs slot-filling against a schema, so we
maintain a **live structured risk profile object** alongside the transcript. Age, city
zone, vehicle details, NCB, health conditions, budget, existing cover.

This is the thing that makes it a product rather than a chatbot with a personality: the
profile is queryable, diffable, saveable, and re-usable across motor and health. It also
means the LLM never has to remember facts correctly — the object holds them.

### Layer 2 — Knowledge base over public documents

Policy wordings, exclusion clauses, rider definitions, waiting-period rules, and IRDAI
public disclosures are **published PDFs**. They are ours to read.

RAG over these is where the real product lives. "Does this plan cover my pre-existing
diabetes?" is answerable from the wording document, and answerable *correctly*. This is
also what kills the top failure mode for insurance chatbots: confident hallucination
about what a policy covers. Grounding in the actual wording is the fix.

### Layer 3 — Deterministic engine (no LLM in the number path)

Premium estimates and eligibility are **rules**, not language:

- **Motor:** IDV from the published depreciation grid, NCB slab lookup, city zone,
  add-on eligibility, third-party compulsory-cover floors.
- **Health:** age-banded premium bands, waiting-period and waiver rules, room-rent
  capping, pre-existing declaration mechanics.

Implemented as plain code with a unit test per rule. The LLM is forbidden from touching
this path — a wrong number here is a wrong number a customer acts on.

### Layer 4 — Quote handoff, not quote scraping

When the user wants binding prices, we hand off: deep-link into the insurer's or a
licensed aggregator's own flow, carrying pre-filled parameters so they do not retype
anything. **The customer completes OTP on the insurer's own domain, in their own browser.**
We never ask for it and never see it.

If they want a side-by-side, they paste the quote text or drop a screenshot back into the
chat, and we parse, normalise and compare it. Slightly more friction than "we handle it
for you" — and it is the difference between a product and a liability.

### Layer 5 — Comparison and recommendation

Deterministic scoring produces the ranking. The LLM explains the tradeoff in plain
language: what this plan is good at, what it excludes, where a cheaper plan would leave
them exposed. Show the exclusions as prominently as the price. Most people buy the wrong
cover because nobody showed them the exclusions.

## Monetisation

Comparison and broking businesses make money on **commission after a sale**, not on
selling quotes. That means the hand-off layer is not a fallback — it is the business
model. An insurer/aggregator referral or partner programme pays on policies originated.
No scraping required; the value is in matching the customer to the right product.

## Open questions

- Motor-first or health-first? Motor rules are far more structured and have no medical
  underwriting, so it is the smaller surface for a first release.
- Local model or API for the conversation layer.
- Whether to chase licensed aggregator partner APIs before building more of our own rules.