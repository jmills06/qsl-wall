#!/usr/bin/env python3
"""
Inbox processor for one-off QSL cards uploaded from admin.html.

Runs inside the private qsl-inbox repo's GitHub Action (see inbox/SETUP.md).
Each upload is a folder the upload page committed:

    incoming/<upload-id>/front.jpg
    incoming/<upload-id>/back.jpg      (optional)
    incoming/<upload-id>/meta.json     source, hints, rotation lock

Per upload: auto-crop phone photos, Claude extraction plus orientation check
(re-reading after a rotation), upload-form hints override the AI, QRZ
Logbook API match (the log wins for date/band/mode, same as an ADIF run),
QRZ XML enrichment, then the same rename/resize/manifest steps as
process_scans.py, written into a qsl-wall checkout for a review PR.

Afterwards the upload folder moves to processed/<id>/ (or review/<id>/ when
no callsign could be found) along with result.json describing what happened.

    python inbox/process_inbox.py --inbox . --wall wall --report pr.md --summary summary.json
"""

import argparse
import json
import os
import re
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np
from PIL import Image

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

import process_scans as ps                       # noqa: E402
from cardcrop import find_and_flatten, rotate_clockwise   # noqa: E402
from qrz_logbook import fetch_qsos_adif          # noqa: E402

ORIENTATION_PROMPT = ps.EXTRACTION_PROMPT + """

Also include these two keys in the same JSON object:
  "front_rotation": degrees to rotate the FIRST image clockwise so its main printed text reads upright: 0, 90, 180, or 270
  "back_rotation": the same for the SECOND image, or 0 if there is only one image"""

HINT_FIELDS = ("callsign", "date", "band", "mode")

# Image similarity (difference hash, 256 bits) against cards already on the
# wall. Measured on the existing collection: the same card re-captured scores
# up to ~66, unrelated cards almost never under 88. Stations reuse one design
# for every QSO and some stock templates are shared between stations, so an
# image match only means "same design". Duplicates are decided by callsign,
# date and band; the image is used to spot a misread callsign.
IMAGE_MATCH = 70
DATE_SLOP_DAYS = 3      # same tolerance match_log uses for card-vs-log dates


def dhash(img, size=16):
    g = np.asarray(img.convert("L").resize((size + 1, size), Image.LANCZOS), dtype=np.int16)
    return (g[:, 1:] > g[:, :-1]).flatten()


def days_apart(a, b):
    """Days between two YYYY-MM-DD dates, or None if either is missing/bad."""
    try:
        return abs((datetime.strptime(a, "%Y-%m-%d") - datetime.strptime(b, "%Y-%m-%d")).days)
    except (TypeError, ValueError):
        return None


def log(msg):
    print(msg, flush=True)


def find_side(folder, name):
    for p in sorted(folder.iterdir()):
        if p.stem.lower() == name and p.suffix.lower() in ps.SCAN_EXTS:
            return p
    return None


def norm_rotation(value):
    try:
        v = int(round(float(value or 0) / 90.0)) * 90 % 360
    except (TypeError, ValueError):
        return 0
    return v


def mock_extract(front_img, back_img, api_key=None, prompt=None):
    data = ps.extract_mock(front_img, back_img)
    data.update(front_rotation=0, back_rotation=0)
    return data


