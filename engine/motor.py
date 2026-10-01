"""Deterministic motor premium engine.

Hard rule: NO LLM IN THIS PATH. Premium maths is arithmetic over published rules,
so it lives in plain code with unit tests. A number produced here is a number a
customer may act on, so it must be reproducible and explainable.

Pipeline:
    profile slots
      -> vehicle age, IDV (depreciation grid)
      -> third-party component (statutory, cubic-capacity + zone)
      -> own-damage component (rate per 1000 of sum insured + loadings - NCB)
      -> add-ons
      -> GST and MCV
      -> MotorQuote with a line-by-line explanation
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any

from . import tables
from core.profile import Profile

CURRENT_YEAR = 2026


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass
class LineItem:
    label: str
    amount: float
    note: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class MotorQuote:
    """A computed estimate with its full working shown.

    Not a binding quote. Insurer-specific rate cards move these numbers; this is a
    defensible order-of-magnitude estimate from published rule data.
    """

    cover_type: str
    zone: str
    vehicle_age_years: float
    cc_band: str
    ex_showroom_estimate: float
    idv: float
    ncb: int

    third_party_premium: float
    own_damage_premium: float
    addon_premium: float

    subtotal: float
    gst: float
    mcv: float
    total: float

    lines: list[LineItem] = field(default_factory=list)
    add_ons: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["lines"] = [li.to_dict() for li in self.lines]
        return d


class MissingDataError(ValueError):
    """Required profile slots absent, with the list of what is missing."""

    def __init__(self, missing: list[str]):
        self.missing = missing
        super().__init__(f"missing required data: {', '.join(missing)}")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def vehicle_age(registration_year: int, current_year: int = CURRENT_YEAR) -> float:
    """Age in years. Mid-year registration is common, so callers may prefer a
    more precise age; this is the conservative whole-year form."""
    return max(0.0, float(current_year - registration_year))


def _estimate_ex_showroom(profile: Profile) -> tuple[float, str]:
    """Ex-showroom value, with an explicit note about where the number came from.

    Ex-showroom is NOT derivable from the profile alone - it needs the make/model
    price list. We use a transparent fallback band by body type rather than
    pretending to know it, and say so in the quote.
    """
    make = (profile.get("vehicle_make") or "").lower()
    cc = profile.get("vehicle_variant_cc")
    cc_val = profile.get("vehicle_variant_cc_value")

    # If a price list lookup ever lands in the profile, use it.
    explicit = profile.get("ex_showroom")
    if isinstance(explicit, (int, float)) and explicit > 0:
        return float(explicit), "from supplied ex-showroom value"

    band = tables.cc_band(cc_val) if cc_val else "1000_1500cc"
    base = {
        "upto_1000cc": 550_000,
        "1000_1500cc": 900_000,
        "1500cc_plus": 1_600_000,
    }[band]

    # Body-type nudge from the make string, if it names one.
    for keyword, mult in (("suv", 1.25), ("luxury", 2.2), ("bmw", 2.2),
                          ("mercedes", 2.2), ("audi", 2.0), ("swift", 0.65),
                          ("hatchback", 0.65), ("i20", 0.75), ("creta", 1.25)):
        if keyword in make:
            base *= mult
            break

    return base, (f"estimated from cubic-capacity band ({band}); "
                  f"ex-showroom needs a price-list lookup to be exact")


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def quote_motor(profile: Profile, current_year: int = CURRENT_YEAR) -> MotorQuote:
    """Compute a motor premium estimate from a filled profile.

    Raises MissingDataError rather than guessing when required slots are absent.
    """
    required = ("vehicle_year",)
    missing = [r for r in required if profile.get(r) in (None, "", [], {})]
    if missing:
        raise MissingDataError(missing)

    lines: list[LineItem] = []
    notes: list[str] = []
    warnings: list[str] = []

    # ---- cover type ----------------------------------------------------
    cover_raw = (profile.get("policy_type") or "comprehensive").lower()
    if "third" in cover_raw and ("only" in cover_raw or cover_raw == "third_party"):
        cover_type = "third_party_only"
    else:
        cover_type = "comprehensive"

    # ---- zone ----------------------------------------------------------
    zone = tables.zone_for_city(profile.get("city") or "")
    if not profile.get("city"):
        warnings.append("City unknown - defaulted to Zone B, which understates "
                        "the third-party component.")

    # ---- vehicle age and IDV -------------------------------------------
    age = vehicle_age(int(profile.get("vehicle_year")), current_year)
    depr = tables.depreciation_rate(age)

    ex_showroom, ex_note = _estimate_ex_showroom(profile)
    idv = round(ex_showroom * depr, -2)   # round to nearest hundred, as insurers do
    notes.append(f"IDV {ex_note}.")
    if age > tables.MAX_AGE_FOR_GRID:
        notes.append(f"Vehicle is {age:.0f} years old, past the published depreciation "
                     f"grid - the {tables.AGE_OUTLIER_FLOOR:.0%} floor was applied. "
                     f"Insurers often agree an IDV by inspection for such vehicles.")

    lines.append(LineItem(
        f"IDV (sum insured)",
        idv,
        f"Ex-showroom ~Rs {ex_showroom:,.0f} x {depr:.0%} depreciation for a "
        f"{age:.1f}-year-old vehicle",
    ))

    # ---- cubic capacity band -------------------------------------------
    cc_val = profile.get("vehicle_variant_cc_value")
    band = tables.cc_band(cc_val)
    if cc_val is None:
        warnings.append("Cubic capacity unknown - assumed the 1000-1500cc band. "
                        "Tell me the variant to sharpen this.")

    # NCB applies to the own-damage component only, so it is read up front and
    # defaults to zero - a missing NCB is a real 0%, not an unknown.
    ncb = profile.get("ncb")
    ncb = 0 if ncb is None else int(ncb)

    # ---- third-party component -----------------------------------------
    tp_base = tables.TP_BASE_PREMIUM_BY_CC_BAND[band]
    tp_pre_gst = round(tp_base * tables.ZONE_MULTIPLIER[zone])

    if cover_type == "third_party_only":
        od_pre_gst = 0.0
        # Advise upgrading whenever the car still has value worth protecting.
        # The older it is, the stronger the case - so the note escalates rather
        # than being suppressed for old vehicles, which is when it matters most.
        if age <= 1:
            notes.append("Third-party-only is nearly always a mistake on a car this "
                         "new: it will not pay for a single rupee of damage to your "
                         "own vehicle, or theft. Take comprehensive.")
        elif age <= tables.MAX_AGE_FOR_GRID:
            notes.append("Third-party-only does not cover damage to your own car. "
                         f"At {age:.0f} years old the car still has real value, so "
                         "comprehensive is usually the better buy.")
        else:
            notes.append(f"At {age:.0f} years old, weigh comprehensive carefully: "
                         "IDV is already depreciated, so you may be paying more "
                         "than the car is worth. Compare the OD premium against "
                         "an agreed IDV from an inspection before deciding.")
    else:
        # ---- own-damage component --------------------------------------
        rate_per_1000 = tables.OD_BASE_RATE_PER_1000[band]
        od_base = (idv / 1000.0) * rate_per_1000
        lines.append(LineItem(
            "Own-damage base",
            round(od_base),
            f"{rate_per_1000:.2f} per 1000 of IDV, {band}, Zone {zone}",
        ))

        # Loadings
        loading_pct = 0.0
        loading_reasons: list[str] = []

        if age < 1:
            loading_pct += tables.NEW_CAR_OD_LOADING
            loading_reasons.append(f"{tables.NEW_CAR_OD_LOADING:.0%} new-car loading")

        old_loading = min(
            tables.OLD_VEHICLE_MAX_LOADING,
            tables.OLD_VEHICLE_OD_LOADING_PER_YEAR * max(0, age - 1),
        )
        if old_loading > 0:
            loading_pct += old_loading
            loading_reasons.append(f"{old_loading:.0%} age loading")

        if profile.get("bought_used"):
            loading_pct += tables.USED_VEHICLE_OD_LOADING
            loading_reasons.append(f"{tables.USED_VEHICLE_OD_LOADING:.0%} used-vehicle loading")

        make = (profile.get("vehicle_make") or "").lower()
        for body, mult in tables.OD_RISK_LOADING.items():
            if body != "compact" and body in make:
                loading_pct += mult
                loading_reasons.append(f"{mult:.0%} {body} risk loading")
                break

        od_loaded = od_base * (1 + loading_pct)
        if loading_reasons:
            lines.append(LineItem(
                "Own-damage loadings",
                round(od_loaded - od_base),
                " + ".join(loading_reasons),
            ))
        od_pre_gst = round(od_loaded)

        # ---- NCB discount (applies to OD only) -------------------------
        disc = tables.ncb_discount(ncb)
        if disc > 0:
            reduction = round(od_pre_gst * disc)
            od_pre_gst -= reduction
            lines.append(LineItem(
                f"NCB {ncb}% discount",
                -reduction,
                f"{disc:.0%} off the own-damage premium only. It does not reduce "
                f"the third-party component, which is statutory.",
            ))
        else:
            notes.append("No NCB claimed. A 20% NCB is available for a claim-free "
                         "year, which is usually worth more than most add-ons.")

    tp_lines_note = (f"Statutory third-party cover, {band}, Zone {zone}. "
                     "Price is fixed by regulation - shop around and it will not move.")

    # ---- add-ons -------------------------------------------------------
    wanted = tables.normalise_add_ons(profile.get("add_ons") or [])
    addons_total = 0.0
    ppa_seats = 0
    for a in wanted:
        base = tables.ADDON_BASE_PREMIUM.get(a)
        if base is None:
            warnings.append(f"Add-on '{a}' has no reference price yet - excluded.")
            continue
        if a == "passenger_personal_accident":
            ppa_seats += 1
        addons_total += base

    if wanted and cover_type == "third_party_only":
        notes.append("Add-ons only apply on a comprehensive policy. They were "
                     "priced anyway so you can compare against comprehensive.")

    if addons_total:
        lines.append(LineItem(
            "Add-ons" + (f" ({', '.join(wanted)})" if wanted else ""),
            round(addons_total),
            "Indicative public ranges, ex-GST. No-claim-benefit (nil-inclined) is "
            "priced at zero because it pays out your own NCB money.",
        ))

    # ---- statutory charges ---------------------------------------------
    subtotal = tp_pre_gst + od_pre_gst + round(addons_total)
    gst = round(subtotal * tables.GST_RATE)
    mcv = round(subtotal * tables.MCV_RATE)
    total = subtotal + gst + mcv

    if tables.MCV_RATE > 0:
        lines.append(LineItem("GST @ 18%", gst, "Statutory"))
        lines.append(LineItem("MCV contribution @ 6%", mcv,
                              "Deducted from the premium, goes to the Motor Vehicle Fund"))

    lines.insert(0, LineItem("Third-party premium", tp_pre_gst, tp_lines_note))

    quote = MotorQuote(
        cover_type=cover_type,
        zone=zone,
        vehicle_age_years=age,
        cc_band=band,
        ex_showroom_estimate=round(ex_showroom, -2),
        idv=idv,
        ncb=ncb,
        third_party_premium=tp_pre_gst,
        own_damage_premium=od_pre_gst,
        addon_premium=round(addons_total),
        subtotal=subtotal,
        gst=gst,
        mcv=mcv,
        total=total,
        lines=lines,
        add_ons=wanted,
        notes=notes,
        warnings=warnings,
    )

    if cover_type == "third_party_only" and addons_total:
        # Add-ons are meaningless without own-damage cover; do not present a
        # third-party quote that includes them as if they were real.
        quote.warnings.append(
            "Add-ons priced on a third-party-only quote are not purchasable.")
        quote.addon_premium = 0

    return quote


def format_quote(quote: MotorQuote) -> str:
    """Plain-text quote for the chat transcript."""
    out = [
        f"--- {quote.cover_type.replace('_', ' ').upper()} ESTIMATE ---",
        f"IDV: Rs {quote.idv:,.0f}   |   Zone {quote.zone}   |   "
        f"{quote.vehicle_age_years:.0f}-year-old {quote.cc_band}",
        "",
    ]
    for li in quote.lines:
        amt = f"Rs {li.amount:,.0f}" if li.amount >= 0 else f"-Rs {abs(li.amount):,.0f}"
        out.append(f"  {li.label:32s} {amt:>14s}")
        if li.note:
            out.append(f"      {li.note}")
    out.append("")
    out.append(f"  {'TOTAL':32s} {'Rs ' + format(quote.total, ',.0f'):>14s}")
    out.append("")
    for n in quote.notes:
        out.append(f"  * {n}")
    for w in quote.warnings:
        out.append(f"  ! {w}")
    return "\n".join(out)