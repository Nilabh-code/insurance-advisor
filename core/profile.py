"""Structured risk profile.

The point of this object: the LLM never has to remember facts correctly. Every turn
runs slot-filling against the active schema and merges the result into this dict,
which is the source of truth for pricing, eligibility and comparison.

Kept deliberately plain (dict + dataclasses, stdlib only) so it can be serialised to
JSON, persisted, diffed between turns, and handed to a web layer unchanged.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from . import schemas


# ---------------------------------------------------------------------------
# Coercion - the LLM returns loosely typed JSON, so normalise it here
# ---------------------------------------------------------------------------

_NUM_WORDS = {
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
    "ten": 10, "fifteen": 15, "twenty five": 25, "seventy five": 75,
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
}

_LAKH_UNITS = {"lakh": 100_000, "lakhs": 100_000, "lac": 100_000, "lacs": 100_000}
_CRORE_UNITS = {"crore": 10_000_000, "crores": 10_000_000, "cr": 10_000_000}
_THOUSAND_UNITS = {"thousand": 1_000, "k": 1_000}

# Negations are checked BEFORE the affirmative list, because a plain substring
# test gets "i don't" wrong: "i do" is a substring of "i don't", so a naive scan
# reports True for an unambiguous No.
_NEGATION_PREFIXES = ("i don't", "i do not", "i dont", "i doesnt", "i didn't",
                      "i did not", "no i", "not really", "definitely not")

TRUE_WORDS = {"yes", "yeah", "yep", "true", "correct", "sure", "ok", "okay",
              "affirmative", "positive"}
FALSE_WORDS = {"no", "nah", "nope", "false", "incorrect", "never", "none",
               "nothing", "negative"}

# Affirmative phrases handled separately: whole-word matching makes "i do" safe
# here only because it runs AFTER _NEGATION_PREFIXES has already rejected
# "i don't" (in "don't", `do` is followed by `n`, so \bdo\b never matches).
TRUE_PHRASES = ("i do", "i have", "yes i", "yep i", "sure i", "absolutely", "definitely")

_ORDINAL_YEAR = re.compile(r"\b(19|20)\d{2}\b")
_CCY_TOKEN = re.compile(r"(?:rs\.?|₹|inr)\s*([\d,]+(?:\.\d+)?)", re.I)
_PLAIN_NUMBER = re.compile(r"\b([\d,]+(?:\.\d+)?)\b")
_CC = re.compile(r"(\d{3,4})\s*(?:cc|cm3)")


class SlotExtractionError(ValueError):
    """Raised when a value cannot be coerced into the slot's declared type."""


