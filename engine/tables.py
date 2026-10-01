"""Public reference data for the motor premium engine.

Sources, all public:
  - Depreciation grid for IDV: IRDAI annual report / industry-standard schedule
    widely reproduced in motor policy wordings.
  - NCB slabs: IRDAI Motor Insurance Business (Records and Analytics) Regulations.
  - Zone A/B/C: statutory city categories used for third-party premium rates.

Every number here is a rule, not an estimate, so the engine stays deterministic and
testable. Rate multipliers per insurer are NOT here - those come from public
disclosures or a licensed feed, and live in the plan catalogue.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# IDV depreciation (% of ex-showroom value retained), by year of manufacture
# The published schedule caps out at 5 years; beyond that insurers apply the
# final row, which is why the last bucket is a range rather than a point.
# ---------------------------------------------------------------------------

DEPRECIATION_BY_AGE: dict[int, float] = {
    0: 0.95,   # < 1 year
    1: 0.85,   # 1 - 2 years
    2: 0.75,   # 2 - 3 years
    3: 0.65,   # 3 - 4 years
    4: 0.55,   # 4 - 5 years
    5: 0.50,   # 5+ years (floor; insurers may use up to 0.50)
}

# Beyond 5 years, IDV is commonly a fixed multiple of the depreciated value or a
# negotiated figure. We expose a floor so the estimate stays conservative.
MAX_AGE_FOR_GRID = 5
AGE_OUTLIER_FLOOR = 0.50


def depreciation_rate(age_years: float) -> float:
    """Depreciation % for a vehicle of the given age.

    Accepts float ages (a car registered 8 months ago is not 'age 0') and
    clamps anything past the published grid.
    """
    if age_years < 0:
        raise ValueError("vehicle age cannot be negative")
    if age_years <= 0.5:
        # Not yet a year old - most wordings apply the < 1 year rate
        return DEPRECIATION_BY_AGE[0]
    whole = int(age_years)
    rate = DEPRECIATION_BY_AGE.get(whole)
    if rate is None:
        return AGE_OUTLIER_FLOOR
    # Interpolate linearly between the two bracketing grid points so a 2.5-year-old
    # car does not jump discontinuously.
    if age_years == whole:
        return rate
    upper = DEPRECIATION_BY_AGE.get(whole + 1, AGE_OUTLIER_FLOOR)
    frac = age_years - whole
    return rate + (upper - rate) * frac


# ---------------------------------------------------------------------------
# No Claim Bonus slabs
# ---------------------------------------------------------------------------

# Eligible discount on the own-damage premium, by NCB percentage.
NCB_SLAB: dict[int, float] = {
    0: 0.00,
    20: 0.20,
    25: 0.25,
    35: 0.35,
    45: 0.45,
    50: 0.50,
}


def ncb_discount(ncb: int) -> float:
    """Discount fraction for an NCB percentage, snapping to the nearest slab."""
    if ncb in NCB_SLAB:
        return NCB_SLAB[ncb]
    candidates = sorted(NCB_SLAB)
    nearest = min(candidates, key=lambda c: abs(c - ncb))
    return NCB_SLAB[nearest]


# ---------------------------------------------------------------------------
# City zones - statutory categories for third-party premium
# ---------------------------------------------------------------------------

ZONE_A_CITIES = {
    "mumbai", "delhi", "bangalore", "bengaluru", "chennai", "kolkata", "hyderabad",
    "pune", "ahmedabad", "surat", "jaipur", "lucknow", "kanpur", "nagpur",
    "indore", "chandigarh", "coimbatore", "kochi", "thiruvananthapuram", "trivandrum",
    "bhopal", "patna", "ludhiana", "agra", "nashik", "faridabad", "meerut",
    "rajkot", "varanasi", "srinagar", "amritsar", "allahabad", "dhanbad",
    "howrah", "warangal", "jamshedpur", "kozhikode", "thanjavur", "tiruchirappalli",
}

ZONE_B_CITIES = {
    "mohali", "jamshedpur_extra", "thrissur", "mysore", "mysuru", "durgapur",
    "kolar", "tumkur", "dehradun", "raipur", "panipat", "kollam", "haldia",
}

# Anything not listed is Zone B by regulatory default for private vehicles.
DEFAULT_ZONE = "B"


def zone_for_city(city: str) -> str:
    """Zone rating for a city. Unknown cities fall back to Zone B, never raise."""
    if not city:
        return DEFAULT_ZONE
    c = str(city).strip().lower()
    if c in ZONE_A_CITIES:
        return "A"
    if c in ZONE_B_CITIES:
        return "B"
    return DEFAULT_ZONE


# ---------------------------------------------------------------------------
# Third-party (Act Only) liability floor
# ---------------------------------------------------------------------------

# The statutory minimum third-party liability cover, expressed in rupees of
# insured liability. Raised by IRDAI notification; used here as the floor for the
# liability component, not as a premium.
TP_MINIMUM_LIABILITY = 7_500_000          # 7.5 crore
TP_PREVIOUS_LIABILITY = 5_000_000          # historical, kept for comparison view

# Year-wise cap on third-party premium for private cars under 1000cc, ex-GST.
# Published in the IRDAI annual report. Zone-indexed: Zone A pays the base,
# Zones B/C pay less.
TP_BASE_PREMIUM_BY_CC_BAND: dict[str, int] = {
    "upto_1000cc": 2_766,       # <= 1000cc
    "1000_1500cc": 3_287,       # 1000-1500cc
    "1500cc_plus": 4_193,       # > 1500cc
}

ZONE_MULTIPLIER = {"A": 1.0, "B": 0.75, "C": 0.65}

GST_RATE = 0.18                # 18% GST on motor premium
MCV_RATE = 0.06                # 6% of premium to Motor Vehicle Fund (stamped into quotes)


# ---------------------------------------------------------------------------
# Own-damage rate model
# ---------------------------------------------------------------------------

# Published base rate per 1000 of sum-insured for private cars, by cubic capacity.
# Zone-indexed. These are the components insurers build their OD rate card on.
OD_BASE_RATE_PER_1000: dict[str, float] = {
    "upto_1000cc": 4.80,        # rupees of premium per 1000 of sum insured
    "1000_1500cc": 6.20,
    "1500cc_plus": 8.50,
}

# New-car surcharge: an all-risk policy on a brand-new car carries a loading on
# the OD premium because theft/accident claims dominate the first year.
NEW_CAR_OD_LOADING = 0.15

# Age loading on OD premium once the vehicle passes the grid.
OLD_VEHICLE_OD_LOADING_PER_YEAR = 0.03
OLD_VEHICLE_MAX_LOADING = 0.25

# Used-vehicle purchase loading on OD, applied when buying cover on a used car.
USED_VEHICLE_OD_LOADING = 0.10

# Make/model risk loading bands, chosen from published rate-card practice. Keyed by
# body type the user describes; defaults to 0 when unknown.
OD_RISK_LOADING: dict[str, float] = {
    "suv": 0.10,
    "luxury": 0.25,
    "sports": 0.30,
    "commercial": 0.15,
    "hatchback": 0.0,
    "sedan": 0.0,
    "compact": 0.0,
}

# Add-on base premiums, ex-GST, before zone loading. Indicative public ranges,
# used to answer "roughly what will the add-ons cost" honestly.
ADDON_BASE_PREMIUM: dict[str, float] = {
    "zero_dep": 1_200,
    "engine_protect": 900,
    "road_assist": 750,
    "consumables": 400,
    "key_loss": 350,
    "rti": 300,
    "passenger_personal_accident": 450,   # per seat, scaled below
    "tyre_and_alloy": 700,
    "bumper_bumper": 500,
    "nil_inclined": 0,
}

ADDON_ALIASES = {
    "zero dep": "zero_dep", "zero depreciation": "zero_dep", "zerodep": "zero_dep",
    "engine protect": "engine_protect", "engine damage": "engine_protect",
    "engine protect cover": "engine_protect",
    "road assist": "road_assist", "roadside": "road_assist",
    "roadside assistance": "road_assist", "towing": "road_assist",
    "consumables": "consumables", "engine consumables": "consumables",
    "key loss": "key_loss", "key replacement": "key_loss",
    "rti": "rti", "road tax": "rti",
    "passenger cover": "passenger_personal_accident",
    "ppa": "passenger_personal_accident",
    "nil inclined": "nil_inclined", "nil_inv": "nil_inclined",
    "tyre": "tyre_and_alloy", "tyres": "tyre_and_alloy",
    "tyre and alloy": "tyre_and_alloy", "tyre & alloy": "tyre_and_alloy",
    "alloy wheel": "tyre_and_alloy", "alloy wheels": "tyre_and_alloy",
    "bumper": "bumper_bumper", "bumpers": "bumper_bumper",
    "bumper bumper": "bumper_bumper",
}


def cc_band(cubic_capacity: int | None) -> str:
    """Bucket a cubic capacity into a rate band. Unknown capacity lands in the
    middle band rather than guessing cheap."""
    if cubic_capacity is None:
        return "1000_1500cc"
    if cubic_capacity <= 1000:
        return "upto_1000cc"
    if cubic_capacity <= 1500:
        return "1000_1500cc"
    return "1500cc_plus"


def normalise_add_ons(raw: list) -> tuple[list[str], list[str]]:
    """Map whatever the user called their add-ons onto canonical keys.

    Returns (matched, unmatched). The caller MUST surface unmatched entries rather
    than dropping them: a recommendation engine that silently discards "tyre
    cover" will recommend a plan that is missing the thing the customer actually
    asked for, and they will find out only at the point of claim.
    """
    matched: list[str] = []
    unmatched: list[str] = []
    for item in raw or []:
        text = str(item).strip().lower().replace("_", " ")
        if not text:
            continue
        canon = ADDON_ALIASES.get(text) or ADDON_ALIASES.get(text.replace(" ", "_"))
        if canon is None:
            for alias, key in ADDON_ALIASES.items():
                if alias in text:
                    canon = key
                    break
        if canon is None:
            unmatched.append(str(item))
        elif canon not in matched:
            matched.append(canon)
    return matched, unmatched