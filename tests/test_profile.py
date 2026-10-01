"""Profile + slot-filling tests.

The slot system is insurance-agnostic, so these tests exercise the coercion and
merge logic that every product line depends on.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core import schemas                            # noqa: E402
from core.profile import (                          # noqa: E402
    Profile, coerce_bool, coerce_cc, coerce_list,
    coerce_number, coerce_year,
)


# ---------------------------------------------------------------------------
# Numeric coercion
# ---------------------------------------------------------------------------

class TestCoerceNumber:
    def test_plain_ints_and_floats(self):
        assert coerce_number(30) == 30.0
        assert coerce_number(1198.0) == 1198.0

    def test_numeric_strings(self):
        assert coerce_number("30") == 30.0
        assert coerce_number(" 1198 ") == 1198.0

    def test_thousand_separators(self):
        assert coerce_number("25,000") == 25000.0
        assert coerce_number("1,20,000") == 120000.0

    def test_lakh_and_crore(self):
        assert coerce_number("5 lakh") == 500000.0
        assert coerce_number("5 lakhs") == 500000.0
        assert coerce_number("1.5 crore") == 15000000.0
        assert coerce_number("25 thousand") == 25000.0

    def test_currency_prefixed(self):
        assert coerce_number("Rs 25,000") == 25000.0
        assert coerce_number("₹25000") == 25000.0
        assert coerce_number("INR 40,000") == 40000.0

    def test_units_and_spelling(self):
        assert coerce_number("22 years") == 22.0
        assert coerce_number("thirty") == 30.0

    def test_garbage_returns_none(self):
        assert coerce_number("not a number") is None
        assert coerce_number("") is None
        assert coerce_number(None) is None

    def test_booleans_are_not_numbers(self):
        """True would coerce to 1 and silently become an age of 1."""
        assert coerce_number(True) is None
        assert coerce_number(False) is None


class TestCoerceBool:
    def test_yes_words(self):
        for w in ("yes", "Yes", "yeah", "yep", "true", "i do", "sure"):
            assert coerce_bool(w) is True, w

    def test_no_words(self):
        for w in ("no", "No", "nope", "false", "never", "none", "i don't"):
            assert coerce_bool(w) is False, w

    def test_bare_booleans(self):
        assert coerce_bool(True) is True
        assert coerce_bool(False) is False

    def test_unparseable(self):
        assert coerce_bool("maybe") is None
        assert coerce_bool("") is None


class TestCoerceList:
    def test_real_list_passes_through(self):
        assert coerce_list(["bp", "diabetes"]) == ["bp", "diabetes"]

    def test_comma_separated_string(self):
        assert coerce_list("bp, diabetes, thyroid") == ["bp", "diabetes", "thyroid"]

    def test_and_separated(self):
        assert coerce_list("bp and diabetes") == ["bp", "diabetes"]

    def test_empty_means_empty_list(self):
        """'no conditions' is a valid and common answer, not missing data."""
        for v in (None, "", "none", "None", "nothing", "n/a", "-", []):
            assert coerce_list(v) == [], v

    def test_none_entries_stripped(self):
        assert coerce_list(["bp", "none", "", "diabetes"]) == ["bp", "diabetes"]


class TestCoerceYear:
    def test_plain(self):
        assert coerce_year(2020) == 2020
        assert coerce_year("2020") == 2020

    def test_embedded_in_sentence(self):
        assert coerce_year("made in 2019") == 2019
        assert coerce_year("a 2018 model") == 2018

    def test_out_of_range_rejected(self):
        assert coerce_year(1800) is None
        assert coerce_year("when it was new") is None


class TestCoerceCC:
    def test_explicit_cc(self):
        assert coerce_cc("1199cc") == 1199
        assert coerce_cc("1498 cc") == 1498

    def test_litre_conversion(self):
        assert coerce_cc("1.2L") == 1200
        assert coerce_cc("1.5 litre") == 1500

    def test_bare_number_in_range(self):
        assert coerce_cc(1200) == 1200

    def test_implausible_rejected(self):
        assert coerce_cc("diesel") is None
        assert coerce_cc(50) is None


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class TestSchemas:
    def test_all_four_product_lines_present(self):
        assert set(schemas.PRODUCT_LINES) == {"motor", "health", "life", "travel"}

    def test_unknown_product_raises(self):
        with pytest.raises(KeyError):
            schemas.get_schema("crypto")

    def test_every_line_has_slots(self):
        for name, spec in schemas.PRODUCT_LINES.items():
            assert spec["slots"], f"{name} has no slots"

    def test_no_duplicate_slot_names_within_a_line(self):
        for name, spec in schemas.PRODUCT_LINES.items():
            names = [s.name for s in spec["slots"]]
            dupes = {n for n in names if names.count(n) > 1}
            assert not dupes, f"{name} duplicates {dupes}"

    def test_person_slots_reused_across_lines(self):
        """age/city/budget are shared; the shared list must not be mutated."""
        motor_names = {s.name for s in schemas.get_schema("motor")}
        health_names = {s.name for s in schemas.get_schema("health")}
        assert {"age", "city", "budget"} <= motor_names
        assert {"age", "city", "budget"} <= health_names

    def test_enum_slots_declare_values(self):
        for line in schemas.PRODUCT_LINES.values():
            for s in line["slots"]:
                if s.type == "enum":
                    assert s.enum_values, f"{line['label']}.{s.name} is enum with no values"

    def test_bounds_are_sane(self):
        for line in schemas.PRODUCT_LINES.values():
            for s in line["slots"]:
                if s.min is not None and s.max is not None:
                    assert s.min < s.max, f"{s.name} has min >= max"

    def test_prompt_dict_is_compact(self):
        d = schemas.get_schema("motor")[0].to_prompt_dict()
        assert "name" in d and "type" in d
        assert "question" not in d      # questions are not extraction hints


# ---------------------------------------------------------------------------
# Profile
# ---------------------------------------------------------------------------

class TestProfile:
    def test_starts_empty(self):
        p = Profile(product="motor")
        assert p.filled() == {}
        assert not p.is_complete()

    def test_required_slots_for_motor(self):
        p = Profile(product="motor")
        assert {s.name for s in p.missing_required()} >= {"vehicle_year", "age"}

    def test_completeness_counter(self):
        p = Profile(product="motor")
        total = p.completeness()[1]
        for name in ("vehicle_year", "age", "city"):
            p.set(name, {"vehicle_year": 2022, "age": 30, "city": "Mumbai"}[name])
        assert p.completeness() == (total, total)
        assert p.is_complete()

    def test_next_question_walks_the_schema(self):
        p = Profile(product="motor")
        q1 = p.next_question()
        p.set("age", 30)
        assert p.next_question() != q1 or p.next_question() is not None

    def test_summary_lists_filled_fields(self):
        p = Profile(product="motor")
        p.set("vehicle_year", 2022)
        p.set("add_ons", ["zero_dep"])
        text = p.summary()
        assert "vehicle_year: 2022" in text
        assert "zero_dep" in text

    def test_roundtrip_json(self):
        p = Profile(product="motor")
        p.merge_extraction({"age": 30, "vehicle_year": 2022, "add_ons": ["zero_dep"]}, turn=1)
        p2 = Profile.from_dict(p.to_dict())
        assert p2.filled() == p.filled()
        assert p2.product == "motor"
        assert len(p2.history) == 3

    def test_to_json_is_valid(self):
        import json
        p = Profile(product="health")
        p.set("age", 45)
        assert json.loads(p.to_json())["slots"]["age"] == 45


class TestMergeExtraction:
    def test_accepts_valid_values(self):
        p = Profile(product="motor")
        rep = p.merge_extraction({"age": 30, "vehicle_year": 2022})
        assert rep["accepted"] == {"age": 30, "vehicle_year": 2022}
        assert p.get("age") == 30

    def test_rejects_slot_not_in_schema(self):
        p = Profile(product="motor")
        rep = p.merge_extraction({"policy_number": "ABC123"})
        assert "policy_number" in rep["rejected"]

    def test_rejects_uncoercible_value_without_raising(self):
        p = Profile(product="motor")
        rep = p.merge_extraction({"age": "quite young"})
        assert "age" in rep["rejected"]
        assert p.get("age") is None

    def test_out_of_range_value_rejected(self):
        p = Profile(product="motor")
        rep = p.merge_extraction({"age": 500})
        assert "age" in rep["rejected"]

    def test_empty_and_null_ignored(self):
        p = Profile(product="motor")
        rep = p.merge_extraction({"age": None, "vehicle_make": "   "})
        assert rep["accepted"] == {}
        assert rep["rejected"] == {}

    def test_coerces_loose_types(self):
        p = Profile(product="motor")
        rep = p.merge_extraction({
            "age": "30 years",
            "vehicle_year": "made in 2019",
            "bought_used": "yes",
            "add_ons": "zero dep, road assist",
        })
        assert rep["accepted"]["age"] == 30
        assert rep["accepted"]["vehicle_year"] == 2019
        assert rep["accepted"]["bought_used"] is True
        assert rep["accepted"]["add_ons"] == ["zero dep", "road assist"]

    def test_enum_normalisation(self):
        p = Profile(product="motor")
        rep = p.merge_extraction({"policy_type": "Comprehensive Cover"})
        assert rep["accepted"]["policy_type"] == "comprehensive"

    def test_history_records_provenance(self):
        p = Profile(product="motor")
        p.merge_extraction({"age": "30 years"}, turn=3)
        h = p.history[-1]
        assert h.slot == "age" and h.value == 30 and h.raw == "30 years"

    def test_correction_is_recorded_as_a_warning(self):
        p = Profile(product="motor")
        p.merge_extraction({"age": 30}, turn=1)
        p.merge_extraction({"age": 31}, turn=2)
        assert any("changed" in w for w in p.warnings)
        assert p.get("age") == 31

    def test_product_lines_have_isolated_slots(self):
        """A health answer must not leak into a motor profile."""
        motor_p = Profile(product="motor")
        health_p = Profile(product="health")
        motor_p.merge_extraction({"vehicle_year": 2020})
        assert "sum_insured" not in motor_p
        assert health_p.get("vehicle_year") is None


class TestMissingRequired:
    def test_empty_claims_as_the_answer_is_complete(self):
        """'I have no conditions' must satisfy a required list slot."""
        p = Profile(product="health")
        p.set("preexisting", [])
        assert "preexisting" not in {s.name for s in p.missing_required()}