# Adding QSL Cards in Waves

The runbook for growing the K8JKU QSL Wall, updated after the 182-scan wave.
Every wave: **scan → export → process → clean → review → push**. The board at
`https://jmills06.github.io/qsl-wall/` picks up pushed cards automatically
within the hour (immediately on a browser/DakBoard refresh).

---

## One-time prerequisites (per machine)

Already done on the work machine. Repeat when setting up another machine or
after rotating a password.

**Never put actual key or password values in this file or anywhere in the
repo - it is public. Values live only in environment variables.**

### 1. Clone and packages

```powershell
cd $HOME\projects
git clone https://github.com/jmills06/qsl-wall.git
py -m pip install pillow requests truststore
```

(`truststore` makes QRZ lookups work behind a corporate TLS proxy; harmless
on a home network.)

### 2. Credentials

Three environment variables, set permanently: Start menu > "Edit environment
variables for your account" > New (under *User variables*):

| Variable | Value | Where it comes from |
|---|---|---|
| `ANTHROPIC_API_KEY` | starts with `sk-ant-api...` | console.anthropic.com > Settings > API Keys. This is the **Console** (pay-as-you-go, needs credit under Billing), separate from a claude.ai subscription. A token starting with `sk-ant-oat...` is the wrong kind and will 401. |
| `QRZ_USERNAME` | K8JKU | QRZ login (XML Subscriber required) |
| `QRZ_PASSWORD` | the QRZ password | Same password as the `spothole-data` Actions secrets - rotate all places together |

### 3. Verify

Variables only appear in PowerShell windows opened **after** they were set
(sometimes not until sign-out/reboot). In a fresh window:

```powershell
$env:ANTHROPIC_API_KEY.Substring(0,14)   # must print sk-ant-api..., NOT sk-ant-oat...
$env:QRZ_USERNAME                        # must print the username
```

If stale in every new window, force it for the session:

```powershell
$env:ANTHROPIC_API_KEY = [System.Environment]::GetEnvironmentVariable("ANTHROPIC_API_KEY", "User")
```

### 4. Credential troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `API error 401: invalid x-api-key` | Wrong key type (`oat`) or truncated paste | Console key, use the copy button, re-set variable |
| API error about billing/credits | No Console credit | Add credit under Console > Billing |
| `QRZ enrichment disabled` at startup | QRZ vars not visible to this window | Fresh window or session-force command |
| QRZ `CERTIFICATE_VERIFY_FAILED: self-signed` | Corporate TLS inspection | `py -m pip install truststore` (auto-detected) |
| `QRZ login failed` | Password rotation didn't reach this machine | Update env var AND spothole-data secret |

---

## Step 1 - Scan the wave

Scan every card **front, back, front, back** into
`C:\Users\Z305394\Downloads\QSL Intake`. Pairing is by sorted filename.

- **Scan backs even when blank.** Blank backs are auto-detected and skipped;
  guessing breaks pairing for every card after.
