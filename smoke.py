"""End-to-end smoke test: real profile in, real quote out.

Run directly:  python smoke.py
Exercises the deterministic engine with realistic profiles and prints the quotes
so the numbers can be eyeballed against what a broker would quote.
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from core.profile import Profile
from engine import motor


def show(title, **kw):
    p = Profile(product="motor")
    for k, v in kw.items():
        if v is not None:
            p.set(k, v)
    print()
    print("=" * 78)
    print(title)
    print("=" * 78)
    try:
        q = motor.quote_motor(p)
        print(motor.format_quote(q))
    except motor.MissingDataError as e:
        print(f"  MissingDataError: {e.missing}")


def main():
    print("SMOKE TEST - deterministic motor engine")
    print("Current year assumed:", motor.CURRENT_YEAR)

    show("New Swift, Mumbai, 20% NCB, comprehensive",
         age=30, city="Mumbai", vehicle_make="Maruti Swift",
         vehicle_year=2025, vehicle_variant_cc_value=1198,
         fuel_type="petrol", ncb=20, policy_type="comprehensive",
         add_ons=["zero_dep", "road_assist"])

    show("Same car, ZERO NCB (claim history) - price difference is the lesson",
         age=30, city="Mumbai", vehicle_make="Maruti Swift",
         vehicle_year=2025, vehicle_variant_cc_value=1198,
         fuel_type="petrol", ncb=0, policy_type="comprehensive",
         add_ons=["zero_dep", "road_assist"])

    show("5-year-old Creta, Zone B city, comprehensive",
         age=38, city="Guna", vehicle_make="Hyundai Creta SUV",
         vehicle_year=2021, vehicle_variant_cc_value=1496,
         fuel_type="diesel", ncb=35, policy_type="comprehensive")

    show("Third-party ONLY on a 1-year-old car - should get a strong warning",
         age=26, city="Bengaluru", vehicle_make="Honda City",
         vehicle_year=2025, vehicle_variant_cc_value=1498,
         fuel_type="petrol", ncb=50, policy_type="third_party")

    show("12-year-old car, third-party only - IDV depreciation floor",
         age=45, city="Kochi", vehicle_make="Maruti Alto",
         vehicle_year=2014, vehicle_variant_cc_value=796,
         fuel_type="petrol", ncb=50, policy_type="third_party")

    show("EV - no cubic capacity known, defaults to middle band",
         age=29, city="Delhi", vehicle_make="Tata Nexon EV",
         vehicle_year=2024, fuel_type="electric",
         ncb=20, policy_type="comprehensive")

    show("Missing required slot - should raise, not guess",
         age=30, city="Mumbai", vehicle_make="Maruti Swift")

    print()
    print("=" * 78)
    print("DETERMINISM CHECK - same input twice must be byte-identical")
    print("=" * 78)
    p = Profile(product="motor")
    p.merge_extraction({"age": 30, "city": "Mumbai", "vehicle_make": "Maruti Swift",
                        "vehicle_year": 2025, "vehicle_variant_cc_value": 1198,
                        "ncb": 20, "policy_type": "comprehensive",
                        "add_ons": ["zero_dep"]})
    a = motor.quote_motor(p).to_dict()
    b = motor.quote_motor(p).to_dict()
    print("identical:", a == b)
    print("total:", f"Rs {a['total']:,.0f}")


if __name__ == "__main__":
    main()