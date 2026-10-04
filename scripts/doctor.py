#!/usr/bin/env python3
"""Environment doctor for douyin-download v3.

Usage: python3 doctor.py [--cdp URL] [--json]
Prints JSON; exits 0 when every required check passes.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dy_common as C  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cdp", default=C.DEFAULT_CDP)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    report = C.run_doctor(cdp=a.cdp)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