class Pipeline:
    def __init__(self, args):
        self.args = args
        self.inbox = Path(args.inbox)
        self.wall = Path(args.wall)
        self.cards_dir = self.wall / "cards"
        self.thumbs_dir = self.cards_dir / "thumbs"
        self.thumbs_dir.mkdir(parents=True, exist_ok=True)
        self.manifest_path = self.wall / "data" / "cards.json"
        self.manifest = ps.load_manifest(self.manifest_path)

        self.api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        self.extract = mock_extract if args.mock else ps.extract_via_api
        if not args.mock and not self.api_key:
            sys.exit("ERROR: ANTHROPIC_API_KEY is not set")

        self.logbook_key = os.environ.get("QRZ_LOGBOOK_KEY", "")
        if not self.logbook_key:
            log("QRZ Logbook matching disabled (QRZ_LOGBOOK_KEY not set)")

        self.qrz = None
        user, pw = os.environ.get("QRZ_USERNAME", ""), os.environ.get("QRZ_PASSWORD", "")
        if user and pw:
            self.qrz = ps.QrzClient(user, pw, self.inbox / "qrz_cache.json")
        else:
            log("QRZ enrichment disabled (QRZ_USERNAME / QRZ_PASSWORD not set)")

        self._hashes = None   # card id -> dhash, built on first use

    # -- steps ---------------------------------------------------------------

    def carry_pending(self, carry_path):
        """Re-add cards from the still-open review PR so a rebuilt branch keeps
        them (including any fixes made by hand in that PR)."""
        pending = json.loads(Path(carry_path).read_text(encoding="utf-8"))
        known = {c["id"] for c in self.manifest["cards"]}
        carried = [c for c in pending.get("cards", []) if c["id"] not in known]
        self.manifest["cards"].extend(carried)
        if carried:
            ps.save_manifest(self.manifest, self.manifest_path)
        log(f"Carried {len(carried)} card(s) from the open review PR")
        return carried

    def card_hashes(self):
        if self._hashes is None:
            self._hashes = {}
            for c in self.manifest["cards"]:
                path = self.wall / (c.get("thumb") or "")
                if c.get("thumb") and path.exists():
                    try:
                        self._hashes[c["id"]] = dhash(Image.open(path))
                    except Exception:
                        pass
        return self._hashes

    def image_matches(self, img):
        """Cards on the wall (or in the open PR) whose front looks like img."""
        h = dhash(img)
        by_id = {c["id"]: c for c in self.manifest["cards"]}
        hits = [(int((h != v).sum()), by_id[cid]) for cid, v in self.card_hashes().items()
                if cid in by_id]
        return sorted([x for x in hits if x[0] <= IMAGE_MATCH], key=lambda x: x[0])

    def find_duplicate(self, callsign, date, band):
        """A card already on the wall (or in the open PR) for the same QSO:
        same callsign, date and band. Held for confirmation, not auto-added."""
        if not date:
            return None
        for c in self.manifest["cards"]:
            if c["callsign"] == callsign and c.get("date") == date and c.get("band") == band:
                return c
        return None

    def similar_cards(self, callsign, date, front, result):
        """Non-blocking notes: other QSOs with this station, and an image that
        matches a different station's card (a misread callsign?)."""
        others = [c for c in self.manifest["cards"] if c["callsign"] == callsign]
        if others:
            result.setdefault("info", []).append(
                f"You already have {len(others)} other card(s) from {callsign}: "
                + ", ".join(" ".join(x for x in (c.get("date") or "undated", c.get("band")) if x)
                            for c in others) + ".")
        for dist, c in self.image_matches(front):
            if c["callsign"] == callsign:
                continue
            gap = days_apart(c.get("date"), date)
            if gap is None or gap <= DATE_SLOP_DAYS:
                result["flags"].append(
                    f"The image looks like `{c['id']}` ({c['callsign']}, {c.get('date') or 'undated'}). "
                    "Check the callsign wasn't misread.")
                break

    def log_match(self, data, callsign, flags):
        if not self.logbook_key:
            return None
        adif, note = fetch_qsos_adif(self.logbook_key, callsign)
        if not adif and "/" in callsign:
            adif, note = fetch_qsos_adif(self.logbook_key, ps.qrz_base_call(callsign))
        qsos = [q for v in ps.parse_adif_text(adif).values() for q in v] if adif else []
        qso = ps.match_log(data, {callsign: qsos}) if qsos else None
        if qso is None:
            flags.append(f"No log match ({note}). Date/band/mode are from the card.")
        return qso

    def process_upload(self, folder):
        uid = folder.name
        meta_path = folder / "meta.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
        hints = {k: (v or "").strip() for k, v in (meta.get("hints") or {}).items()}
        source = meta.get("source", "scanner")
        flags = []

        front_path, back_path = find_side(folder, "front"), find_side(folder, "back")
        if not front_path:
            raise RuntimeError("no front image in upload")
        front = ps.load_image_oriented(front_path)
        back = ps.load_image_oriented(back_path) if back_path else None

        crop = {}
        if source == "photo":
            for side in ("front", "back"):
                img = front if side == "front" else back
                if img is None:
                    continue
                img, info = find_and_flatten(img)
                crop[side] = info["method"]
                if info["method"] != "quad":
                    flags.append(f"Auto-crop on the {side} was unsure ({info['method']}); check the edges.")
                if side == "front":
                    front = img
                else:
                    back = img

        if back is not None and ps.is_blank_scan(back):
            log("    back side is blank, skipping it")
            back = None

        rotation_locked = bool(meta.get("rotation_locked"))
        prompt = ps.EXTRACTION_PROMPT if rotation_locked else ORIENTATION_PROMPT
        data = self.extract(front, back, self.api_key, prompt)
        rot_f = norm_rotation(data.pop("front_rotation", 0))
        rot_b = norm_rotation(data.pop("back_rotation", 0)) if back is not None else 0
        rotated = {}
        if not rotation_locked and (rot_f or rot_b):
            front, back = rotate_clockwise(front, rot_f), (rotate_clockwise(back, rot_b) if back else None)
            rotated = {"front": rot_f, "back": rot_b}
            flags.append("Auto-rotated " + ", ".join(
                f"{s} {d}°" for s, d in rotated.items() if d) + ". Check it reads upright.")
            log(f"    rotated {rotated}, re-reading upright")
            data = self.extract(front, back, self.api_key, ps.EXTRACTION_PROMPT)

        extracted = dict(data)
        confidence = (data.get("confidence") or "low").lower()

        used_hints = {k: hints[k] for k in HINT_FIELDS if hints.get(k)}
        for k, v in used_hints.items():
            data[k] = v.upper() if k == "callsign" else v
        if used_hints.get("callsign"):
            confidence = "high"
        if hints.get("notes"):
            data["notes"] = " ".join(p for p in (hints["notes"], data.get("notes")) if p)

        callsign, confidence = ps.clean_callsign(data, confidence)
        result = {"upload_id": uid, "source": source, "crop": crop, "rotated": rotated,
                  "hints": used_hints, "extracted": extracted, "flags": flags}
        if not callsign:
            result["status"] = "review"
            result["reason"] = "No callsign could be read. Add it as a hint and retry."
            hits = self.image_matches(front)
            if hits and hits[0][0] <= 40:
                c = hits[0][1]
                result["reason"] += (f" It looks like {c['callsign']} ({c.get('date') or 'undated'}),"
                                     " which is already on the wall, so it may be a duplicate.")
            return result

        source_tag = "card"
        qso = self.log_match(data, callsign, flags)
        if qso:
            ps.apply_log_match(data, qso)
            confidence = "high"
            source_tag = "card+log"
            log(f"    matched log: {qso['callsign']} {qso['date']} {qso['band']} {qso['mode']}")

        qrz_verified, operator_name = False, None
        if self.qrz:
            try:
                rec = ps.qrz_lookup_with_fallback(self.qrz, callsign)
                if rec:
                    qrz_verified, operator_name = True, rec["name"]
                    ps.apply_qrz(data, rec)
                    source_tag += "+qrz"
                else:
                    flags.append(f"{callsign} not found in QRZ (misread, silent key, or special event?).")
                    if "log" not in source_tag:
                        confidence = "low"
            except Exception as e:
                msg = re.sub(r"password=[^&\s')]+", "password=***", str(e))
                flags.append(f"QRZ lookup failed: {msg}")

        if confidence == "low":
            flags.append("Low confidence in the callsign/date. Double-check them.")

        data["band"] = ps.normalize_band(data.get("band"))
        data["mode"] = ps.normalize_mode(data.get("mode"))
        dup = self.find_duplicate(callsign, data.get("date"), data.get("band"))
        if dup and not meta.get("allow_duplicate"):
            result.update(status="duplicate", duplicate_of=dup["id"], card=data,
                          reason=f"Looks like a duplicate of {dup['id']} (same callsign, "
                                 "date and band). Add it anyway, or discard it.")
            return result
        if dup:
            flags.append(f"Same QSO as `{dup['id']}`; you chose to add it anyway.")
        self.similar_cards(callsign, data.get("date"), front, result)

        stem = ps.card_stem(callsign, data, self.cards_dir, self.manifest)
        entry = ps.build_entry(stem, callsign, operator_name, data, back is not None,
                               confidence, qrz_verified, source_tag,
                               f"{uid}/{front_path.name}",
                               f"{uid}/{back_path.name}" if back_path else None)
        ps.save_card_images(front, back, stem, self.cards_dir, self.thumbs_dir)
        self.manifest["cards"].append(entry)
        self.card_hashes()[entry["id"]] = dhash(Image.open(self.wall / entry["thumb"]))
        ps.save_manifest(self.manifest, self.manifest_path)

        result.update(status="added", card_id=entry["id"], log_matched=bool(qso),
                      qrz_verified=qrz_verified)
        return result

    def run(self):
        carried = self.carry_pending(self.args.carry) if self.args.carry else []
        incoming = self.inbox / "incoming"
        uploads = sorted(p for p in incoming.iterdir() if p.is_dir()) if incoming.exists() else []
        log(f"{len(uploads)} upload(s) in the inbox")

        added, review, errors = [], [], []
        for i, folder in enumerate(uploads, 1):
            log(f"[{i}/{len(uploads)}] {folder.name}")
            try:
                result = self.process_upload(folder)
            except Exception as e:
                msg = re.sub(r"password=[^&\s')]+", "password=***", str(e))
                log(f"    ERROR: {msg}")
                result = {"upload_id": folder.name, "status": "review",
                          "reason": f"Processing error: {msg}", "flags": []}
                errors.append(folder.name)
            result["processed_at"] = datetime.now().isoformat(timespec="seconds")

            dest_root = self.inbox / ("processed" if result["status"] == "added" else "review")
            dest_root.mkdir(exist_ok=True)
            dest = dest_root / folder.name
            if dest.exists():
                shutil.rmtree(dest)
            shutil.move(str(folder), dest)
            (dest / "result.json").write_text(json.dumps(result, indent=2, ensure_ascii=False),
                                              encoding="utf-8")
            (added if result["status"] == "added" else review).append(result)
            log(f"    -> {result['status']}: {result.get('card_id') or result.get('reason')}")
            if not self.args.mock and i < len(uploads):
                time.sleep(1)

        summary = {"added": len(added), "carried": len(carried),
                   "review": [r["upload_id"] for r in review], "errors": errors}
        if self.args.summary:
            Path(self.args.summary).write_text(json.dumps(summary, indent=2), encoding="utf-8")
        if self.args.report:
            Path(self.args.report).write_text(self.report(carried, added, review), encoding="utf-8")
        log(f"\nDone. {len(added)} added, {len(review)} need help, {len(carried)} carried.")
        return summary

    # -- review PR body --------------------------------------------------------

    def report(self, carried, added, review):
        results = {r["card_id"]: r for r in added}
        for e in carried:   # earlier runs' notes live with the processed upload
            uid = (e.get("original_front") or "").split("/")[0]
            rp = self.inbox / "processed" / uid / "result.json"
            if uid and rp.exists():
                results.setdefault(e["id"], json.loads(rp.read_text(encoding="utf-8")))
        pending = carried + [c for c in self.manifest["cards"] if c["id"] in
                             {r["card_id"] for r in added}]
        base = self.args.image_base.rstrip("/") + "/"
        out = ["## New QSL cards for review", "",
               f"{len(pending)} card(s) waiting. Check each one, fix anything in "
               "`data/cards.json` on this branch if needed, then **merge to publish** "
               "them on the wall. Close the PR to throw them all away.", ""]
        for c in pending:
            r = results.get(c["id"], {})
            title = " · ".join(x for x in (c["callsign"], c.get("date"),
                                                " ".join(y for y in (c.get("band"), c.get("mode")) if y)) if x)
            out += [f"### {title}", ""]
            imgs = [f'<img src="{base}{c["front"]}" width="380">']
            if c.get("back"):
                imgs.append(f'<img src="{base}{c["back"]}" width="380">')
            out += [" ".join(imgs), "", "| Field | Value |", "|---|---|"]
            for k in ("name", "qth", "country", "park", "notes", "confidence", "source"):
                v = c.get(k)
                if v not in (None, ""):
                    out.append(f"| {k} | {str(v).replace('|', '/')} |")
            if c.get("lat") is not None:
                out.append(f"| coords | {c['lat']}, {c['lon']}"
                           f"{' (approx)' if c.get('coords_approx') else ''} |")
            out.append(f"| id | `{c['id']}` |")
            out.append("")
            checks = []
            checks.append("✅ matched your QRZ logbook" if r.get("log_matched") or "log" in c["source"]
                          else "➖ no logbook match")
            checks.append("✅ QRZ verified" if c.get("qrz_verified") else "⚠️ not QRZ verified")
            if r.get("hints"):
                checks.append("✏️ your hints used: " + ", ".join(r["hints"]))
            out.append(" · ".join(checks))
            for f in r.get("flags", []):
                out.append(f"- ⚠️ {f}")
            for f in r.get("info", []):
                out.append(f"- ℹ️ {f}")
            out.append("")
        if review:
            out += ["---", "", "### Needs your help (not in this PR)", ""]
            for r in review:
                out.append(f"- `{r['upload_id']}`: {r.get('reason')}")
            out += ["", "Open the upload page and handle these under **Needs help**.", ""]
        return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(description="Process admin.html uploads into a qsl-wall review branch")
    ap.add_argument("--inbox", required=True, help="qsl-inbox checkout (has incoming/)")
    ap.add_argument("--wall", required=True, help="qsl-wall checkout to write cards into")
    ap.add_argument("--carry", help="cards.json from the open review PR, to keep its cards")
    ap.add_argument("--report", help="write the review PR body (markdown) here")
    ap.add_argument("--summary", help="write a JSON summary here")
    ap.add_argument("--image-base",
                    default="https://raw.githubusercontent.com/jmills06/qsl-wall/inbox/pending/",
                    help="URL prefix for images in the PR body")
    ap.add_argument("--mock", action="store_true", help="no API calls (pipeline testing)")
    args = ap.parse_args()
    Pipeline(args).run()


if __name__ == "__main__":
    main()
