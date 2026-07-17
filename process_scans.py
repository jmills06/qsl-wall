#!/usr/bin/env python3
"""
QSL Card Scan Processor - K8JKU
================================
Takes a folder of raw QSL card scans (front/back pairs with random scanner
filenames), extracts contact info using the Claude API vision endpoint,
optionally cross-references an ADIF log export as the source of truth,
then renames files, generates display + thumbnail derivatives, and builds
the cards.json manifest for the QSL Wall board.

Usage (Windows, py launcher):
    py process_scans.py --incoming scans/incoming --out . --mock --limit 3   (pipeline test, no API)
    py process_scans.py --incoming scans/incoming --out . --limit 5          (real test run, 5 pairs)
    py process_scans.py --incoming scans/incoming --out . --adif mylog.adi   (full run with log matching)

Requires:  py -m pip install pillow requests
API key:   set ANTHROPIC_API_KEY as an environment variable
           (PowerShell:  $env:ANTHROPIC_API_KEY = "sk-ant-...")

Folder layout produced under --out:
    cards/               display-size images (~1200px wide, JPEG q80)
    cards/thumbs/        grid thumbnails (~300px wide)
    data/cards.json      manifest for the board
    review/              pairs the extractor could not confidently process
    processed/           original scans moved here after success (never deleted)
"""

import argparse
import base64
import io
import json
import os
import re
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

import requests
from PIL import Image, ImageOps

# Corporate networks with TLS inspection re-sign HTTPS with their own CA.
# truststore (optional) makes Python trust the Windows certificate store,
# which includes such CAs.  Install with:  py -m pip install truststore
try:
    import truststore
    truststore.inject_into_ssl()
except ImportError:
    pass

# ----------------------------------------------------------------------------
# Config
# ----------------------------------------------------------------------------

API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"
MODEL = "claude-haiku-4-5-20251001"   # cheap + good at vision; switch to a
                                       # Sonnet model string if a card stumps it
DISPLAY_WIDTH = 1200                   # px, board never shows larger than this
THUMB_WIDTH = 300                      # px, wall grid thumbnails
JPEG_QUALITY = 80
API_IMAGE_MAX = 1400                   # px long edge sent to the API (token cost control)
SCAN_EXTS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp"}

EXTRACTION_PROMPT = """These two images are the front and back of a single QSL card \
(amateur radio contact confirmation card) received by station K8JKU in Clarkston, Michigan. \
The contact details may be printed or handwritten, and may appear on either side.

Extract the following and respond with ONLY a JSON object, no markdown fences, no commentary:

{
  "callsign": "the sending station's callsign (NOT K8JKU - K8JKU is the recipient)",
  "date": "QSO date in YYYY-MM-DD format, or null if not readable",
  "band": "band like 20m, 40m, 2m etc. Convert frequency in MHz to band if needed. null if unknown",
  "mode": "SSB, CW, FT8, FM etc. Treat USB/LSB/Phone as SSB. null if unknown",
  "qth": "the station's stated city/region, or null",
  "country": "the station's country, or null",
  "lat": approximate latitude of the station QTH as a number, or null,
  "lon": approximate longitude of the station QTH as a number, or null,
  "notes": "anything notable: park references, personal messages, special event info, club affiliations. NEVER include street addresses, PO boxes, or postal codes - this data is published publicly. Empty string if none",
  "confidence": "high, medium, or low - your confidence in the callsign and date specifically"
}

If multiple QSOs are listed on the card, use the first row and mention the others in notes. \
If you cannot find a callsign at all, set callsign to null and confidence to low."""

# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------

def log(msg):
    print(msg, flush=True)


def sanitize_callsign_for_filename(callsign):
    """EA8/DL1ABC -> EA8-DL1ABC etc. Safe for filenames and URLs."""
    return re.sub(r"[^A-Za-z0-9-]", "-", callsign.upper()).strip("-")


def load_image_oriented(path):
    img = Image.open(path)
    img = ImageOps.exif_transpose(img)
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    return img


