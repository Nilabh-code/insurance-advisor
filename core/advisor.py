"""Conversation orchestrator.

Ties the three layers together:

    user message
      -> LLM extracts slots (JSON)          [core/llm.py]
      -> profile merges + coerces them     [core/profile.py]
      -> if complete, deterministic engine [engine/motor.py]
      -> LLM explains the result in plain English

The ordering matters: the engine runs BEFORE the explanation, and the explanation
is given the computed numbers as facts. The model narrates a calculation it did
not perform - it never invents a premium.
"""

from __future__ import annotations

import json

import config
from core import schemas
from core.llm import LLMClient, LLMError
from core.profile import Profile
from engine import motor


EXTRACTION_SYSTEM = """You extract structured insurance data from what a customer says.

Return ONLY a JSON object. No prose, no markdown fence, no explanation.

Schema for this product line:
{schema}

Already known (do not re-report these unless the customer is correcting them):
{known}

Rules:
- Extract EVERY fact present in the message, not just one.
- Only use slot names from the schema above. Never invent one.
- A list slot answered with "none" / "no" / "nothing" must be an empty list [].
- Do not guess. Omit a slot entirely rather than inventing a plausible value.
- Keep the customer's own words for list entries and text slots.
- Currency: convert "5 lakh" to 500000, "1.5 crore" to 15000000.
- Booleans must be true/false, not strings.

Example response format:
{{"age": 34, "city": "Pune", "vehicle_year": 2021, "add_ons": ["zero dep", "road assist"]}}
"""

CHAT_SYSTEM = """You are an insurance advisor for the Indian market.

You are warm, plain-spoken, and concise. You talk like a good broker, not a
policy document.

How to behave:
- Ask ONE question at a time. Never dump a checklist.
- Never invent a price, premium, or coverage detail. If a number has not been
  computed, say it will be worked out once you have what you need.
- When you have a computed quote, explain it. Lead with what it means for the
  customer, not with the arithmetic.
- Be honest about tradeoffs. If a cheaper option leaves them exposed, say so.
- If the customer says "I don't know" to something, tell them how to find out or
  make a safe assumption out loud.
- Indian context: rupees, lakh/crore, Zone A/B/C, NCB, IDV, third-party vs
  comprehensive.

Current profile state:
{state}

Already answered (do not ask again):
{known}

Ask the next most useful question, or respond to what they just said. Keep it to
2-4 sentences.
"""

EXPLAIN_SYSTEM = """You are explaining a computed insurance quote to a customer.

You are given the calculation. Treat every number as ground truth - restate it,
do not recompute it, round it, or estimate around it.

Structure your reply as:
1. The bottom line: what this costs and whether it is good.
2. What drives the price (2-3 bullets, concrete).
3. What to watch out for - exclusions, gaps, things that would make a different
   plan better. Be specific and honest; do not sell.
4. One clear next step.

Plain English, no jargon without explaining it, no bullet-point spam. 150-250 words.

COMPUTED QUOTE:
{quote}

CUSTOMER PROFILE:
{state}
"""


