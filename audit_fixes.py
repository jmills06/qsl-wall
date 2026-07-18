"""
audit_fixes.py -- Apply flags from the review.html audit pass (2026-07-18).

Rotations, front/back swaps, thumb regeneration, and two removals.
Manifest edit only, NO processor. Run from repo root:

    py audit_fixes.py

ABORT PHASE (no changes until every check passes):
  - manifest has exactly 571 cards
  - every flagged stem matches exactly one manifest entry
  - every file to be touched exists
  - swap-flagged cards actually have both front and back
APPLY PHASE:
  - 180 rotations (front rotations also rotate the thumb)
  - 90-degree rotations, cw = clockwise fix, ccw = counterclockwise fix
  - front/back swaps, thumb regenerated from the new front
  - KO4SXH 2023-09-22 and N5WGA removed, files to mystery\\
  - manifest written via json.dump, count verified (571 -> 569)

W8FO is EXCLUDED from swaps by default: its "back" is a W8EO duplicate,
so swapping would show the wrong card as W8FO's face. Pending the search
for a real W8FO scan in processed\\. Set INCLUDE_W8FO_SWAP = True to
override.

Delete this script before the next push.
"""

import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MANIFEST = ROOT / "data" / "cards.json"
MYSTERY = ROOT / "mystery"

EXPECT_BEFORE = 571
EXPECT_AFTER = 569
JPEG_QUALITY = 92

INCLUDE_W8FO_SWAP = False