def resize_to_width(img, width):
    if img.width <= width:
        return img.copy()
    h = round(img.height * width / img.width)
    return img.resize((width, h), Image.LANCZOS)


def image_to_api_b64(img):
    """Downscale and JPEG-encode for the API call to keep token cost low."""
    work = img.copy()
    long_edge = max(work.size)
    if long_edge > API_IMAGE_MAX:
        scale = API_IMAGE_MAX / long_edge
        work = work.resize((round(work.width * scale), round(work.height * scale)), Image.LANCZOS)
    buf = io.BytesIO()
    if work.mode != "RGB":
        work = work.convert("RGB")
    work.save(buf, "JPEG", quality=78)
    return base64.standard_b64encode(buf.getvalue()).decode("ascii")


def is_blank_scan(img):
    """Detect an empty scanner page (blank card back). Checks brightness and
    variance on a downscaled grayscale copy, ignoring the outer edge where
    scanner shadows live."""
    small = img.convert("L").resize((100, 64))
    px = list(small.crop((5, 4, 95, 60)).getdata())
    mean = sum(px) / len(px)
    var = sum((p - mean) ** 2 for p in px) / len(px)
    return mean > 200 and var < 250


def maidenhead_to_latlon(grid):
    """Convert a Maidenhead grid square (4 or 6 char) to approx center lat/lon."""
    try:
        g = grid.strip().upper()
        if len(g) < 4:
            return None, None
        lon = (ord(g[0]) - ord("A")) * 20 - 180
        lat = (ord(g[1]) - ord("A")) * 10 - 90
        lon += int(g[2]) * 2
        lat += int(g[3]) * 1
        if len(g) >= 6 and g[4].isalpha() and g[5].isalpha():
            lon += (ord(g[4]) - ord("A")) * (2 / 24) + (1 / 24)
            lat += (ord(g[5]) - ord("A")) * (1 / 24) + (0.5 / 24)
        else:
            lon += 1
            lat += 0.5
        return round(lat, 4), round(lon, 4)
    except Exception:
        return None, None


def normalize_band(value):
    if not value:
        return None
    v = str(value).strip().lower().replace(" ", "")
    m = re.match(r"^(\d+(?:\.\d+)?)(m|cm)$", v)
    if m:
        return v
    # frequency in MHz (or kHz, common on handwritten cards) -> band
    try:
        mhz = float(v.replace("mhz", "").replace("khz", ""))
    except ValueError:
        return v
    if mhz >= 1000:   # written in kHz, e.g. 14255
        mhz /= 1000
    bands = [
        (1.8, 2.0, "160m"), (3.5, 4.0, "80m"), (5.3, 5.41, "60m"),
        (7.0, 7.3, "40m"), (10.1, 10.15, "30m"), (14.0, 14.35, "20m"),
        (18.068, 18.168, "17m"), (21.0, 21.45, "15m"), (24.89, 24.99, "12m"),
        (28.0, 29.7, "10m"), (50.0, 54.0, "6m"), (144.0, 148.0, "2m"),
        (222.0, 225.0, "1.25m"), (420.0, 450.0, "70cm"),
    ]
    for lo, hi, name in bands:
        if lo <= mhz <= hi:
            return name
    return v


def normalize_mode(value):
    if not value:
        return None
    v = str(value).strip().upper()
    if v in ("USB", "LSB", "PHONE", "PH"):
        return "SSB"
    return v


# ----------------------------------------------------------------------------
# QRZ XML API enrichment (optional, needs XML Subscriber account)
# ----------------------------------------------------------------------------

QRZ_URL = "https://xmldata.qrz.com/xml/current/"
QRZ_NS = "{http://xmldata.qrz.com}"


