"""Pluggable per-product slot schemas.

The product is "every type of insurance", so the slot system cannot hardcode car
fields. Each product line contributes its own schema; the profile is the union of
whatever schemas are active for the conversation.

A Slot declares:
  name        the key it fills in the profile dict
  type        one of: int, float, money, text, bool, enum, date, list, city, vehicle
  required    whether the recommendation engine can run without it
  question    plain-English ask, shown to the LLM and reused verbatim in the UI
  hint        extra guidance for the extractor (accepted spellings, ranges, units)
  enum_values closed vocabulary, when type == "enum"
  min/max      numeric bounds, enforced after extraction
  unit        rupees, percent, years - used for display and sanity checks
"""

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Slot:
    name: str
    type: str
    required: bool = False
    question: str = ""
    hint: str = ""
    enum_values: tuple = ()
    min: float | None = None
    max: float | None = None
    unit: str = ""

    def to_prompt_dict(self) -> dict[str, Any]:
        """Compact form handed to the LLM so it knows what to extract."""
        d: dict[str, Any] = {"name": self.name, "type": self.type}
        if self.required:
            d["required"] = True
        if self.enum_values:
            d["values"] = list(self.enum_values)
        if self.min is not None or self.max is not None:
            d["range"] = [self.min, self.max]
        if self.unit:
            d["unit"] = self.unit
        if self.hint:
            d["hint"] = self.hint
        return d


# ---------------------------------------------------------------------------
# Shared slots - apply to any product line
# ---------------------------------------------------------------------------

PERSON_SLOTS = [
    Slot("age", "int", required=True, min=18, max=100, unit="years",
         question="How old are you?",
         hint="Age of the person being insured / the main driver."),
    Slot("city", "city", required=True,
         question="Which city do you live in?",
         hint="Indian city name. Determines the Zone A / B / C rating."),
    Slot("budget", "money", unit="rupees",
         question="What's a comfortable annual premium range for you?",
         hint="Annual budget in rupees. Optional - do not invent one."),
]


# ---------------------------------------------------------------------------
# Motor
# ---------------------------------------------------------------------------

MOTOR_SLOTS = [
    Slot("vehicle_make", "text",
         question="What make and model is the vehicle?"),
    Slot("vehicle_year", "int", required=True, min=1950, max=2030, unit="year",
         question="What year was the vehicle manufactured?",
         hint="Registration year counts as manufacturing year for IDV in India."),
    Slot("vehicle_variant_cc", "text",
         question="Which variant / engine capacity is it?",
         hint="e.g. 1.2L petrol, 1.5L diesel, 1199cc. Used for cubic-capacity bands."),
    Slot("fuel_type", "enum", enum_values=("petrol", "diesel", "cng", "electric", "hybrid"),
         question="What fuel does it run on?"),
    Slot("ncb", "int", min=0, max=100, unit="percent",
         question="What's your current No Claim Bonus percentage?",
         hint="0 / 20 / 25 / 35 / 45 / 50. Say 0 if you have never claimed or are unsure."),
    Slot("claims_history", "list",
         question="Have you made any claims in the last 3 years?",
         hint="List of claim descriptions, or empty list if none."),
    Slot("policy_type", "enum", enum_values=("third_party", "comprehensive", "third_party_only_plus"),
         question="Do you want third-party only or comprehensive cover?"),
    Slot("add_ons", "list",
         question="Any add-ons you're interested in?",
         hint="e.g. zero dep, engine protect, road assist, consumables, key loss, RTI."),
    Slot("bought_used", "bool",
         question="Is this a used vehicle purchase?"),
    Slot("financed", "bool",
         question="Is the vehicle financed / on loan?"),
]


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

HEALTH_SLOTS = [
    Slot("health_age", "int", min=0, max=100, unit="years",
         question="What is the age of the person being insured?"),
    Slot("sum_insured", "money", unit="rupees",
         question="What sum insured are you thinking?",
         hint="Common choices: 3L / 5L / 10L / 25L / 50L."),
    Slot("preexisting", "list", required=True,
         question="Any existing conditions - BP, diabetes, thyroid, asthma, nothing at all?",
         hint="List of conditions verbatim. Empty list is a valid and common answer."),
    Slot("medication", "list",
         question="Any regular medication?"),
    Slot("smoker", "bool",
         question="Do you smoke?"),
    Slot("alcohol", "bool",
         question="Do you drink alcohol?"),
    Slot("occupation", "enum", required=True,
         enum_values=("office", "business", "professional", "manual", "hazardous"),
         question="What does the person do for a living?"),
    Slot("weight_kg", "float", min=10, max=300, unit="kg",
         question="What's the current weight?"),
    Slot("height_cm", "float", min=30, max=250, unit="cm",
         question="Height?"),
    Slot("waiver_of_conditions", "bool",
         question="Do you want a waiver of conditions rider?"),
]


