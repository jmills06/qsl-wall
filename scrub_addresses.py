import json, re
from pathlib import Path

p = Path("data/cards.json")
m = json.loads(p.read_text(encoding="utf-8"))
pat = re.compile(
    r"[^.]*\b(\d+\s+\w[\w\s]*\b(St|Street|Rd|Road|Ave|Avenue|Dr|Drive|Ln|Lane|Ct|Court|Way|Blvd)\b|P\.?O\.?\s*Box\s*\d+)[^.]*\.?",
    re.IGNORECASE)
fixed = 0
for c in m["cards"]:
    n = c.get("notes") or ""
    new = re.sub(r"\s{2,}", " ", pat.sub("", n)).strip(" .")
    if new != n.strip(" ."):
        print("cleaned", c["id"])
        c["notes"] = new
        fixed += 1
p.write_text(json.dumps(m, indent=2, ensure_ascii=False), encoding="utf-8")
print(fixed, "notes cleaned")