class QrzClient:
    """Minimal QRZ XML API client with session handling and a local lookup
    cache (qrz_cache.json) so repeat runs never re-query the same callsign."""

    def __init__(self, username, password, cache_path):
        self.username = username
        self.password = password
        self.key = None
        self.cache_path = Path(cache_path)
        self.cache = {}
        if self.cache_path.exists():
            try:
                self.cache = json.loads(self.cache_path.read_text(encoding="utf-8"))
            except Exception:
                self.cache = {}

    def _save_cache(self):
        self.cache_path.write_text(
            json.dumps(self.cache, indent=1, ensure_ascii=False), encoding="utf-8")

    def _parse(self, xml_text):
        import xml.etree.ElementTree as ET
        root = ET.fromstring(xml_text)
        def section(tag):
            el = root.find(f"{QRZ_NS}{tag}")
            if el is None:
                el = root.find(tag)   # be lenient about namespace
            return el
        out = {"session": {}, "callsign": {}}
        for name, key in (("Session", "session"), ("Callsign", "callsign")):
            el = section(name)
            if el is not None:
                for child in el:
                    out[key][child.tag.replace(QRZ_NS, "")] = (child.text or "").strip()
        return out

    def _login(self):
        r = requests.get(QRZ_URL, params={
            "username": self.username, "password": self.password,
            "agent": "k8jku-qsl-wall-1.0"}, timeout=30)
        r.raise_for_status()
        parsed = self._parse(r.text)
        key = parsed["session"].get("Key")
        if not key:
            raise RuntimeError(f"QRZ login failed: {parsed['session'].get('Error', 'no key returned')}")
        self.key = key

    def lookup(self, callsign):
        """Returns a dict of station data, or None if not found in QRZ."""
        call = callsign.upper()
        if call in self.cache:
            return self.cache[call]          # may be None (cached not-found)
        if not self.key:
            self._login()
        for attempt in range(2):
            r = requests.get(QRZ_URL, params={"s": self.key, "callsign": call}, timeout=30)
            r.raise_for_status()
            parsed = self._parse(r.text)
            err = parsed["session"].get("Error", "")
            if "Session Timeout" in err or "Invalid session key" in err:
                self._login()
                continue
            if err and "Not found" in err:
                self.cache[call] = None
                self._save_cache()
                return None
            cs = parsed["callsign"]
            if not cs.get("call"):
                self.cache[call] = None
                self._save_cache()
                return None
            fname = cs.get("fname", "")
            lname = cs.get("name", "")
            result = {
                "name": " ".join(p for p in (fname, lname) if p) or None,
                "country": cs.get("country") or None,
                "state": cs.get("state") or None,
                "city": cs.get("addr2") or None,
                "grid": cs.get("grid") or None,
                "lat": float(cs["lat"]) if cs.get("lat") else None,
                "lon": float(cs["lon"]) if cs.get("lon") else None,
            }
            self.cache[call] = result
            self._save_cache()
            return result
        raise RuntimeError("QRZ session could not be re-established")


def qrz_base_call(callsign):
    """EA8/DL1ABC -> DL1ABC, HC1MD/2 -> HC1MD, K8JKU/P -> K8JKU.
    Picks the longest slash-segment that looks like an actual callsign."""
    parts = [p for p in callsign.upper().split("/") if p]
    candidates = [p for p in parts
                  if any(c.isdigit() for c in p) and any(c.isalpha() for c in p)
                  and len(p) >= 3]
    if not candidates:
        return callsign.upper()
    return max(candidates, key=len)




# ----------------------------------------------------------------------------
# ADIF log matching (optional source of truth)
# ----------------------------------------------------------------------------

ADIF_FIELD_RE = re.compile(r"<([A-Za-z_]+):(\d+)(?::[A-Za-z])?>", re.IGNORECASE)


