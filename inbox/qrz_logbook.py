"""
QRZ Logbook API client: fetch your own logged QSOs with one station.

This replaces the ADIF export for one-off cards: the inbox workflow asks
QRZ for every QSO with the card's callsign, and process_scans.match_log
picks the one matching the card's date, exactly as it does with mylog.adi.

API key: QRZ.com > Logbook > Settings > "API Access Key" (the logbook key,
not the XML lookup password). Stored as the QRZ_LOGBOOK_KEY Actions secret.
"""

import html
import re
from urllib.parse import unquote_plus

import requests

LOGBOOK_URL = "https://logbook.qrz.com/api"
USER_AGENT = "k8jku-qsl-wall/1.0"


class LogbookError(RuntimeError):
    pass


def parse_fetch_response(text):
    """Returns (fields, adif_text). The response is name=value pairs joined
    by '&', with the ADIF last. The ADIF may be URL-encoded or HTML-entity
    encoded depending on the client path, so handle both."""
    head, sep, adif = text.partition("ADIF=")
    fields = {}
    for part in head.split("&"):
        if "=" in part:
            k, v = part.split("=", 1)
            fields[k.strip().upper()] = unquote_plus(v).strip()
    if sep:
        if re.search(r"%3C", adif, re.IGNORECASE):
            adif = unquote_plus(adif)
        adif = html.unescape(adif)
    return fields, adif


def fetch_qsos_adif(api_key, callsign, session=None):
    """(adif_text, note) for every QSO in the logbook with this callsign.
    adif_text is '' when there are none; note says why for the review PR."""
    http = session or requests
    r = http.post(LOGBOOK_URL,
                  data={"KEY": api_key, "ACTION": "FETCH", "OPTION": f"CALL:{callsign}"},
                  headers={"User-Agent": USER_AGENT}, timeout=60)
    r.raise_for_status()
    fields, adif = parse_fetch_response(r.text)
    result = fields.get("RESULT", "").upper()
    if result == "AUTH":
        raise LogbookError(f"QRZ Logbook rejected the API key: {fields.get('REASON', 'AUTH')}")
    if result == "OK":
        return adif, f"{fields.get('COUNT', '?')} QSO(s) in QRZ logbook"
    # FAIL is also what an empty result looks like; never worth stopping a
    # card over, the card data still stands and the PR shows the reason
    return "", f"no QRZ logbook match ({fields.get('REASON') or result or 'empty reply'})"
