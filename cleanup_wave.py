#!/usr/bin/env python3
"""
cleanup_wave.py - tidy data/cards.json after a wave with duplicate scans.

Dry-run by default (prints what it WOULD do). Run with --apply to execute.

    py cleanup_wave.py --out .            # report only
    py cleanup_wave.py --out . --apply    # do it

What it does:
  REMOVE   exact duplicates: same callsign+date+band+mode beyond the first
           copy kept. Files deleted (originals still live in processed\\).
  REVIEW   degenerate/misread entries get pulled from the manifest and their
           images moved to review\\ for a human decision:
             - dateless entries when a dated entry for the same callsign exists
             - park refs read as callsigns (K-####, US-####, POTA...)
             - the recipient's own callsign (K8JKU)
             - implausible years (<2015) when the same callsign also has a
               plausible-year entry
  REPORT   left in place, listed for eyeballing:
             - same callsign with multiple different dates (could be real
               repeat QSOs, could be a misread date on a duplicate scan)
             - entries with no band or mode
             - entries never verified by QRZ
"""
import argparse
import json
import shutil
from collections import defaultdict
from pathlib import Path

OWN_CALL = "K8JKU"
import re
PARK_RE = re.compile(r"^(POTA[\s_-]*)?(US|K|VE)[\s_-]*\d{3,5}$")
JUNK = {"null", "none", "unk", "unknown", "n/a", "2-way", "2way", "two-way"}


def scrub(c):
    """Normalize junk band/mode strings left by earlier script versions."""
    for k in ("band", "mode"):
        if c.get(k) and str(c[k]).strip().lower() in JUNK:
            c[k] = None
    return c


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=".", help="repo root")
    ap.add_argument("--apply", action="store_true", help="actually modify files")
    args = ap.parse_args()

    out = Path(args.out)
    manifest_path = out / "data" / "cards.json"
    review_dir = out / "review"
    review_dir.mkdir(exist_ok=True)
    m = json.loads(manifest_path.read_text(encoding="utf-8"))
    cards = [scrub(c) for c in m["cards"]]
    print(f"Manifest: {len(cards)} entries\n")

    keep, remove_dupe, to_review = [], [], []

    # pass 1: exact duplicates (first occurrence wins, wave order preserved)
    seen = set()
    for c in cards:
        key = (c["callsign"], c.get("date"), c.get("band"), c.get("mode"))
        if key in seen:
            remove_dupe.append(c)
        else:
            seen.add(key)
            keep.append(c)

    # index the survivors by callsign for sibling rules
    by_call = defaultdict(list)
    for c in keep:
        by_call[c["callsign"]].append(c)
    # index verified QSO signatures for misread-twin detection
    verified_qsos = {(c.get("date"), c.get("band"), c.get("mode"))
                     for c in keep if c.get("qrz_verified") and c.get("date")}

    def year(c):
        return int(c["date"][:4]) if c.get("date") else None
    def logged(c):
        return "log" in (c.get("source") or "")

    survivors = []
    for c in keep:
        sibs = [s for s in by_call[c["callsign"]] if s is not c]
        reason = None
        if PARK_RE.match(c["callsign"] or ""):
            reason = "park ref read as callsign"
        elif c["callsign"] == OWN_CALL:
            reason = "recipient's own callsign"
        elif c.get("date") is None and any(s.get("date") for s in sibs):
            reason = "dateless duplicate of a dated card"
        elif year(c) and year(c) < 2015 and any((year(s) or 0) >= 2015 for s in sibs):
            reason = f"implausible year {year(c)} beside a plausible sibling"
        elif (not logged(c)) and any(
                logged(s) and s.get("band") == c.get("band")
                and s.get("mode") == c.get("mode") and s.get("date") != c.get("date")
                for s in sibs):
            reason = "card-only date beside a log-matched twin (likely misread date)"
        elif (not c.get("band")) and any(
                s.get("date") == c.get("date") and s.get("band") for s in sibs):
            reason = "empty duplicate of a fuller same-day entry"
        elif (not c.get("qrz_verified")) and c.get("date") and \
                (c.get("date"), c.get("band"), c.get("mode")) in verified_qsos:
            reason = "unverified callsign matching a verified card's exact QSO (likely misread call)"
        if reason:
            to_review.append((c, reason))
        else:
            survivors.append(c)

    # ---------- report ----------
    print(f"=== REMOVE as exact duplicates: {len(remove_dupe)} ===")
    for c in remove_dupe:
        print(f"  {c['id']}")

    print(f"\n=== PULL to review\\: {len(to_review)} ===")
    for c, why in to_review:
        print(f"  {c['id']:45}  ({why})")

    print(f"\n=== KEEP: {len(survivors)} ===")

    # report-only oddities among survivors
    multi = {call: cs for call, cs in by_call.items()
             if len({c.get('date') for c in cs if c in survivors}) > 1}
    if multi:
        print("\n--- Same callsign, multiple dates (verify these are real repeat QSOs) ---")
        for call, cs in sorted(multi.items()):
            dates = sorted(str(c.get("date")) for c in cs if c in survivors)
            print(f"  {call:10}  {', '.join(dates)}")

    gaps = [c for c in survivors if not c.get("band") or not c.get("mode")]
    if gaps:
        print("\n--- Missing band/mode (fix by hand in cards.json if the card shows it) ---")
        for c in gaps:
            print(f"  {c['id']}")

    unver = [c for c in survivors if not c.get("qrz_verified")]
    if unver:
        print("\n--- Never QRZ-verified (silent keys / expired calls are fine; misreads are not) ---")
        for c in unver:
            print(f"  {c['id']}")

    if not args.apply:
        print(f"\nDRY RUN ONLY. Result would be {len(survivors)} cards. "
              f"Re-run with --apply to execute.")
        return

    # ---------- apply ----------
    def files_of(c):
        return [out / p for p in (c.get("front"), c.get("back"), c.get("thumb")) if p]

    for c in remove_dupe:
        for f in files_of(c):
            f.unlink(missing_ok=True)
    pulled_log = []
    for c, why in to_review:
        for f in files_of(c):
            if f.exists():
                shutil.move(str(f), review_dir / f.name)
        pulled_log.append({"id": c["id"], "reason": why,
                           "original_front": c.get("original_front"),
                           "original_back": c.get("original_back")})
    if pulled_log:
        pulled_path = review_dir / "pulled.json"
        existing = []
        if pulled_path.exists():
            existing = json.loads(pulled_path.read_text(encoding="utf-8"))
        pulled_path.write_text(json.dumps(existing + pulled_log, indent=2),
                               encoding="utf-8")
        print(f"\nOriginal scan filenames for pulled entries recorded in "
              f"review\\pulled.json (originals live in processed\\)")

    m["cards"] = survivors
    from datetime import datetime
    m["generated"] = datetime.now().isoformat(timespec="seconds")
    manifest_path.write_text(json.dumps(m, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nAPPLIED. Manifest now has {len(survivors)} cards. "
          f"{len(remove_dupe)} duplicate sets deleted, "
          f"{len(to_review)} entries moved to review\\.")


if __name__ == "__main__":
    main()