# ---------------------------------------------------------------------------
# Life
# ---------------------------------------------------------------------------

LIFE_SLOTS = [
    Slot("life_age", "int", required=True, min=18, max=75, unit="years",
         question="What is your age?"),
    Slot("life_sum_assured", "money", required=True, unit="rupees",
         question="What sum assured are you looking for?"),
    Slot("occupation_class", "enum", enum_values=("class_1", "class_2", "class_3", "class_4"),
         question="What does the person do for a living?"),
    Slot("smoker_life", "bool",
         question="Do you smoke?"),
    Slot("tobacco", "enum", enum_values=("none", "cigarettes", "cigars", "smokeless", "betel"),
         question="Any tobacco or nicotine use?"),
    Slot("nominee", "int", min=0, max=100, unit="people",
         question="How many people depend on this income?"),
    Slot("existing_cover", "money", unit="rupees",
         question="Any existing life cover or group cover from your employer?"),
    Slot("term_vs_investment", "enum", required=True,
         enum_values=("pure_term", "endowment", "ulip", "pension", "whole_life"),
         question="Pure protection cover, or do you want an investment-linked product?"),
]


# ---------------------------------------------------------------------------
# Travel
# ---------------------------------------------------------------------------

TRAVEL_SLOTS = [
    Slot("destination", "text", required=True,
         question="Which country or countries are you travelling to?"),
    Slot("travel_dates", "date",
         question="When are you travelling?"),
    Slot("trip_duration_days", "int", min=1, max=365, unit="days",
         question="How many days is the trip?"),
    Slot("traveller_age", "int", min=0, max=120, unit="years",
         question="Age of each traveller?"),
    Slot("travel_purpose", "enum", enum_values=("business", "leisure", "study", "medical", "visiting"),
         question="What is the purpose of the trip?"),
    Slot("visa_type", "enum", enum_values=("none", "schengen", "usa", "uk", "canada", "australia", "other"),
         question="Any visa involved?"),
    Slot("adventure_sports", "bool",
         question="Any adventure sports - trekking, diving, skiing?"),
    Slot("travel_cancellation", "bool",
         question="Do you want trip cancellation cover?"),
]


# ---------------------------------------------------------------------------
# Product line registry
# ---------------------------------------------------------------------------

PRODUCT_LINES: dict[str, dict[str, Any]] = {
    "motor": {
        "label": "Motor Insurance",
        "slots": MOTOR_SLOTS + PERSON_SLOTS,
        "engine": "motor",
    },
    "health": {
        "label": "Health Insurance",
        "slots": HEALTH_SLOTS + PERSON_SLOTS,
        "engine": "health",
    },
    "life": {
        "label": "Life Insurance",
        "slots": LIFE_SLOTS,
        "engine": "life",
    },
    "travel": {
        "label": "Travel Insurance",
        "slots": TRAVEL_SLOTS,
        "engine": None,          # no deterministic engine yet
    },
}


def get_schema(product: str) -> list[Slot]:
    """Slot list for a product line. Raises on unknown product rather than guessing."""
    key = (product or "").strip().lower()
    if key not in PRODUCT_LINES:
        raise KeyError(f"unknown product line: {product!r} "
                       f"(known: {sorted(PRODUCT_LINES)})")
    return PRODUCT_LINES[key]["slots"]


def engine_for(product: str) -> str | None:
    """Name of the deterministic engine handling this product, if one exists."""
    key = (product or "").strip().lower()
    if key not in PRODUCT_LINES:
        raise KeyError(f"unknown product line: {product!r}")
    return PRODUCT_LINES[key]["engine"]


def product_label(product: str) -> str:
    return PRODUCT_LINES[(product or "").strip().lower()]["label"]


def is_answered(slot_name: str, filled: dict) -> bool:
    """Whether a slot counts as answered.

    Subtle but important: an EMPTY LIST is a valid answer, not missing data.
    "I have no pre-existing conditions" fills `preexisting` with []. If that
    counted as missing, the conversation would ask the same question forever.

    So: key absent, or None, or an empty string = not answered.
        key present with [] = answered (the customer said "none").
    """
    if slot_name not in filled:
        return False
    value = filled[slot_name]
    if value is None:
        return False
    if isinstance(value, str) and not value.strip():
        return False
    return True


def missing_required(product: str, filled: dict) -> list[Slot]:
    """Required slots the customer has not answered yet."""
    return [s for s in get_schema(product) if s.required and not is_answered(s.name, filled)]