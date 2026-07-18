import json, re
from pathlib import Path
from collections import Counter

out = Path(".")
m = json.loads((out / "data" / "cards.json").read_text(encoding="utf-8"))
cards = m["cards"]

print(f"=== WAVE REPORT ===")
print(f"Total cards in manifest: {len(cards)}")

# New this wave = anything not in the 219 from wave 3.
# Uses processed_date if present, else falls back to counting.
dated = [c for c in cards if c.get("processed_date", "").startswith("2026-07")]
print(f"Cards processed this July: {len(dated)}")

# Field completeness
no_name = [c["id"] for c in cards if not c.get("operator_name")]
no_coords = [c["id"] for c in cards if not c.get("lat") or not c.get("lon")]
no_qso = [c["id"] for c in cards if not c.get("log_matched")]
unk_date = [c["id"] for c in cards if "unknown" in str(c.get("date", "")).lower()]
unk_mode = [c["id"] for c in cards if str(c.get("mode", "")).upper() in ("UNK", "", "NONE")]

def show(label, items, cap=15):
    print(f"\n{label}: {len(items)}")
    for i in items[:cap]:
        print(f"  {i}")
    if len(items) > cap:
        print(f"  ...and {len(items)-cap} more")

show("Missing operator name", no_name)
show("Missing coordinates", no_coords)
show("No log match", no_qso, cap=25)
show("Unknown date", unk_date)
show("Unknown mode", unk_mode)

# Suspect dates: anything before 2010 or in the future
sus = []
for c in cards:
    d = str(c.get("date", ""))
    yr = re.match(r"(\d{4})", d)
    if yr:
        y = int(yr.group(1))
        if y < 2010 or y > 2026:
            sus.append(f"{c['id']}  ({d})")
show("Suspect dates (<2010 or >2026)", sus)

# Duplicate suffixes
dupes = [c["id"] for c in cards if re.search(r"-\d+$", str(c.get("id", "")))]
show("Entries with -N dupe suffix", dupes)

# Review folder
rev = Path("review")
if rev.exists():
    flagged = sorted(set(f.stem.replace(".extracted", "") for f in rev.glob("*.extracted.json")))
    show("Review folder (flagged pairs)", flagged, cap=60)
else:
    print("\nReview folder: none")

# Intake leftovers
intake = Path(r"C:\Users\Z305394\Downloads\QSL Intake")
left = [f.name for f in intake.glob("*") if f.is_file()]
print(f"\nFiles still in intake: {len(left)}")

# State tally
states = Counter(c.get("state") for c in cards if c.get("state"))
missing = sorted(set("AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY".split()) - set(states))
print(f"\nStates confirmed: {len(states)} of 50")
print(f"Missing: {', '.join(missing) if missing else 'NONE - clean sweep'}")