def coerce_number(value: Any) -> float | None:
    """Best-effort numeric parse from whatever the model returned.

    Handles ints, floats, numeric strings, '25,000', '5 lakh', '1.2 crore',
    '22 years', and spelled-out small numbers.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)

    text = str(value).strip().lower()
    if not text:
        return None

    # Multiplier expressed as a word: '5 lakh', '1 crore', '20 thousand'
    for unit, mult in {**_CRORE_UNITS, **_LAKH_UNITS, **_THOUSAND_UNITS}.items():
        m = re.search(rf"([\d,]+(?:\.\d+)?)\s*{re.escape(unit)}\b", text)
        if m:
            return float(m.group(1).replace(",", "")) * mult

    # Currency symbol / 'Rs.' prefix
    m = _CCY_TOKEN.search(text)
    if m:
        return float(m.group(1).replace(",", ""))

    # Plain number anywhere
    m = _PLAIN_NUMBER.search(text)
    if m:
        return float(m.group(1).replace(",", ""))

    # Spelled-out numbers
    for word, num in _NUM_WORDS.items():
        if re.search(rf"\b{re.escape(word)}\b", text):
            return float(num)

    return None


def coerce_bool(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if not text:
        return None

    # Negations first - see the comment on _NEGATION_PREFIXES.
    for neg in _NEGATION_PREFIXES:
        if neg in text:
            return False

    # Whole-word match, so "yacht" does not read as "yes".
    def has_word(word: str) -> bool:
        return bool(re.search(rf"\b{re.escape(word)}\b", text))

    if any(has_word(w) for w in TRUE_WORDS):
        return True
    if any(p in text for p in TRUE_PHRASES):
        return True
    if any(has_word(w) for w in FALSE_WORDS):
        return False
    if "n't" in text:            # contractions that survived word matching
        return False
    return None


def coerce_list(value: Any) -> list:
    """Normalise a list-ish value. Empty/None/'none' all mean an empty list."""
    if value is None:
        return []
    if isinstance(value, list):
        return [v for v in value if str(v).strip() and str(v).strip().lower() not in ("none", "null", "n/a")]
    text = str(value).strip()
    if not text or text.lower() in ("none", "no", "nothing", "nil", "n/a", "-"):
        return []
    return [p.strip() for p in re.split(r"[,;]| and ", text) if p.strip()]


def coerce_year(value: Any) -> int | None:
    """Pull a 4-digit year out of '2020', 'made in 2020', 'a 2019 model'."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        n = int(value)
        return n if 1950 <= n <= 2030 else None
    m = _ORDINAL_YEAR.search(str(value))
    return int(m.group(0)) if m else None


def coerce_cc(value: Any) -> int | None:
    """Extract cubic capacity: '1199cc', '1.2L', '1.2 litre', '1200'."""
    if value is None or isinstance(value, bool):
        return None
    m = _CC.search(str(value))
    if m:
        return int(m.group(1))
    # '1.2L' / '1.5 litre' -> cubic capacity in cc
    m = re.search(r"([\d.]+)\s*(?:l|litre|liter|l)\b", str(value).lower())
    if m:
        return int(float(m.group(1)) * 1000)
    n = coerce_number(value)
    if n is not None and 100 <= n <= 6000:
        return int(n)
    return None


def coerce_value(slot: schemas.Slot, value: Any) -> Any:
    """Coerce + range-check one value against its slot declaration.

    Returns a coerced value, or None when the input cannot be made to fit. Never
    raises for a bad model response - the caller records a warning instead, because
    dropping a bad slot must not break the turn.
    """
    if value is None:
        return None

    stype = slot.type

    if stype in ("int", "float"):
        n = coerce_number(value)
        if n is None:
            return None
        if stype == "int":
            n = int(round(n))
        if slot.min is not None and n < slot.min:
            return None
        if slot.max is not None and n > slot.max:
            return None
        return n

    if stype == "money":
        return coerce_number(value)

    if stype == "bool":
        return coerce_bool(value)

    if stype == "list":
        return coerce_list(value)

    if stype == "enum":
        text = str(value).strip().lower().replace(" ", "_").replace("-", "_")
        allowed = {v.lower(): v for v in slot.enum_values}
        if text in allowed:
            return allowed[text]
        # fuzzy: 'comprehensive cover' -> comprehensive
        for low, canon in allowed.items():
            if low in text:
                return canon
        return None

    if stype == "year":
        return coerce_year(value)

    if stype == "cc":
        return coerce_cc(value)

    # text, city, date
    text = str(value).strip()
    return text or None


# ---------------------------------------------------------------------------
# The profile
# ---------------------------------------------------------------------------

@dataclass
class SlotFill:
    """One extracted fact, with provenance."""
    slot: str
    value: Any
    source_turn: int
    raw: Any = None            # what the model originally returned, pre-coercion

    def to_dict(self) -> dict:
        return {"slot": self.slot, "value": self.value,
                "turn": self.source_turn, "raw": self.raw}