def parse_adif(path):
    """Minimal ADIF parser. Returns dict: callsign -> list of QSO dicts."""
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    # strip header
    eoh = re.search(r"<eoh>", text, re.IGNORECASE)
    if eoh:
        text = text[eoh.end():]
    qsos = {}
    for record in re.split(r"<eor>", text, flags=re.IGNORECASE):
        fields = {}
        pos = 0
        while True:
            m = ADIF_FIELD_RE.search(record, pos)
            if not m:
                break
            name = m.group(1).upper()
            length = int(m.group(2))
            start = m.end()
            fields[name] = record[start:start + length].strip()
            pos = start + length
        call = fields.get("CALL", "").upper()
        if not call:
            continue
        raw_date = fields.get("QSO_DATE", "")
        date = None
        if re.match(r"^\d{8}$", raw_date):
            date = f"{raw_date[0:4]}-{raw_date[4:6]}-{raw_date[6:8]}"
        lat, lon = (None, None)
        if fields.get("GRIDSQUARE"):
            lat, lon = maidenhead_to_latlon(fields["GRIDSQUARE"])
        qso = {
            "callsign": call,
            "date": date,
            "band": normalize_band(fields.get("BAND") or fields.get("FREQ")),
            "mode": normalize_mode(fields.get("MODE")),
            "country": fields.get("COUNTRY") or None,
            "qth": fields.get("QTH") or fields.get("STATE") or None,
            "lat": lat,
            "lon": lon,
            "park": fields.get("SIG_INFO") or fields.get("POTA_REF") or None,
        }
        qsos.setdefault(call, []).append(qso)
    return qsos


def match_log(extracted, logbook):
    """Given extracted card data and the ADIF index, return the best QSO or None."""
    call = (extracted.get("callsign") or "").upper()
    if not call or call not in logbook:
        return None
    candidates = logbook[call]
    card_date = extracted.get("date")
    if card_date:
        try:
            target = datetime.strptime(card_date, "%Y-%m-%d")
            def dist(q):
                if not q["date"]:
                    return 10**9
                return abs((datetime.strptime(q["date"], "%Y-%m-%d") - target).days)
            best = min(candidates, key=dist)
            if dist(best) <= 3:   # scanner/handwriting date slop tolerance
                return best
        except ValueError:
            pass
    if len(candidates) == 1:
        return candidates[0]
    return None   # ambiguous: multiple QSOs, no usable date. Card data stands.


# ----------------------------------------------------------------------------
# Extraction (real API + mock)
# ----------------------------------------------------------------------------

def extract_via_api(front_img, back_img, api_key):
    content = [
        {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                     "data": image_to_api_b64(front_img)}},
    ]
    if back_img is not None:
        content.append(
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                         "data": image_to_api_b64(back_img)}})
    content.append({"type": "text", "text": EXTRACTION_PROMPT})

    body = {
        "model": MODEL,
        "max_tokens": 600,
        "messages": [{"role": "user", "content": content}],
    }
    headers = {
        "x-api-key": api_key,
        "anthropic-version": API_VERSION,
        "content-type": "application/json",
    }
    for attempt in range(4):
        resp = requests.post(API_URL, headers=headers, json=body, timeout=120)
        if resp.status_code == 200:
            data = resp.json()
            text = "".join(b.get("text", "") for b in data.get("content", []))
            text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
            return json.loads(text)
        if resp.status_code in (429, 500, 502, 503, 529):
            wait = 5 * (attempt + 1)
            log(f"    API {resp.status_code}, retrying in {wait}s...")
            time.sleep(wait)
            continue
        raise RuntimeError(f"API error {resp.status_code}: {resp.text[:300]}")
    raise RuntimeError("API retries exhausted")


MOCK_COUNTER = {"n": 0}

def extract_mock(front_img, back_img, api_key=None):
    """Fake extractor for pipeline testing without API calls."""
    MOCK_COUNTER["n"] += 1
    n = MOCK_COUNTER["n"]
    samples = [
        {"callsign": "JA1XYZ", "date": "2023-04-12", "band": "20m", "mode": "SSB",
         "qth": "Tokyo", "country": "Japan", "lat": 35.68, "lon": 139.69,
         "notes": "", "confidence": "high"},
        {"callsign": "EA8/DL1ABC", "date": "2024-01-05", "band": "15m", "mode": "CW",
         "qth": "Tenerife", "country": "Canary Islands", "lat": 28.29, "lon": -16.63,
         "notes": "Holiday operation", "confidence": "medium"},
        {"callsign": None, "date": None, "band": None, "mode": None,
         "qth": None, "country": None, "lat": None, "lon": None,
         "notes": "unreadable test card", "confidence": "low"},
        {"callsign": "W8KNX", "date": "2022-09-16", "band": "40m", "mode": "SSB",
         "qth": "Michigan", "country": "USA", "lat": 42.46, "lon": -83.65,
         "notes": "POTA K-1234", "confidence": "high"},
    ]
    return dict(samples[(n - 1) % len(samples)])


