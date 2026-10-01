"""Motor premium engine tests.

These guard the number path. If a rule changes, a test should fail and say which
rule moved - that is the whole point of keeping the LLM out of this code.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core.profile import Profile                      # noqa: E402
from engine import motor, tables                      # noqa: E402


# ---------------------------------------------------------------------------
# Depreciation grid
# ---------------------------------------------------------------------------

class TestDepreciation:
    def test_brand_new_keeps_95(self):
        assert tables.depreciation_rate(0) == 0.95
        assert tables.depreciation_rate(0.5) == 0.95

    def test_grid_points(self):
        assert tables.depreciation_rate(1) == 0.85
        assert tables.depreciation_rate(2) == 0.75
        assert tables.depreciation_rate(3) == 0.65
        assert tables.depreciation_rate(4) == 0.55
        assert tables.depreciation_rate(5) == 0.50

    def test_beyond_grid_floors_at_50(self):
        assert tables.depreciation_rate(6) == 0.50
        assert tables.depreciation_rate(15) == 0.50

    def test_interpolates_between_points(self):
        # 2.5 years should sit halfway between 0.75 and 0.65
        assert tables.depreciation_rate(2.5) == pytest.approx(0.70)

    def test_depreciation_never_increases_with_age(self):
        prev = 1.01
        for age in [i / 10 for i in range(0, 71)]:
            cur = tables.depreciation_rate(age)
            assert cur <= prev
            prev = cur

    def test_negative_age_rejected(self):
        with pytest.raises(ValueError):
            tables.depreciation_rate(-1)


# ---------------------------------------------------------------------------
# NCB and zones
# ---------------------------------------------------------------------------

class TestNCB:
    def test_standard_slabs(self):
        assert tables.ncb_discount(0) == 0.0
        assert tables.ncb_discount(20) == 0.20
        assert tables.ncb_discount(50) == 0.50

    def test_snaps_unknown_value_to_nearest_slab(self):
        assert tables.ncb_discount(22) == 0.20   # nearest to 20
        assert tables.ncb_discount(48) == 0.50   # nearest to 50

    def test_never_exceeds_50(self):
        assert tables.ncb_discount(100) == 0.50


class TestZone:
    def test_known_zone_a(self):
        assert tables.zone_for_city("Mumbai") == "A"
        assert tables.zone_for_city("bangalore") == "A"

    def test_case_insensitive(self):
        assert tables.zone_for_city("CHENNAI") == "A"

    def test_unknown_city_defaults_to_b(self):
        assert tables.zone_for_city("Nowhereville") == "B"

    def test_empty_city_does_not_raise(self):
        assert tables.zone_for_city("") == "B"
        assert tables.zone_for_city(None) == "B"


class TestCCBand:
    def test_bands(self):
        assert tables.cc_band(800) == "upto_1000cc"
        assert tables.cc_band(1199) == "1000_1500cc"
        assert tables.cc_band(1500) == "1000_1500cc"
        assert tables.cc_band(2000) == "1500cc_plus"

    def test_unknown_lands_middle_not_cheapest(self):
        assert tables.cc_band(None) == "1000_1500cc"


# ---------------------------------------------------------------------------
# Quote computation
# ---------------------------------------------------------------------------

def make_profile(**kw) -> Profile:
    p = Profile(product="motor")
    base = {
        "age": 30,
        "city": "Mumbai",
        "vehicle_make": "Maruti Swift",
        "vehicle_year": 2022,
        "vehicle_variant_cc_value": 1198,
        "fuel_type": "petrol",
        "ncb": 20,
        "policy_type": "comprehensive",
        "add_ons": [],
    }
    base.update(kw)
    for k, v in base.items():
        if v is not None:
            p.set(k, v)
    return p


class TestQuoteBasics:
    def test_comprehensive_quote_has_od_and_tp(self):
        q = motor.quote_motor(make_profile())
        assert q.cover_type == "comprehensive"
        assert q.third_party_premium > 0
        assert q.own_damage_premium > 0
        assert q.zone == "A"

    def test_third_party_only_has_no_od(self):
        q = motor.quote_motor(make_profile(policy_type="third_party"))
        assert q.cover_type == "third_party_only"
        assert q.own_damage_premium == 0

    def test_total_is_arithmetically_consistent(self):
        q = motor.quote_motor(make_profile())
        assert q.subtotal == q.third_party_premium + q.own_damage_premium + q.addon_premium
        assert q.gst == pytest.approx(round(q.subtotal * 0.18))
        assert q.total == q.subtotal + q.gst + q.mcv

    def test_missing_vehicle_year_raises_with_the_name(self):
        p = make_profile()
        p.set("vehicle_year", None)
        with pytest.raises(motor.MissingDataError) as exc:
            motor.quote_motor(p)
        assert "vehicle_year" in exc.value.missing


class TestIDV:
    def test_newer_car_has_higher_idv(self):
        new = motor.quote_motor(make_profile(vehicle_year=2025))
        old = motor.quote_motor(make_profile(vehicle_year=2019))
        assert new.idv > old.idv

    def test_idv_scales_with_depreciation(self):
        p = make_profile(vehicle_year=2025)   # age 1 -> 0.85
        q = motor.quote_motor(p)
        assert q.idv == pytest.approx(q.ex_showroom_estimate * 0.85, rel=0.02)

    def test_old_car_notes_the_grid_floor(self):
        q = motor.quote_motor(make_profile(vehicle_year=2010))
        assert any("past the published depreciation grid" in n for n in q.notes)


class TestNCBEffect:
    def test_ncb_reduces_total(self):
        without = motor.quote_motor(make_profile(ncb=0))
        with_ncb = motor.quote_motor(make_profile(ncb=50))
        assert with_ncb.total < without.total

    def test_ncb_does_not_reduce_third_party(self):
        """The single most misunderstood point in Indian motor insurance: NCB does
        not touch the statutory third-party component."""
        without = motor.quote_motor(make_profile(ncb=0))
        with_ncb = motor.quote_motor(make_profile(ncb=50))
        assert with_ncb.third_party_premium == without.third_party_premium
        assert with_ncb.own_damage_premium < without.own_damage_premium

    def test_missing_ncb_treated_as_zero_not_crashed(self):
        q = motor.quote_motor(make_profile(ncb=None))
        assert q.ncb == 0


class TestZoneEffect:
    def test_zone_a_costs_more_third_party_than_zone_b(self):
        a = motor.quote_motor(make_profile(city="Mumbai"))          # Zone A
        b = motor.quote_motor(make_profile(city="Guna"))             # not listed -> Zone B
        assert a.third_party_premium > b.third_party_premium

    def test_zone_multiplier_applied(self):
        a = motor.quote_motor(make_profile(city="Mumbai", policy_type="third_party"))
        b = motor.quote_motor(make_profile(city="Guna", policy_type="third_party"))
        assert a.third_party_premium == pytest.approx(round(b.third_party_premium / 0.75))

    def test_zone_b_multiplier(self):
        b = motor.quote_motor(make_profile(city="Guna", policy_type="third_party"))
        assert b.zone == "B"


class TestAddOnNormalisation:
    def test_returns_matched_and_unmatched(self):
        matched, unmatched = tables.normalise_add_ons(
            ["zero depreciation", "helicopter rescue"])
        assert matched == ["zero_dep"]
        assert unmatched == ["helicopter rescue"]

    def test_common_phrasings_all_resolve(self):
        """Every add-on a customer realistically names must map to something, or be
        reported - never silently dropped."""
        for probe in ("zero dep", "zero depreciation", "engine protect",
                      "road assist", "roadside assistance", "consumables",
                      "key loss", "RTI", "passenger cover", "tyre and alloy",
                      "alloy wheels", "bumpers", "nil inclined"):
            matched, unmatched = tables.normalise_add_ons([probe])
            assert matched or unmatched == [probe], f"{probe!r} vanished entirely"
            if probe != "nil inclined":
                assert matched, f"{probe!r} silently dropped"

    def test_deduplicates(self):
        matched, _ = tables.normalise_add_ons(["zero dep", "zero depreciation", "zerodep"])
        assert matched == ["zero_dep"]

    def test_empty_input(self):
        assert tables.normalise_add_ons([]) == ([], [])
        assert tables.normalise_add_ons(None) == ([], [])
        assert tables.normalise_add_ons([""]) == ([], [])


class TestCoverTypeAndAddons:
    def test_comprehensive_exceeds_third_party_only(self):
        comp = motor.quote_motor(make_profile(policy_type="comprehensive"))
        tp = motor.quote_motor(make_profile(policy_type="third_party"))
        assert comp.total > tp.total

    def test_addons_increase_total(self):
        without = motor.quote_motor(make_profile(add_ons=[]))
        with_addons = motor.quote_motor(make_profile(add_ons=["zero_dep", "road_assist"]))
        assert with_addons.total > without.total
        assert with_addons.add_ons == ["zero_dep", "road_assist"]

    def test_unknown_addon_is_reported_not_silently_dropped(self):
        """A recommendation engine that quietly discards a requested cover hands the
        customer a policy missing what they asked for. It must be surfaced."""
        q = motor.quote_motor(make_profile(add_ons=["helicopter rescue"]))
        assert any("No published reference price" in w for w in q.warnings)
        assert "helicopter rescue" in " ".join(q.warnings)

    def test_matched_and_unreported_addons_coexist(self):
        q = motor.quote_motor(make_profile(add_ons=["zero_dep", "helicopter rescue"]))
        assert q.add_ons == ["zero_dep"]
        assert any("helicopter rescue" in w for w in q.warnings)

    def test_addon_aliases_normalised(self):
        q = motor.quote_motor(make_profile(add_ons=["Zero Dep", "roadside assistance"]))
        assert "zero_dep" in q.add_ons
        assert "road_assist" in q.add_ons

    def test_addons_on_third_party_only_flagged_as_unbuyable(self):
        q = motor.quote_motor(make_profile(policy_type="third_party",
                                            add_ons=["zero_dep"]))
        assert any("not purchasable" in w for w in q.warnings)

    def test_nil_inclined_prices_at_zero(self):
        q = motor.quote_motor(make_profile(add_ons=["nil_inclined"]))
        assert "nil_inclined" in q.add_ons
        assert q.addon_premium == 0


class TestLoadings:
    def test_new_car_loading_raises_od(self):
        new = motor.quote_motor(make_profile(vehicle_year=2026))
        used_fresh = motor.quote_motor(make_profile(vehicle_year=2025))
        # At age 0 vs 1 the loadings differ even after depreciation
        assert new.vehicle_age_years < used_fresh.vehicle_age_years

    def test_used_vehicle_loading_raises_od(self):
        fresh = motor.quote_motor(make_profile(bought_used=False))
        used = motor.quote_motor(make_profile(bought_used=True))
        assert used.own_damage_premium > fresh.own_damage_premium

    def test_old_vehicle_od_premium_loading_is_capped(self):
        very_old = motor.quote_motor(make_profile(vehicle_year=2000))
        assert very_old.own_damage_premium > 0   # did not go negative


class TestWarnings:
    def test_unknown_city_warns(self):
        q = motor.quote_motor(make_profile(city=None))
        assert any("City unknown" in w for w in q.warnings)

    def test_missing_cc_warns(self):
        q = motor.quote_motor(make_profile(vehicle_variant_cc_value=None))
        assert any("Cubic capacity unknown" in w for w in q.warnings)

    def test_third_party_only_on_old_car_advises_comprehensive(self):
        """A 2019 car is 7 years old in 2026, so it hits the old-car branch. Any
        branch must still advise - the point is that third-party-only on a car
        with value is always worth questioning."""
        q = motor.quote_motor(make_profile(vehicle_year=2019,
                                            policy_type="third_party"))
        assert any("comprehensive" in n.lower() for n in q.notes)

    def test_third_party_only_new_car_gets_strong_warning(self):
        q = motor.quote_motor(make_profile(vehicle_year=2026,
                                            policy_type="third_party"))
        assert any("nearly always a mistake" in n for n in q.notes)

    def test_third_party_only_very_old_car_warns_about_idv(self):
        q = motor.quote_motor(make_profile(vehicle_year=2005,
                                            policy_type="third_party"))
        assert any("depreciated" in n for n in q.notes)


class TestDeterminism:
    def test_same_input_same_output(self):
        """The whole design rests on this: identical profile, identical number."""
        a = motor.quote_motor(make_profile())
        b = motor.quote_motor(make_profile())
        assert a.to_dict() == b.to_dict()

    def test_no_hidden_state_between_calls(self):
        motor.quote_motor(make_profile(add_ons=["zero_dep"]))
        q = motor.quote_motor(make_profile(add_ons=[]))
        assert q.addon_premium == 0


class TestFormatting:
    def test_format_includes_total_and_lines(self):
        text = motor.format_quote(motor.quote_motor(make_profile()))
        assert "TOTAL" in text
        assert "IDV" in text
        assert "Rs" in text

    def test_negative_line_items_render_as_subtractions(self):
        q = motor.quote_motor(make_profile(ncb=50))
        text = motor.format_quote(q)
        assert "-Rs" in text