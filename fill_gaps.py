"""
fill_gaps.py -- Wave 4 gap fills for The Mailbag (qsl-wall).

Manifest edit + file renames + one rotation. NO processor. Run from repo root:

    py fill_gaps.py

What it does, in order:
  ABORT PHASE (no changes made until every check passes):
    - manifest exists and has exactly 573 cards
    - every target entry exists exactly once
    - no pre-existing N4JOM or N7CUR entries (dup guard before callsign fixes)
    - every file to be renamed exists; no destination filename already exists
    - NU8M notes contain the "12:12 UTC" typo
  APPLY PHASE:
    - K8GO 2023-10-01: mode SSB
    - KB5KU 2024-01-20 -> N4JOM: callsign, name, coords (Manning SC),
      mode SSB, front + thumb rotated 180 (card was scanned upside down)
    - N7GUR 2023-12-23 -> N7CUR: callsign, name, coords (Goodyear AZ,
      from grid DM33tl), mode SSB
    - VA3RMF 2024-01-19: mode SSB + note (card also confirms 2023-10-15 QSO)
    - W9DAE: date 2023-10-12, band 20m, mode SSB (Golden Ticket activation)
    - AB8LL: date 2023-09-22 (log + postmark elimination)
    - Meetup set (K9JP, W8FO, W8EO, W8SH): date 2024-06-08, band 2m, mode FM
    - KD2BIS: note only (collection card, no QSO data on card)
    - NU8M: notes typo 12:12 -> 17:12 UTC, drop "QNX QSL format used."
    - N7KOM: pulled from manifest, files moved to mystery\\ (no QSO yet,
      podcast guest keepsake)
    - all renamed image paths updated in the manifest
    - manifest written via json.dump, counts verified (573 -> 572)

Add this file to the close-out step 4 deletion list.
"""

import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MANIFEST = ROOT / "data" / "cards.json"
MYSTERY = ROOT / "mystery"

EXPECT_BEFORE = 573
EXPECT_AFTER = 572

# ---------------------------------------------------------------------------
# Edit table. Match on callsign, plus date where a callsign has siblings.
# Fields set to SET_* are applied; NOTE is appended to any existing notes.
# ---------------------------------------------------------------------------
UNCHANGED = object()

EDITS = [
    dict(match_call="K8GO", match_date="2023-10-01",
         mode="SSB"),
    dict(match_call="KB5KU", match_date="2024-01-20",
         callsign="N4JOM", name="James Mason",
         lat=33.6952, lon=-80.2110,
         mode="SSB", rotate_front=True,
         note="Callsign corrected from misread KB5KU (license-plate-letter "
              "card scanned upside down)."),
    dict(match_call="N7GUR", match_date="2023-12-23",
         callsign="N7CUR", name="Brian Keel",
         lat=33.479, lon=-112.375,
         mode="SSB",
         note="Callsign corrected from misread N7GUR (C/G confusion)."),
    dict(match_call="VA3RMF", match_date="2024-01-19",
         mode="SSB",
         note="Card also confirms a second QSO: 2023-10-15 16:12 UTC, 20m."),
    dict(match_call="W9DAE",
         date="2023-10-12", band="20m", mode="SSB",
         note="Date from log: 2023-10-12 21:32 UTC, Golden Ticket "
              "activation. Card carries no QSO data."),
    dict(match_call="AB8LL",
         date="2023-09-22",
         note="Date from log: 2023-09-22 22:57 UTC on 7.252 (KO4SXH/W3LTR "
              "evening pileup). Postmark mid-Oct 2023 consistent."),
    # Michigan POTA Meetup 2024-06-08 reconciliation
    dict(match_call="K9JP", date="2024-06-08", band="2m", mode="FM"),
    dict(match_call="W8FO", date="2024-06-08", band="2m", mode="FM"),
    dict(match_call="W8EO", date="2024-06-08", band="2m", mode="FM"),
    dict(match_call="W8SH", date="2024-06-08", band="2m", mode="FM"),
    dict(match_call="KD2BIS",
         note="Collection card; QSO table rubber-stamped 'For collection, "
              "good DX 73s'. No QSO details recorded on card."),
]

REMOVE_TO_MYSTERY = ["N7KOM"]  # keepsake, no QSO yet
DUP_GUARD = ["N4JOM", "N7CUR"]  # must not already exist before rename

NU8M_OLD = "12:12 UTC"
NU8M_NEW = "17:12 UTC"
NU8M_DROP = "QNX QSL format used."


def die(msg):
    print(f"ABORT: {msg}")
    print("No changes were made.")
    sys.exit(1)


def find_entries(cards, call, date=None):
    hits = [c for c in cards if (c.get("callsign") or "").upper() == call]
    if date is not None:
        hits = [c for c in hits if c.get("date") == date]
    return hits


def stem_of(entry):
    d = entry.get("date") or "unknown-date"
    b = entry.get("band") or "unk"
    m = entry.get("mode") or "UNK"
    return f"{entry['callsign']}_{d}_{b}_{m}"