# ----------------------------------------------------------------------------
# Main pipeline
# ----------------------------------------------------------------------------

def find_pairs(incoming, single_sided=False):
    files = sorted(
        [p for p in Path(incoming).iterdir() if p.suffix.lower() in SCAN_EXTS],
        key=lambda p: p.name.lower(),
    )
    if single_sided:
        return [(f, None) for f in files]
    if len(files) % 2 != 0:
        log(f"WARNING: odd number of scans ({len(files)}). Last file has no pair "
            f"and will be skipped: {files[-1].name}")
        files = files[:-1]
    return [(files[i], files[i + 1]) for i in range(0, len(files), 2)]


def load_manifest(path):
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"generated": None, "cards": []}


def unique_stem(stem, cards_dir, manifest):
    existing_ids = {c["id"] for c in manifest["cards"]}
    candidate = stem
    i = 2
    while candidate.lower() in existing_ids or (cards_dir / f"{candidate}_front.jpg").exists():
        candidate = f"{stem}-{i}"
        i += 1
    return candidate


def process(args):
    out = Path(args.out)
    cards_dir = out / "cards"
    thumbs_dir = cards_dir / "thumbs"
    data_dir = out / "data"
    review_dir = out / "review"
    processed_dir = out / "processed"
    for d in (cards_dir, thumbs_dir, data_dir, review_dir, processed_dir):
        d.mkdir(parents=True, exist_ok=True)

    manifest_path = data_dir / "cards.json"
    manifest = load_manifest(manifest_path)

    logbook = {}
    if args.adif:
        logbook = parse_adif(args.adif)
        total_qsos = sum(len(v) for v in logbook.values())
        log(f"Loaded ADIF log: {len(logbook)} callsigns, {total_qsos} QSOs")

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    extractor = extract_mock if args.mock else extract_via_api
    if not args.mock and not api_key and not args.enrich_only:
        sys.exit("ERROR: ANTHROPIC_API_KEY environment variable is not set.\n"
                 'PowerShell:  $env:ANTHROPIC_API_KEY = "sk-ant-..."')

    qrz = None
    if not args.no_qrz:
        qrz_user = os.environ.get("QRZ_USERNAME", "")
        qrz_pass = os.environ.get("QRZ_PASSWORD", "")
        if qrz_user and qrz_pass:
            qrz = QrzClient(qrz_user, qrz_pass, out / "qrz_cache.json")
            log("QRZ enrichment enabled")
        else:
            log("QRZ enrichment disabled (set QRZ_USERNAME and QRZ_PASSWORD to enable)")

    if args.enrich_only:
        if not qrz:
            sys.exit("ERROR: --enrich-only needs QRZ_USERNAME and QRZ_PASSWORD set.")
        todo = [c for c in manifest["cards"] if not c.get("qrz_verified")]
        log(f"Enrich-only mode: {len(todo)} of {len(manifest['cards'])} cards "
            f"need QRZ data\n")
        done, missed = 0, 0
        for c in todo:
            try:
                rec = qrz.lookup(c["callsign"])
                if rec is None and "/" in c["callsign"]:
                    rec = qrz.lookup(qrz_base_call(c["callsign"]))
                if rec:
                    if not c.get("name"):
                        c["name"] = rec["name"]
                    if c.get("lat") is None and rec["lat"] is not None:
                        c["lat"], c["lon"] = rec["lat"], rec["lon"]
                        c["coords_approx"] = False
                    if not c.get("country"):
                        c["country"] = rec["country"]
                    if not c.get("qth"):
                        c["qth"] = ", ".join(
                            p for p in (rec["city"], rec["state"]) if p) or None
                    c["qrz_verified"] = True
                    if "+qrz" not in c["source"]:
                        c["source"] += "+qrz"
                    done += 1
                    log(f"  {c['callsign']:>10}  {rec['name'] or 'verified'}")
                else:
                    missed += 1
                    log(f"  {c['callsign']:>10}  not found in QRZ")
            except Exception as e:
                msg = re.sub(r"password=[^&\s')]+", "password=***", str(e))
                sys.exit(f"QRZ error, stopping: {msg}")
        manifest["generated"] = datetime.now().isoformat(timespec="seconds")
        manifest_path.write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
        log(f"\nDone. {done} enriched, {missed} not found. Manifest updated.")
        return

    pairs = find_pairs(args.incoming, args.single_sided)
    if args.limit:
        pairs = pairs[: args.limit]
    log(f"Found {len(pairs)} card pair(s) to process"
        f"{' [MOCK MODE - no API calls]' if args.mock else ''}"
        f"{' [DRY RUN - no files written]' if args.dry_run else ''}\n")

    ok, flagged, failed = 0, 0, 0

    for idx, (front_path, back_path) in enumerate(pairs, 1):
        pair_desc = front_path.name + (f" + {back_path.name}" if back_path else "")
        log(f"[{idx}/{len(pairs)}] {pair_desc}")
        try:
            front_img = load_image_oriented(front_path)
            back_img = load_image_oriented(back_path) if back_path else None

            if back_img is not None and is_blank_scan(back_img):
                log("    back side is blank, skipping it")
                back_img = None

            data = extractor(front_img, back_img, api_key)

            callsign = (data.get("callsign") or "").upper() or None
            confidence = (data.get("confidence") or "low").lower()
            source = "card"

            qso = match_log(data, logbook) if logbook else None
            if qso:
                # Log is the source of truth; card fills gaps
                for k in ("date", "band", "mode"):
                    data[k] = qso[k] or data.get(k)
                for k in ("country", "qth", "lat", "lon"):
                    data[k] = qso[k] if qso[k] is not None else data.get(k)
                data["park"] = qso.get("park")
                confidence = "high"
                source = "card+log"
                log(f"    matched log: {qso['callsign']} {qso['date']} {qso['band']} {qso['mode']}")

            qrz_verified = False
            operator_name = None
            if qrz and callsign:
                try:
                    rec = qrz.lookup(callsign)
                    if rec is None and "/" in callsign:
                        rec = qrz.lookup(qrz_base_call(callsign))
                    if rec:
                        qrz_verified = True
                        operator_name = rec["name"]
                        # QRZ fills anything the log didn't provide
                        if data.get("lat") is None and rec["lat"] is not None:
                            data["lat"], data["lon"] = rec["lat"], rec["lon"]
                        if not data.get("country"):
                            data["country"] = rec["country"]
                        if not data.get("qth"):
                            data["qth"] = ", ".join(
                                p for p in (rec["city"], rec["state"]) if p) or None
                        source += "+qrz"
                        log(f"    QRZ: {operator_name or 'verified'}")
                    else:
                        log(f"    QRZ: {callsign} not found - flagging for review "
                            f"(possible misread callsign)")
                        if confidence == "high" and source == "card":
                            confidence = "low"   # unverified card-only extraction
                except Exception as e:
                    msg = re.sub(r"password=[^&\s')]+", "password=***", str(e))
                    log(f"    QRZ lookup failed ({msg}), continuing without")

            if not callsign or confidence == "low":
                flagged += 1
                log(f"    FLAGGED for review (callsign={callsign}, confidence={confidence})")
                if not args.dry_run:
                    shutil.copy2(front_path, review_dir / front_path.name)
                    if back_path:
                        shutil.copy2(back_path, review_dir / back_path.name)
                    review_note = review_dir / (front_path.stem + ".extracted.json")
                    review_note.write_text(json.dumps(data, indent=2), encoding="utf-8")
                continue

            data["band"] = normalize_band(data.get("band"))
            data["mode"] = normalize_mode(data.get("mode"))

            safe_call = sanitize_callsign_for_filename(callsign)
            date_part = data.get("date") or "unknown-date"
            band_part = data.get("band") or "unk"
            mode_part = data.get("mode") or "UNK"
            stem = f"{safe_call}_{date_part}_{band_part}_{mode_part}"
            stem = unique_stem(stem, cards_dir, manifest)

            entry = {
                "id": stem.lower(),
                "callsign": callsign,
                "name": operator_name,
                "date": data.get("date"),
                "band": data.get("band"),
                "mode": data.get("mode"),
                "qth": data.get("qth"),
                "country": data.get("country"),
                "lat": data.get("lat"),
                "lon": data.get("lon"),
                "coords_approx": source == "card" and data.get("lat") is not None,
                "park": data.get("park"),
                "notes": data.get("notes") or "",
                "front": f"cards/{stem}_front.jpg",
                "back": f"cards/{stem}_back.jpg" if back_img else None,
                "thumb": f"cards/thumbs/{stem}.jpg",
                "confidence": confidence,
                "qrz_verified": qrz_verified,
                "source": source,
                "original_front": front_path.name,
                "original_back": back_path.name if back_path else None,
            }

            if args.dry_run:
                log(f"    would create: {stem}_front.jpg / _back.jpg / thumb  [{source}]")
            else:
                resize_to_width(front_img, DISPLAY_WIDTH).save(
                    cards_dir / f"{stem}_front.jpg", "JPEG", quality=JPEG_QUALITY, optimize=True)
                if back_img:
                    resize_to_width(back_img, DISPLAY_WIDTH).save(
                        cards_dir / f"{stem}_back.jpg", "JPEG", quality=JPEG_QUALITY, optimize=True)
                resize_to_width(front_img, THUMB_WIDTH).save(
                    thumbs_dir / f"{stem}.jpg", "JPEG", quality=JPEG_QUALITY, optimize=True)
                shutil.move(str(front_path), processed_dir / front_path.name)
                if back_path:
                    shutil.move(str(back_path), processed_dir / back_path.name)
                manifest["cards"].append(entry)
                manifest["generated"] = datetime.now().isoformat(timespec="seconds")
                manifest_path.write_text(
                    json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")

            ok += 1
            log(f"    OK -> {stem}  [{source}, {confidence}]")

        except Exception as e:
            failed += 1
            log(f"    ERROR: {e}")

        if not args.mock and idx < len(pairs):
            time.sleep(args.delay)

    log(f"\nDone. {ok} processed, {flagged} flagged for review, {failed} errors.")
    log(f"Manifest: {manifest_path}  ({len(manifest['cards'])} total cards)")
    if flagged:
        log(f"Review folder: {review_dir}  (fix these by hand or rescan)")


def main():
    ap = argparse.ArgumentParser(description="QSL card scan processor for the K8JKU QSL Wall")
    ap.add_argument("--incoming", help="folder of raw scans (front/back pairs, sorted order)")
    ap.add_argument("--out", default=".", help="output root (repo root). Default: current dir")
    ap.add_argument("--adif", help="optional ADIF log export to use as source of truth")
    ap.add_argument("--limit", type=int, help="process only the first N pairs (testing)")
    ap.add_argument("--mock", action="store_true", help="skip API, use fake data (pipeline testing)")
    ap.add_argument("--dry-run", action="store_true", help="show what would happen, write nothing")
    ap.add_argument("--single-sided", action="store_true", help="scans are fronts only, no backs")
    ap.add_argument("--no-qrz", action="store_true", help="skip QRZ enrichment even if creds are set")
    ap.add_argument("--enrich-only", action="store_true",
                    help="skip scanning; add QRZ data to existing manifest entries")
    ap.add_argument("--delay", type=float, default=1.0, help="seconds between API calls (default 1)")
    args = ap.parse_args()
    if args.enrich_only:
        args.incoming = args.incoming or "."
    elif not args.incoming:
        ap.error("--incoming is required (unless using --enrich-only)")
    process(args)


if __name__ == "__main__":
    main()
