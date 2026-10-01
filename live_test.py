"""Live end-to-end test against the real model.

Not part of pytest: it costs tokens and needs network + the Pi serving the model.
Run manually:  python live_test.py

Walks a realistic motor conversation turn by turn, printing what the extractor
pulled out, what the engine computed, and what the model said back.
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

import config                                     # noqa: E402
from core.advisor import Advisor                  # noqa: E402

SCRIPT = [
    "hi, I need to insure my car",
    "I'm 34, I live in Pune",
    "It's a 2021 Creta diesel, the 1.5L variant",
    "I have a 35% no claim bonus and haven't claimed anything in 3 years",
    "I want comprehensive with zero depreciation and road assist",
]


def main():
    print("=" * 78)
    print("LIVE TEST -", config.MODEL)
    print("=" * 78)

    # cheap reachability probe first
    probe = Advisor(product="motor")
    h = probe.client.health()
    print(f"health: {h.get('ok')} {h.get('error', '')}")
    if not h.get("ok"):
        print("endpoint unreachable - aborting")
        return 1

    advisor = Advisor(product="motor")

    for msg in SCRIPT:
        print()
        print("-" * 78)
        print(f"YOU: {msg}")
        print("-" * 78)
        try:
            r = advisor.handle(msg)
        except Exception as e:
            print(f"TURN FAILED: {type(e).__name__}: {e}")
            return 1

        print(f"ASSISTANT: {r['reply']}")
        if r.get("extracted"):
            print(f"  [extracted] {r['extracted']}")
        if r.get("rejected"):
            print(f"  [rejected]  {r['rejected']}")
        if r.get("missing"):
            print(f"  [missing]   {r['missing']}")
        done, total = r["completeness"]
        print(f"  [profile]   {done}/{total} required")
        if r.get("quote"):
            q = r["quote"]
            print(f"  [QUOTE]     Rs {q['total']:,.0f} total "
                  f"(TP {q['third_party_premium']:,.0f} + OD "
                  f"{q['own_damage_premium']:,.0f} + addons {q['addon_premium']:,.0f})")

    print()
    print("=" * 78)
    print("FINAL PROFILE")
    print("=" * 78)
    print(advisor.profile.summary())
    return 0


if __name__ == "__main__":
    sys.exit(main())