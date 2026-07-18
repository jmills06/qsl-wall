import json, shutil
from pathlib import Path

INTAKE = Path(r"C:\Users\Z305394\Downloads\QSL Intake")
mpath = Path("data/cards.json")
m = json.loads(mpath.read_text(encoding="utf-8"))
bad = {"k4lli_2023-10-24_2m_ssb", "n4jh_1923-12-21_20m_ssb"}
keep = []
for c in m["cards"]:
    if c["id"].lower() in bad:
        for k in ("front", "back", "thumb"):
            p = Path(c.get(k) or "")
            if str(p) and p.exists():
                p.unlink(); print("deleted", p)
        for k in ("original_front", "original_back"):
            name = c.get(k)
            if not name: continue
            hits = list(Path("processed").rglob(name))
            if hits:
                shutil.move(str(hits[0]), INTAKE / name); print("restored to intake:", name)
            else:
                print("WARNING could not find", name, "in processed\\")
    else:
        keep.append(c)
m["cards"] = keep
mpath.write_text(json.dumps(m, indent=2, ensure_ascii=False), encoding="utf-8")
print(f"{len(keep)} cards remain in manifest")
