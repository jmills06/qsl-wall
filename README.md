# QSL Card Scan Processor - K8JKU QSL Wall

Turns a folder of raw scanner output into renamed, resized, manifest-tracked
card images ready for the QSL Wall board.

## One-time setup (Windows)

```powershell
py -m pip install pillow requests
$env:ANTHROPIC_API_KEY = "sk-ant-your-key-here"
$env:QRZ_USERNAME = "your-qrz-username"
$env:QRZ_PASSWORD = "your-qrz-password"
```

The keys are set per PowerShell session. To make them permanent:
System Properties > Environment Variables > add each as a user variable.

**QRZ enrichment** (optional but recommended, uses your XML Subscriber
account, same credentials as the spothole-data Actions secrets): when the
QRZ variables are set, every extracted callsign is verified against QRZ.
- Found: operator name, registered coordinates, and QTH fill any gaps the
  card and log didn't cover, and the card is marked `qrz_verified`
- Not found: likely a misread callsign, so the card drops to review instead
  of entering the manifest with bad data
- Portable calls fall back automatically (HC1MD/2 retries as HC1MD)
- Lookups cache to `qrz_cache.json`, so reruns and repeat stations never
  re-query
Use `--no-qrz` to skip enrichment for a run.

## Scanning workflow

1. Scan cards in **front, back, front, back** order. The script pairs files
   by sorted filename, so sequential scanner names (IMG_0001, IMG_0002...) pair
   correctly automatically.
2. Drop the scans into `scans\incoming\`
3. Export a fresh ADIF from your logger (optional but strongly recommended,
   it becomes the source of truth for date/band/mode/park)

## Commands

```powershell
# Pipeline test, no API calls, fake data:
py process_scans.py --incoming scans\incoming --out . --mock

# Real test on the first 5 cards:
py process_scans.py --incoming scans\incoming --out . --adif mylog.adi --limit 5

# Full run:
py process_scans.py --incoming scans\incoming --out . --adif mylog.adi

# Cards scanned front-only:
py process_scans.py --incoming scans\incoming --out . --single-sided
```

## What it does per card pair

1. Sends both sides to the Claude API (Haiku model, downscaled copies to keep cost low)
2. Extracts callsign, date, band, mode, QTH, country, approx coordinates, notes
3. If an ADIF is provided and the callsign matches, the **log wins** for
   date/band/mode, grid square becomes exact coordinates, and POTA park refs
   are captured
4. Renames to `CALLSIGN_DATE_BAND_MODE_front.jpg` / `_back.jpg`
5. Writes display images (1200px, ~150-250KB) to `cards\` and thumbnails
   (300px) to `cards\thumbs\`
6. Appends the card to `data\cards.json` (saved after every card, safe to
   interrupt and resume)
7. Moves the originals to `processed\` (nothing is ever deleted)

Cards where the callsign can't be confidently read are copied to `review\`
along with whatever was extracted, and the originals stay in `incoming\` so
they retry on the next run (or you can rename/fix them by hand).

## Notes

- Blank back sides are detected automatically and skipped (not sent to the
  API, not stored). Scan every card front and back without worrying about it.
- Frequencies written in kHz (like "14255") or MHz (like "14.255") both
  normalize to the right band.

- Rerunning is safe: processed originals are out of `incoming`, and duplicate
  stems get a `-2` suffix instead of overwriting
- `--delay` (default 1s) spaces out API calls; raise it if you hit rate limits
- The model string is set at the top of the script (`MODEL`) if you want to
  swap in a stronger model for a stubborn batch
- Keep full-resolution originals archived outside the repo. Only `cards\`,
  `cards\thumbs\`, and `data\` get committed.
