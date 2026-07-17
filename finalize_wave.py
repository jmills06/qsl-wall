#!/usr/bin/env python3
"""
finalize_wave.py - apply the human-review verdicts from the card-reading session.
Run ONCE, before the answers reprocessing run.

    py finalize_wave.py --out .

Deletes manifest entries confirmed as misreads/contamination (their display
images are removed; originals remain in processed\\), and applies verified
date/mode/notes corrections to entries that stay.
"""
import argparse
import json
from datetime import datetime
from pathlib import Path

# Entries confirmed bad during human review - removed, images deleted:
DELETE_IDS = [
    "k9jku_2023-09-16_20m_unk",        # Josiah Russell's KI5UBT card back, misread + false QRZ match
    "kf0fik_2023-11-25_80m_ssb",       # cross-contaminated with W0NKA card; rebuilt via answers
    "kf0fik_2023-10-12_20m_ssb",       # cross-contaminated with W0NKA card; rebuilt via answers
    "kg8hz_2024-01-24_40m_cw",         # duplicate scan w/ misread date AND swapped faces; sibling kept+fixed
    "kd6gln_2024-09-10_12m_unk",       # franken-pair: AI6AQ envelope + KD6GLN front; rebuilt from originals
    "n0may_unknown-date_unk_unk",      # actually the back of KH2TJ's card (Todd's note)
    "n1bhd_unknown-date_40m_ssb",      # upside-down N9FAL card; rebuilt via answers
]

# Verified corrections applied to surviving entries:
EDITS = {
    "kg8hz_2019-01-24_40m_cw": {
        "id": "kg8hz_2024-01-19_40m_ssb",
        "date": "2024-01-19",
        "mode": "SSB",
        "notes": "Gerry Trimble Jr, New Boston MI. Card reads 24-1-19 (year first); "
                 "Feb 2024 postmark confirms. 23:25 UTC, 7.260 MHz, RST 59. Yaesu FT-891 "
                 "100W, Shark 40m whip. POTA K-6636. TNX for call & PTP. Work location "
                 "Ford EDC 1 pictured.",
    },
    "w8eo_unknown-date_unk_2-way": {
        "id": "w8eo_2024-06-08",
        "date": "2024-06-08",
        "notes": "Ed Oxer (ex-W8KCI), East Lansing MI, Ingham County. Memento card from "
                 "the Michigan Parks on the Air Meetup, 6/8/2024 (blank QSO table).",
    },
    "w8fo_2024-06-08_2m_2-way": {
        "notes": "QCWA Chapter 142 Northwest Ohio. Memento card from the Michigan Parks "
                 "on the Air Meetup, 6/8/2024.",
    },
    "k9jp_unknown-date_unk_unk": {
        "id": "k9jp_2024-06-08",
        "date": "2024-06-08",
        "notes": "Jeffrey Peters, Brownstown Township MI, Wayne County, grid EN82id. "
                 "POTA-design memento card from the Michigan Parks on the Air Meetup, "
                 "6/8/2024 (blank QSO table).",
    },
    "kh2tj_2024-03-01_15m_ssb": {
        "notes_append": "Also sent a follow-up postcard: Thanks for the POTA activation, "
                        "look for you on the next one! 72/73, Todd",
    },
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=".")
    args = ap.parse_args()
    out = Path(args.out)
    manifest_path = out / "data" / "cards.json"
    m = json.loads(manifest_path.read_text(encoding="utf-8"))

    deleted, edited = 0, 0
    kept = []
    for c in m["cards"]:
        if c["id"] in DELETE_IDS:
            for p in (c.get("front"), c.get("back"), c.get("thumb")):
                if p:
                    (out / p).unlink(missing_ok=True)
            print(f"deleted  {c['id']}")
            deleted += 1
            continue
        if c["id"] in EDITS:
            fix = EDITS[c["id"]]
            for k, v in fix.items():
                if k == "notes_append":
                    c["notes"] = ((c.get("notes") or "").rstrip(". ") + ". " + v).strip()
                else:
                    c[k] = v
            print(f"edited   {c['id']}")
            edited += 1
        kept.append(c)

    m["cards"] = kept
    m["generated"] = datetime.now().isoformat(timespec="seconds")
    manifest_path.write_text(json.dumps(m, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nDone. {deleted} deleted, {edited} edited, {len(kept)} cards remain.")


if __name__ == "__main__":
    main()