def planned_renames(entry, new_entry_values):
    """Return list of (old_relpath, new_relpath) for front/back/thumb."""
    preview = dict(entry)
    preview.update(new_entry_values)
    new_stem = stem_of(preview)
    old_stem = None
    renames = []
    for key in ("front", "back", "thumb"):
        old = entry.get(key)
        if not old:
            continue
        base = Path(old).name
        if old_stem is None:
            # derive old stem from the front (or first available) filename
            old_stem = base.replace("_front.jpg", "").replace(
                "_back.jpg", "").replace(".jpg", "")
        new_base = base.replace(old_stem, new_stem)
        if new_base != base:
            new = str(Path(old).parent / new_base).replace("\\", "/")
            renames.append((key, old, new))
    return renames


def main():
    # ------------------------------ ABORT PHASE ------------------------------
    if not MANIFEST.exists():
        die(f"manifest not found at {MANIFEST}")
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    cards = manifest["cards"]

    if len(cards) != EXPECT_BEFORE:
        die(f"expected {EXPECT_BEFORE} cards, found {len(cards)}")

    for call in DUP_GUARD:
        if find_entries(cards, call):
            die(f"{call} already exists in manifest; callsign fix would "
                f"create a duplicate. Investigate before running.")

    plan = []  # (entry, values_to_set, note, renames, rotate_paths)
    for e in EDITS:
        hits = find_entries(cards, e["match_call"], e.get("match_date"))
        if len(hits) != 1:
            die(f"{e['match_call']} ({e.get('match_date') or 'any date'}): "
                f"expected exactly 1 entry, found {len(hits)}")
        entry = hits[0]
        values = {k: v for k, v in e.items()
                  if k in ("callsign", "name", "lat", "lon",
                           "date", "band", "mode")}
        renames = planned_renames(entry, values)
        for _, old, new in renames:
            if not (ROOT / old).exists():
                die(f"missing file: {old}")
            if (ROOT / new).exists():
                die(f"rename target already exists: {new}")
        rotate = []
        if e.get("rotate_front"):
            # rotate front and thumb (thumb derives from front)
            for key in ("front", "thumb"):
                p = entry.get(key)
                if p and not (ROOT / p).exists():
                    die(f"missing file for rotation: {p}")
                if p:
                    rotate.append((key, p))
        plan.append((entry, values, e.get("note"), renames, rotate))

    removals = []
    for call in REMOVE_TO_MYSTERY:
        hits = find_entries(cards, call)
        if len(hits) != 1:
            die(f"{call}: expected exactly 1 entry to remove, "
                f"found {len(hits)}")
        entry = hits[0]
        for key in ("front", "back", "thumb"):
            p = entry.get(key)
            if p and not (ROOT / p).exists():
                die(f"missing file for mystery move: {p}")
        removals.append(entry)

    nu8m_hits = find_entries(cards, "NU8M")
    if len(nu8m_hits) != 1:
        die(f"NU8M: expected exactly 1 entry, found {len(nu8m_hits)}")
    nu8m = nu8m_hits[0]
    if NU8M_OLD not in (nu8m.get("notes") or ""):
        die(f"NU8M notes do not contain '{NU8M_OLD}'; already fixed or "
            f"different wording. Inspect manually.")

    print("All abort-phase checks passed. Applying...")

    # ------------------------------ APPLY PHASE ------------------------------
    from PIL import Image  # imported here so abort phase needs no PIL

    for entry, values, note, renames, rotate in plan:
        for key, p in rotate:
            full = ROOT / p
            img = Image.open(full)
            img.rotate(180, expand=True).save(full)
            print(f"  rotated 180: {p}")
        for key, old, new in renames:
            (ROOT / old).rename(ROOT / new)
            entry[key] = new
            print(f"  renamed: {old} -> {new}")
        entry.update(values)
        if note:
            existing = (entry.get("notes") or "").strip()
            entry["notes"] = (existing + " " + note).strip()
        print(f"  updated: {entry['callsign']} "
              f"{entry.get('date')} {entry.get('band')} {entry.get('mode')}")

    MYSTERY.mkdir(exist_ok=True)
    for entry in removals:
        for key in ("front", "back", "thumb"):
            p = entry.get(key)
            if p:
                dest = MYSTERY / Path(p).name
                shutil.move(str(ROOT / p), str(dest))
                print(f"  moved to mystery: {p}")
        cards.remove(entry)
        print(f"  removed from manifest: {entry['callsign']}")

    notes = nu8m["notes"].replace(NU8M_OLD, NU8M_NEW)
    notes = notes.replace(NU8M_DROP, "").replace("  ", " ").strip()
    nu8m["notes"] = notes
    print(f"  NU8M notes fixed: {notes}")

    if len(cards) != EXPECT_AFTER:
        die(f"post-edit count is {len(cards)}, expected {EXPECT_AFTER}; "
            f"manifest NOT written")

    with open(MANIFEST, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)
    print(f"\nManifest written: {len(cards)} cards "
          f"({EXPECT_BEFORE} -> {EXPECT_AFTER}, N7KOM to mystery).")
    print("Done. Spot-check the board locally, then continue close-out "
          "steps 2-5.")


if __name__ == "__main__":
    main()