- **Scan upright.** Rotate any sideways/upside-down scans in Photos before
  processing (the N9FAL lesson - the extractor can't read inverted cards).
- **THE BIG ONE - check the count before running:**

  ```powershell
  (Get-ChildItem "C:\Users\Z305394\Downloads\QSL Intake").Count
  ```

  Files must equal **cards x 2**. The 182-scan wave was actually ~90 cards
  double-exported by the scan app, which cost an afternoon of dedupe. If the
  count is roughly double what you scanned, the app exported twice: wipe the
  intake folder and re-export once.
- Any batch size works, but for a huge wave do a `--limit 10` first run to
  sanity-check pairing before committing the whole stack.

## Step 2 - Fresh log export

Overwrite `mylog.adi` in the repo folder with a current ADIF export. The log
is the source of truth: it turns card guesses into confirmed QSOs, supplies
exact grid coordinates, and catches misread callsigns.

## Step 3 - Process

```powershell
cd $HOME\projects\qsl-wall
py process_scans.py --incoming "C:\Users\Z305394\Downloads\QSL Intake" --out . --adif mylog.adi
```

Per card: Claude API extraction, log match, QRZ verify + enrich, rename to
`CALLSIGN_DATE_BAND_MODE`, resize, thumbnail, manifest append, originals to
`processed\`. Street addresses are scrubbed from notes automatically.

Flags:

| Flag | Purpose |
|---|---|
| `--limit N` | first N pairs only (testing a big wave) |
| `--dry-run --mock` | pairing preview, no API, no writes |
| `--answers file.json` | use human-provided readings for listed pairs (no API) |
| `--enrich-only` | add QRZ data to manifest entries that lack it |
| `--single-sided` | wave scanned fronts only |
| `--no-qrz` | skip QRZ this run |
| `--delay 2` | slow API calls if rate limited |

Safe to Ctrl+C and re-run: manifest saves per card, processed originals are
out of intake, duplicates get `-2` suffixes.

## Step 4 - Clean

**Standard step every wave, not just messy ones:**

```powershell
py cleanup_wave.py --out .          # read the report
py cleanup_wave.py --out . --apply  # if the report looks right
```

Removes exact-duplicate entries, pulls misreads (park refs as callsigns,
your own call, dateless/dupe-date twins, unverified calls shadowing verified
QSOs), and reports oddities for eyeballing. Pulled entries are recorded in
`review\pulled.json` with their original scan filenames.

## Step 5 - Handle review flags

Flagged pairs stay in intake (they re-run and re-bill until resolved):

- **Bad scan** → rescan into intake, delete the bad pair, re-run
- **Hard handwriting** → upload the pair images to Claude in the project
  chat; you get back an `answers.json` keyed to the filenames, then:
  `py process_scans.py --incoming ... --adif mylog.adi --answers answers.json`
- **Duplicate of a card already on the wall** → just delete the pair
- **Unidentifiable** (no callsign anywhere) → move to `mystery\`, search the
  log by date/location/equipment clues, resolve later via answers
- **Not in QRZ** (silent keys, expired, special events) → data may be fine;
  check the `.extracted.json` in `review\`

The wave is clean when **the intake folder is empty**.

## Step 6 - Verify and push

```powershell
py cleanup_wave.py --out .    # final dry-run: should find nothing new
Select-String -Path data\cards.json -Pattern "PO Box|P\.O\.|\d+\s+\w+\s+(St|Street|Rd|Road|Ave|Drive|Dr|Lane|Ln|Court|Ct)\b"
git add -A
git commit -m "Wave: NN cards"
git push
```

The address sweep is belt-and-suspenders (scrubbing is built into the
processor now); **if it prints anything, fix before pushing**. Verify after:

```
https://jmills06.github.io/qsl-wall/data/cards.json
https://jmills06.github.io/qsl-wall/
```

---

## Maintenance notes

- **Archive originals.** `processed\` holds every raw scan; move to
  long-term storage periodically. Never delete until archived - every fix
  this wave was possible because originals survived.
- **Costs.** One Haiku vision call per card, fractions of a cent; a 100-card
  wave is well under a dollar. Answers-file pairs cost nothing.
- **Repo size.** ~250KB per card with backs; hundreds of cards is roughly
  100MB, fine for Pages.
- **QRZ password lives in three places:** QRZ itself, this machine's env
  var, spothole-data's Actions secret. Rotate together.
- **Duplicate physical cards** (op sends two for one QSO): keep the better
  one, delete the other pair from intake.
- **`mystery\`** holds unidentified cards between waves; gitignored, revisit
  after log searches.

## The one command to remember

```powershell
cd $HOME\projects\qsl-wall; py process_scans.py --incoming "C:\Users\Z305394\Downloads\QSL Intake" --out . --adif mylog.adi; py cleanup_wave.py --out .
```

Then apply cleanup, clear review, push.