class Advisor:
    """One conversation. Holds the profile and drives the turn loop."""

    def __init__(self, product: str = "motor", client: LLMClient | None = None):
        self.product = product
        self.profile = Profile(product=product)
        self.client = client or LLMClient()
        self.last_quote: motor.MotorQuote | None = None

    # -- helpers -----------------------------------------------------------
    def _schema_block(self) -> str:
        return json.dumps([s.to_prompt_dict() for s in schemas.get_schema(self.product)],
                          indent=1)

    def _known_block(self) -> str:
        if not self.profile.slots:
            return "(nothing yet)"
        return json.dumps(self.profile.slots, indent=1, default=str)

    def _state_block(self) -> str:
        done, total = self.profile.completeness()
        return f"{done}/{total} required facts gathered.\n{self._known_block()}"

    # -- the turn ----------------------------------------------------------
    def handle(self, user_message: str) -> dict:
        """Process one user message. Returns a structured result for the UI."""
        self.profile.turn += 1
        turn = self.profile.turn
        self.profile.transcript.append({"role": "user", "content": user_message})

        out: dict = {"turn": turn, "extracted": {}, "rejected": {},
                     "quote": None, "reply": "", "warnings": list(self.profile.warnings)}

        # 1. Extract slots.
        try:
            extracted = self.client.chat_json([
                {"role": "system", "content": EXTRACTION_SYSTEM.format(
                    schema=self._schema_block(), known=self._known_block())},
                {"role": "user", "content": user_message},
            ], temperature=0.0)
            report = self.profile.merge_extraction(extracted, turn=turn)
            out["extracted"] = report["accepted"]
            out["rejected"] = report["rejected"]
        except LLMError as e:
            # Never drop the customer's facts silently. If extraction failed but we
            # already hold a complete profile, we can still recompute and explain -
            # and the user must be told extraction was skipped, not left guessing.
            out["extraction_error"] = str(e)
            out["reply_suffix"] = (
                "\n\n(I could not read the last message just now, so I have not "
                "updated your details from it. If that message changed anything, "
                "tell me again and I will work it in.)"
            )

        # 2. Derive anything the schema implies (e.g. cubic capacity from variant).
        self._derive()

        # 3. Compute, if we can.
        engine_name = schemas.engine_for(self.product)
        if engine_name == "motor":
            try:
                self.last_quote = motor.quote_motor(self.profile)
                out["quote"] = self.last_quote.to_dict()
            except motor.MissingDataError as e:
                out["missing"] = e.missing

        # 4. Explain.
        out["reply"] = self._explain(user_message, out)
        suffix = out.pop("reply_suffix", "")
        out["reply"] = out["reply"] + suffix
        self.profile.transcript.append({"role": "assistant", "content": out["reply"]})
        out["complete"] = self.profile.is_complete()
        out["completeness"] = self.profile.completeness()
        return out

    def _derive(self) -> None:
        """Fill derived slots the model may not have set explicitly.

        Cubic capacity is the important one: customers say "1.2L petrol" or
        "Vxi variant", not "1198cc". Deriving it locally keeps the model out of
        the number path.
        """
        from core.profile import coerce_cc
        if self.profile.get("vehicle_variant_cc_value") is None:
            for key in ("vehicle_variant_cc", "vehicle_make", "fuel_type"):
                val = self.profile.get(key)
                if val:
                    cc = coerce_cc(val)
                    if cc:
                        self.profile.set("vehicle_variant_cc_value", cc)
                        return

    def _explain(self, user_message: str, out: dict) -> str:
        """Plain-English reply. Falls back to a direct question if the LLM fails."""
        next_q = self.profile.next_question()

        if out.get("quote"):
            try:
                return self.client.chat([
                    {"role": "system", "content": EXPLAIN_SYSTEM.format(
                        quote=motor.format_quote(self.last_quote),
                        state=self._state_block())},
                    {"role": "user", "content": user_message},
                ], temperature=0.3)
            except LLMError as e:
                return (motor.format_quote(self.last_quote)
                        + f"\n\n(explanation unavailable: {e})")

        try:
            return self.client.chat([
                {"role": "system", "content": CHAT_SYSTEM.format(
                    state=self._state_block(), known=self._known_block())},
                {"role": "user", "content": user_message},
            ], temperature=0.4)
        except LLMError as e:
            if next_q:
                return f"{next_q}\n\n(slot extraction offline: {e})"
            return f"I couldn't reach the model just now ({e}). " \
                   f"Your details so far are saved."


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    print("Insurance Advisor - conversational demo")
    print(f"Endpoint: {config.BASE_URL}")
    print(f"Model:    {config.MODEL}")
    print("Type 'product' to switch, 'profile' to inspect, 'quit' to exit.\n")

    product = "motor"
    advisor = Advisor(product=product)
    print(f"Starting a {schemas.product_label(product)} conversation.\n")

    while True:
        try:
            msg = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not msg:
            continue
        low = msg.lower()

        if low in ("quit", "exit", "q"):
            break
        if low == "profile":
            print()
            print(advisor.profile.summary())
            continue
        if low == "product":
            try:
                product = input("product (motor/health/life/travel)> ").strip().lower()
                schemas.get_schema(product)
                advisor = Advisor(product=product)
                print(f"Switched to {schemas.product_label(product)}.\n")
            except (KeyError, EOFError):
                print("Unknown product line.\n")
            continue

        result = advisor.handle(msg)
        print()
        print(result["reply"])
        if result.get("extracted"):
            print("\n[extracted] " + ", ".join(
                f"{k}={v!r}" for k, v in result["extracted"].items()))
        if result.get("rejected"):
            print("[rejected] " + ", ".join(
                f"{k}: {v}" for k, v in result["rejected"].items()))
        done, total = result["completeness"]
        print(f"[profile] {done}/{total} required facts")
        print()

    print("Bye.")


if __name__ == "__main__":
    main()