# stem -> list of issues (from review.html export)
FLAGS = {
    "AB5HN_2023-11-11_20m_SSB": ["front_rotate_ccw90"],
    "AC4HI_2023-06-04_40m_SSB": ["back_rotate_ccw90"],
    "K0KST_2023-10-12_20m_SSB": ["front_rotate_ccw90"],
    "K0VWA_2023-12-24_10m_SSB": ["back_rotate_ccw90"],
    "K2G_2023-03-07_20m_SSB": ["front_upside_down"],
    "K4CAB_2024-03-07_20m_SSB": ["front_rotate_cw90"],
    "K4ELW_2024-03-12_20m_SSB": ["front_back_swapped"],
    "K7PPD_2024-03-12_20m_SSB": ["front_rotate_cw90"],
    "K8BSR_2024-04-13_20m_SSB": ["front_back_swapped"],
    "K8ERS_2023-10-22_20m_SSB": ["front_back_swapped"],
    "K8GO_2023-07-22_40m_SSB": ["front_upside_down"],
    "K9DY_2023-10-22_20m_SSB": ["front_upside_down"],
    "K9JMK_2025-01-18_20m_SSB": ["back_upside_down"],
    "K9OB_2024-01-19_40m_SSB": ["front_back_swapped"],
    "KA0KST_2024-06-20_20m_SSB": ["back_upside_down"],
    "KB2IPH_2023-12-19_20m_SSB": ["back_upside_down"],
    "KC1RRV_2024-03-03_40m_SSB": ["front_upside_down"],
    "KC3QLX_2024-03-01_17m_SSB": ["front_back_swapped"],
    "KC8-N4AR_2024-04-20_40m_SSB": ["front_rotate_ccw90"],
    "KC5RPF_2023-12-24_10m_SSB": ["back_rotate_ccw90"],
    "KD2YUY_2024-03-01_20m_SSB": ["front_upside_down"],
    "KD6GLN_2024-06-22_20m_SSB": ["front_back_swapped"],
    "KD7WQH_2023-10-24_20m_SSB": ["front_upside_down"],
    "KD8VRX_2024-02-09_40m_SSB": ["front_upside_down"],
    "KE4JT_2023-08-26_40m_SSB": ["back_upside_down"],
    "KE8OJV_2023-09-22_40m_SSB": ["front_upside_down"],
    "KE8SUP_2023-08-05_40m_SSB": ["back_upside_down"],
    "KG5YMI_2023-08-19_40m_SSB": ["front_rotate_cw90"],
    "KH2TJ_2024-03-01_15m_SSB": ["front_back_swapped"],
    "KI5GRD_2023-09-23_20m_SSB": ["front_upside_down"],
    "KI4JAV_2023-10-15_20m_SSB": ["front_upside_down"],
    "KI5GTR_2023-10-22_20m_SSB": ["front_upside_down"],
    "KI5UBT_2023-09-16_20m_SSB": ["front_back_swapped"],
    "KK9ZZK_2024-12-23_15m_SSB": ["front_rotate_cw90"],
    "KM4PPV_2023-12-29_40m_SSB": ["back_upside_down"],
    "KM6TFY_2024-06-22_20m_SSB": ["front_back_swapped"],
    "KN4SWS_2024-08-13_20m_SSB": ["front_upside_down"],
    "KN4ZVJ_2023-09-23_20m_SSB": ["front_upside_down"],
    "KO4FHS_2023-08-26_20m_SSB": ["front_rotate_cw90"],
    "KW4BGY_2024-01-01_20m_SSB": ["front_upside_down"],
    "KY4KP_2023-09-23_20m_SSB": ["front_back_swapped"],
    "KY4WW_2024-06-20_20m_SSB": ["front_upside_down"],
    "KZ4CP_2024-01-19_40m_SSB": ["front_rotate_ccw90"],
    "N1XXU_2023-08-13_20m_SSB": ["front_rotate_cw90"],
    "N1XXU_2023-08-19_40m_SSB": ["front_upside_down"],
    "N4BSD_2020-10-20_20m_SSB": ["back_upside_down", "front_upside_down"],
    "N4DH_2023-07-15_40m_SSB": ["front_rotate_ccw90"],
    "N4GAS_2024-01-20_40m_SSB": ["front_upside_down"],
    "N5VOF_2023-09-16_20m_SSB": ["front_back_swapped"],
    "N6BIS_2024-06-14_20m_SSB": ["front_rotate_ccw90"],
    "N6LY_2023-09-17_20m_SSB": ["front_back_swapped"],
    "N8APR_2023-12-30_40m_SSB": ["front_back_swapped"],
    "N7JIM_2024-03-07_20m_SSB": ["front_upside_down"],
    "N9KIY_2023-07-02_20m_SSB": ["front_rotate_cw90"],
    "N9VFR_2023-12-21_20m_SSB": ["front_back_swapped"],
    "NL7V_2023-10-22_20m_SSB": ["front_upside_down"],
    "NX8G_2023-07-15_40m_SSB": ["front_upside_down"],
    "W0HTH_2023-12-15_20m_SSB": ["front_upside_down"],
    "W1DED_2023-09-23_20m_SSB": ["front_back_swapped"],
    "W2ACY_2024-01-21_20m_SSB": ["back_rotate_ccw90"],
    "W3QLC_2023-10-15_20m_SSB": ["front_back_swapped"],
    "W4TWR_2024-01-21_20m_SSB": ["front_upside_down"],
    "W4ZZ_2023-10-12_20m_SSB": ["front_upside_down"],
    "W8FO_2024-06-08_2m_FM": ["front_back_swapped"],
    "W8GV_2024-06-20_20m_SSB": ["front_upside_down"],
    "W9AFB_2024-02-27_40m_SSB": ["front_back_swapped"],
    "W9FAA_2024-03-02_40m_SSB": ["back_upside_down"],
    "W9LG_2024-02-09_20m_SSB": ["front_rotate_ccw90"],
    "WA6URY_2023-09-23_20m_SSB": ["front_back_swapped"],
    "WB6EDK_2023-10-22_15m_SSB": ["front_back_swapped"],
    "WD5BPC_2024-06-14_20m_SSB": ["front_upside_down"],
    "WV4AS_2023-10-17_20m_SSB": ["front_rotate_cw90"],
    "WW5TX_2023-07-22_20m_SSB": ["front_rotate_ccw90"],
}

# stem -> reason (entry removed from manifest, files moved to mystery\)
REMOVALS = {
    "KO4SXH_2023-09-22_40m_SSB": "front is blank",
    "N5WGA_2024-03-31_15m_SSB": "front and back are different cards",
}

# PIL rotate() angle that FIXES each issue (positive = counterclockwise)
ROT_ANGLE = {
    "front_upside_down": 180, "back_upside_down": 180,
    "front_rotate_cw90": -90, "back_rotate_cw90": -90,
    "front_rotate_ccw90": 90, "back_rotate_ccw90": 90,
}


def die(msg):
    print(f"ABORT: {msg}")
    print("No changes were made.")
    sys.exit(1)


def stem_of(entry):
    if entry.get("front"):
        return Path(entry["front"]).name.replace("_front.jpg", "")
    return f"{entry.get('callsign')}_{entry.get('date') or 'unknown-date'}"