@dataclass
class Profile:
    """Live structured risk profile. One per conversation."""
    product: str
    slots: dict[str, Any] = field(default_factory=dict)
    history: list[SlotFill] = field(default_factory=list)
    transcript: list[dict] = field(default_factory=list)
    turn: int = 0
    warnings: list[str] = field(default_factory=list)

    # -- access ------------------------------------------------------------
    def get(self, name: str, default: Any = None) -> Any:
        return self.slots.get(name, default)

    def set(self, name: str, value: Any) -> None:
        self.slots[name] = value

    def __contains__(self, name: str) -> bool:
        return name in self.slots

    def filled(self) -> dict[str, Any]:
        return dict(self.slots)

    # -- completeness ------------------------------------------------------
    @property
    def schema(self) -> list[schemas.Slot]:
        return schemas.get_schema(self.product)

    def slot(self, name: str) -> schemas.Slot | None:
        for s in self.schema:
            if s.name == name:
                return s
        return None

    def missing_required(self) -> list[schemas.Slot]:
        return schemas.missing_required(self.product, self.slots)

    def is_complete(self) -> bool:
        return not self.missing_required()

    def completeness(self) -> tuple[int, int]:
        """(answered_required, total_required). An empty list counts as answered."""
        req = [s for s in self.schema if s.required]
        done = [s for s in req if schemas.is_answered(s.name, self.slots)]
        return len(done), len(req)

    def next_question(self) -> str | None:
        """The next slot to ask about, or None when the profile is complete."""
        for s in self.schema:
            if s.required and not schemas.is_answered(s.name, self.slots):
                return s.question
        return None

    # -- merging model output ---------------------------------------------
    def merge_extraction(self, extracted: dict[str, Any], turn: int | None = None) -> dict:
        """Merge an LLM extraction dict into the profile.

        Returns a report: which slots were accepted, which were rejected and why.
        Never raises on bad model output.
        """
        turn = self.turn if turn is None else turn
        accepted, rejected = {}, {}

        for name, raw in (extracted or {}).items():
            if raw is None or (isinstance(raw, str) and not raw.strip()):
                continue

            slot = self.slot(name)
            if slot is None:
                rejected[name] = "not in schema for this product line"
                continue

            value = coerce_value(slot, raw)
            if value is None:
                rejected[name] = f"could not coerce {raw!r} to {slot.type}"
                self.warnings.append(f"slot {name!r}: bad value {raw!r}")
                continue

            previous = self.slots.get(name)
            accepted[name] = value
            self.slots[name] = value
            self.history.append(SlotFill(name, value, turn, raw))
            if previous is not None and previous != value:
                self.warnings.append(f"slot {name!r} changed {previous!r} -> {value!r}")

        self.turn = max(self.turn, turn)
        return {"accepted": accepted, "rejected": rejected}

    # -- display -----------------------------------------------------------
    def summary(self) -> str:
        """Human-readable snapshot of what we know so far."""
        lines = [f"Profile ({schemas.product_label(self.product)}) - "
                 f"{self.completeness()[0]}/{self.completeness()[1]} required fields"]
        for s in self.schema:
            if s.name in self.slots and self.slots[s.name] not in (None, "", [], {}):
                v = self.slots[s.name]
                if isinstance(v, list):
                    v = ", ".join(map(str, v)) if v else "none"
                unit = f" {s.unit}" if s.unit else ""
                lines.append(f"  {s.name}: {v}{unit}")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "product": self.product,
            "turn": self.turn,
            "slots": self.slots,
            "history": [h.to_dict() for h in self.history],
            "transcript": self.transcript,
            "warnings": self.warnings,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Profile":
        p = cls(product=data["product"])
        p.slots = data.get("slots", {})
        p.turn = data.get("turn", 0)
        p.transcript = data.get("transcript", [])
        p.warnings = data.get("warnings", [])
        p.history = [
            SlotFill(h["slot"], h["value"], h.get("turn", 0), h.get("raw"))
            for h in data.get("history", [])
        ]
        return p

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, default=str)