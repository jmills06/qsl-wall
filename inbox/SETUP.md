# One-off Card Inbox: Setup

Upload single QSL cards from your phone or the scanner PC, get a review PR on
qsl-wall with the AI-read metadata, merge it, and the card is on the wall.

```
admin.html  ──upload──▶  qsl-inbox (private)  ──Action──▶  PR on qsl-wall  ──merge──▶  wall
 (phone/PC)              incoming/<id>/          crop, rotate,    "New QSL cards
                                                 Claude, QRZ log   for review"
```

- **Upload page:** `https://jmills06.github.io/qsl-wall/admin.html` (not linked
  from the wall, marked noindex). It does nothing without your token.
- **Private repo `qsl-inbox`:** holds the raw uploads and archives them
  forever under `processed/`. Card backs and your secrets never touch the
  public repo until you merge the PR.
- **Review:** everything waits in one rolling PR (`inbox/pending` branch).
  Upload three cards in a week and they all pile into the same PR, so there
  are never `cards.json` merge conflicts.

**Never put key or token values in this repo. It is public.**

---

## 1. Create the private inbox repo

1. github.com > New repository > name `qsl-inbox`, **Private**, and tick
   **Add a README** (the upload page can't commit to an empty repo).
2. Default branch must be `main`.
3. Add the workflow: create `.github/workflows/process-uploads.yml` in
   qsl-inbox with the contents of [`process-uploads.yml`](process-uploads.yml)
   from this folder.
4. Add a `.gitignore` in qsl-inbox containing `wall/` (the Action checks
   qsl-wall out into that folder).

## 2. Create two fine-grained tokens

GitHub > Settings > Developer settings > Personal access tokens >
**Fine-grained tokens** > Generate new token.

| Token | Repository access | Permissions | Used by |
|---|---|---|---|
| **Upload token** | Only `qsl-inbox` | Contents: Read and write. Actions: Read-only | You type it into admin.html on each device |
| **Wall token** | Only `qsl-wall` | Contents: Read and write. Pull requests: Read and write | The Action, as a secret |

Pick an expiry you're comfortable with (a year is fine). When one expires,
generate a new one and replace it in the same place.

## 3. Add secrets to qsl-inbox

qsl-inbox > Settings > Secrets and variables > Actions > New repository secret:

| Secret | Value |
|---|---|
| `ANTHROPIC_API_KEY` | Same Console key as your scanner machine (`sk-ant-api...`) |
| `QRZ_USERNAME` | K8JKU |
| `QRZ_PASSWORD` | QRZ password (now **four** places to rotate: QRZ, scanner PC, spothole-data, qsl-inbox) |
| `QRZ_LOGBOOK_KEY` | QRZ.com > My Logbook > Settings > **API Access Key** |
| `QSL_WALL_TOKEN` | The **Wall token** from step 2 |

## 4. Sign in on your phone

Open the upload page and enter `jmills06`, `qsl-inbox`, and the **Upload
token**. It's saved on that device only. Add the page to your home screen.
"Sign out" forgets the token.

---

## Using it

1. **Scanner or Phone photo?** Pick *Phone photo* when you took a picture of
   the card on a table, so it gets auto-cropped and flattened. Pick
   *Scanner* for scanner output, which is used as-is (same as a wave).
2. Choose the front, and the back if it has anything on it.
3. Rotation is checked by the AI. If a preview is clearly sideways, tap
   Rotate yourself. Doing that turns auto-rotate off for that card.
4. **Hints** are optional. Anything you fill in (callsign, date, band, mode)
   overrides what the AI reads, like an answers file. Use them for messy
   handwriting.
5. Upload. A couple of minutes later the Status box links the review PR.

**Photo tips:** put the card on a dark or contrasting surface (not white
paper on a white desk), fill most of the frame, and avoid glare.

## Reviewing the PR

Each card in the PR shows the images, the metadata, and checks:

- ✅ **matched your QRZ logbook**: date/band/mode came from your log, and the
  grid square gave exact coordinates. ➖ means the card's own data stands.
- ✅ **QRZ verified**: callsign exists, operator name filled in.
- ⚠️ flags: auto-crop unsure, auto-rotated, low confidence, or the image
  looks like another station's card already on the wall (a misread callsign?).
- ℹ️ notes: other cards you already have from the same station.

Then:
- **All good:** merge. Pages publishes it within a minute or two.
- **Small fix:** edit `data/cards.json` on the `inbox/pending` branch right
  in the PR (pencil icon), then merge. Your edits survive if another card
  arrives before you merge.
- **Bad crop or rotation:** close the PR and upload again (as *Scanner* to
  skip auto-crop, or rotate it yourself first). Closing drops every card
  in that PR, so re-upload any good ones too, or just fix the image file on
  the branch instead.

## Needs help

Two kinds of uploads are held in `review/` in qsl-inbox instead of going into
the PR, and show under **Needs help** on the upload page:

- **No callsign found:** type the callsign (and date if you know it) and tap
  **Retry**, or **Discard** it.
- **Duplicate:** a card with the same **callsign, date and band** is already
  on the wall or waiting in the open PR. The page shows what was read. Tap
  **Add anyway** (for example, a second card for the same QSO you want to
  keep) or **Discard**.

The date compared is the logged date when the QRZ logbook matched, so a
smudged date on the card still gets caught.

## Costs

Two small Claude calls per card at most (one more only when it re-reads
after rotating). Fractions of a cent per card. Actions minutes on a private
repo come out of the free monthly allowance, and each run takes about a minute.

## Troubleshooting

| Symptom | Fix |
|---|---|
| Sign in: "can't write to that repo" | Upload token needs Contents: Read and write on qsl-inbox |
| Sign in: "is public" | Make qsl-inbox private |
| Status says to give the token Actions read | Edit the Upload token, add Actions: Read-only |
| Upload works but no PR, run failed | qsl-inbox > Actions > open the run. Usually a missing/expired secret |
| `No log match (... AUTH ...)` in the PR | `QRZ_LOGBOOK_KEY` is wrong or the logbook key was regenerated |
| HEIC error on a desktop browser | Export as JPEG, or upload from the phone, which converts automatically |
| Run fails pushing to qsl-wall | `QSL_WALL_TOKEN` expired or lacks Pull requests permission |

## Files

| File | Where it runs |
|---|---|
| `admin.html` (repo root) | Your browser |
| `inbox/process-uploads.yml` | Copied into qsl-inbox as its workflow |
| `inbox/process_inbox.py` | The Action. Reuses `process_scans.py` for extraction, QRZ, naming, resizing |
| `inbox/cardcrop.py` | Auto-crop and flatten for phone photos |
| `inbox/qrz_logbook.py` | QRZ Logbook API (replaces the ADIF export for one-offs) |

Local test without any API calls:

```powershell
py inbox\process_inbox.py --inbox path\to\qsl-inbox --wall . --mock --report pr.md
```