def main():
    if not MANIFEST.exists():
        die(f"manifest not found at {MANIFEST}")
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    cards = manifest["cards"]
    if len(cards) != EXPECT_BEFORE:
        die(f"expected {EXPECT_BEFORE} cards, found {len(cards)}")

    by_stem = {}
    for c in cards:
        by_stem.setdefault(stem_of(c), []).append(c)

    flags = dict(FLAGS)
    if not INCLUDE_W8FO_SWAP:
        skipped = flags.pop("W8FO_2024-06-08_2m_FM", None)
        if skipped:
            print("NOTE: W8FO swap skipped (INCLUDE_W8FO_SWAP = False); "
                  "pending processed\\ search for a real W8FO scan.")

    # ------------------------------ ABORT PHASE ------------------------------
    for stem in list(flags) + list(REMOVALS):
        hits = by_stem.get(stem, [])
        if len(hits) != 1:
            die(f"{stem}: expected exactly 1 manifest entry, "
                f"found {len(hits)}")

    for stem, issues in flags.items():
        entry = by_stem[stem][0]
        for issue in issues:
            side = "front" if issue.startswith("front") else "back"
            if issue == "front_back_swapped":
                if not entry.get("front") or not entry.get("back"):
                    die(f"{stem}: flagged swapped but missing front or back")
                continue
            p = entry.get(side)
            if not p:
                die(f"{stem}: issue {issue} but no {side} image in manifest")
            if not (ROOT / p).exists():
                die(f"missing file: {p}")
        if any(i.startswith("front") for i in issues):
            t = entry.get("thumb")
            if t and not (ROOT / t).exists():
                die(f"missing thumb: {t}")

    for stem in REMOVALS:
        entry = by_stem[stem][0]
        for key in ("front", "back", "thumb"):
            p = entry.get(key)
            if p and not (ROOT / p).exists():
                die(f"missing file for removal: {p}")

    print(f"All abort-phase checks passed. "
          f"{len(flags)} flagged cards, {len(REMOVALS)} removals. Applying...")

    # ------------------------------ APPLY PHASE ------------------------------
    from PIL import Image

    counts = {}

    def rotate_file(relpath, angle):
        full = ROOT / relpath
        img = Image.open(full)
        img.rotate(angle, expand=True).save(full, quality=JPEG_QUALITY)

    def regen_thumb(entry):
        t = entry.get("thumb")
        if not t:
            return
        old = Image.open(ROOT / t)
        box = max(old.size)
        new = Image.open(ROOT / entry["front"])
        new.thumbnail((box, box))
        new.save(ROOT / t, quality=JPEG_QUALITY)

    for stem, issues in flags.items():
        entry = by_stem[stem][0]
        for issue in issues:
            counts[issue] = counts.get(issue, 0) + 1
            if issue == "front_back_swapped":
                f, b = ROOT / entry["front"], ROOT / entry["back"]
                tmp = f.with_suffix(".swaptmp")
                f.rename(tmp)
                b.rename(f)
                tmp.rename(b)
                regen_thumb(entry)
                print(f"  swapped front/back + new thumb: {stem}")
            else:
                side = "front" if issue.startswith("front") else "back"
                angle = ROT_ANGLE[issue]
                rotate_file(entry[side], angle)
                if side == "front":
                    t = entry.get("thumb")
                    if t:
                        rotate_file(t, angle)
                print(f"  rotated {side} {angle:+d}: {stem}")

    MYSTERY.mkdir(exist_ok=True)
    for stem, reason in REMOVALS.items():
        entry = by_stem[stem][0]
        for key in ("front", "back", "thumb"):
            p = entry.get(key)
            if p:
                shutil.move(str(ROOT / p), str(MYSTERY / Path(p).name))
        cards.remove(entry)
        print(f"  removed to mystery ({reason}): {stem}")

    if len(cards) != EXPECT_AFTER:
        die(f"post-edit count is {len(cards)}, expected {EXPECT_AFTER}; "
            f"manifest NOT written")

    with open(MANIFEST, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)

    print(f"\nManifest written: {len(cards)} cards "
          f"({EXPECT_BEFORE} -> {EXPECT_AFTER}).")
    print("Fix summary:")
    for issue, n in sorted(counts.items()):
        print(f"  {issue}: {n}")
    print("Reload review.html (hard refresh) to verify, then commit + push.")


if __name__ == "__main__":
    main()
