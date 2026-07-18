import json, re
from pathlib import Path
from collections import Counter

m = json.loads(Path("data/cards.json").read_text(encoding="utf-8"))
cards = m["cards"]

print(f"=== WAVE REPORT v2 ===")
print(f"Total cards in manifest: {len(cards)}")

no_name = [c["id"] for c in cards if not c.get("name")]
no_log = [c["id"] for c in cards if "log" not in str(c.get("source", ""))]
no_qrz = [c["id"] for c in cards if not c.get("qrz_verified")]
low_conf = [c["id"] for c in cards if c.get("confidence") == "low"]

def show(label, items, cap=20):
    print(f"\n{label}: {len(items)}")
    for i in items[:cap]:
        print(f"  {i}")
    if len(items) > cap:
        print(f"  ...and {len(items)-cap} more")

show("Missing name", no_name)
show("Not log-matched", no_log, cap=30)
show("Not QRZ verified", no_qrz)
show("Low confidence", low_conf)

# State tally from coordinates (US cards only)
try:
    import reverse_geocoder as rg
    us = [c for c in cards if c.get("country") == "United States"
          and c.get("lat") and c.get("lon")]
    results = rg.search([(c["lat"], c["lon"]) for c in us], mode=1)
    states = Counter(r["admin1"] for r in results)
    ABBR = {"Alabama":"AL","Alaska":"AK","Arizona":"AZ","Arkansas":"AR","California":"CA",
    "Colorado":"CO","Connecticut":"CT","Delaware":"DE","Florida":"FL","Georgia":"GA",
    "Hawaii":"HI","Idaho":"ID","Illinois":"IL","Indiana":"IN","Iowa":"IA","Kansas":"KS",
    "Kentucky":"KY","Louisiana":"LA","Maine":"ME","Maryland":"MD","Massachusetts":"MA",
    "Michigan":"MI","Minnesota":"MN","Mississippi":"MS","Missouri":"MO","Montana":"MT",
    "Nebraska":"NE","Nevada":"NV","New Hampshire":"NH","New Jersey":"NJ","New Mexico":"NM",
    "New York":"NY","North Carolina":"NC","North Dakota":"ND","Ohio":"OH","Oklahoma":"OK",
    "Oregon":"OR","Pennsylvania":"PA","Rhode Island":"RI","South Carolina":"SC",
    "South Dakota":"SD","Tennessee":"TN","Texas":"TX","Utah":"UT","Vermont":"VT",
    "Virginia":"VA","Washington":"WA","West Virginia":"WV","Wisconsin":"WI","Wyoming":"WY"}
    got = {ABBR[s] for s in states if s in ABBR}
    missing = sorted(set(ABBR.values()) - got)
    print(f"\nUS cards: {len(us)}")
    print(f"States confirmed: {len(got)} of 50")
    print(f"Missing: {', '.join(missing) if missing else 'NONE - clean sweep'}")
    top = states.most_common(5)
    print("Top states: " + ", ".join(f"{ABBR.get(s,s)} x{n}" for s, n in top))
except ImportError:
    print("\n(reverse_geocoder not installed - state tally skipped)")

# DX tally as a bonus
countries = Counter(c.get("country") for c in cards if c.get("country"))
dx = [(k, v) for k, v in countries.most_common() if k != "United States"]
print(f"\nDX entities: {len(dx)}")
for k, v in dx[:15]:
    print(f"  {k} x{v}")
