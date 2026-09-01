#!/usr/bin/env python3
"""Fetch the mosque's timetable and publish it as prayers.json.

Providers are pluggable so a new mosque can be added by writing a preset in
mosques/, without touching this file.

The failure that matters is not an error: it is silently publishing
yesterday's times or an empty payload and leaving every device to act on it.
So: retries with backoff, request timeouts, schema validation, and a date
check before anything is written.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - Python < 3.9
    print("python 3.9+ required (for zoneinfo)", file=sys.stderr)
    sys.exit(2)

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "prayers.json"
TIMEOUT_S = 25
RETRIES = 4
UA = "prayer-times-sync/2"


def scrub(text, secrets):
    out = str(text)
    for s in secrets:
        if s:
            out = out.replace(s, "***")
    return out


def read_json(path: Path, default=None):
    try:
        with path.open(encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return default


def ymd_in(tz_name: str, offset_days: int = 0) -> str:
    d = datetime.now(ZoneInfo(tz_name)) + timedelta(days=offset_days)
    return d.strftime("%Y-%m-%d")


def hms_in(tz_name: str) -> str:
    return datetime.now(ZoneInfo(tz_name)).strftime("%H:%M:%S")


def get_json(url: str, headers: dict, secrets: list) -> dict:
    last_err = None
    for attempt in range(1, RETRIES + 1):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
                status = getattr(resp, "status", 200)
                if status < 200 or status >= 300:
                    raise RuntimeError(f"HTTP {status}")
                return json.loads(resp.read().decode("utf-8", errors="replace"))
        except Exception as exc:  # noqa: BLE001 - any failure is just a retry
            last_err = exc
            print(f"  attempt {attempt}/{RETRIES} failed: {scrub(exc, secrets)}", file=sys.stderr)
            if attempt < RETRIES:
                time.sleep(attempt * 3)
    raise RuntimeError(f"giving up: {scrub(last_err or 'unknown', secrets)}")


def fetch_masjidal(tz: str, preset: dict):
    api_key = os.environ.get("PRAYER_API_KEY", "")
    base_url = os.environ.get("PRAYER_API_BASE_URL", "")
    if not api_key or not base_url:
        raise RuntimeError("PRAYER_API_KEY / PRAYER_API_BASE_URL are not set")
    url = f"{base_url}&day={ymd_in(tz)}&time={hms_in(tz)}"
    secrets = [api_key, base_url]
    payload = get_json(url, {
        "accept": "*/*",
        "addin-api-key": api_key,
        "user-agent": UA,
    }, secrets)
    return payload, secrets


def fetch_aladhan(tz: str, preset: dict):
    loc = preset.get("location", {}) or {}
    lat = loc.get("latitude")
    lng = loc.get("longitude")
    if lat is None or lng is None:
        raise RuntimeError("aladhan provider needs location.latitude/longitude")
    calc = preset.get("calculation", {}) or {}
    method_map = {"MWL": 3, "ISNA": 2, "EGYPT": 5, "MAKKAH": 4,
                  "KARACHI": 1, "TEHRAN": 7, "JAFARI": 0}
    method = method_map.get(str(calc.get("method", "ISNA")).upper(), 2)
    school = 1 if str(calc.get("asr", "standard")).lower() == "hanafi" else 0
    y, m, d = ymd_in(tz).split("-")
    url = (f"https://api.aladhan.com/v1/timings/{d}-{m}-{y}"
           f"?latitude={lat}&longitude={lng}&method={method}&school={school}")
    res = get_json(url, {"accept": "application/json"}, [])
    t = ((res or {}).get("data") or {}).get("timings")
    if not t:
        raise RuntimeError("aladhan returned no timings")

    def hm(v):
        return f"{str(v)[:5]}:00"

    # Reshape into the envelope the devices already understand.
    payload = {
        "data": {
            "name": preset.get("name") or preset.get("id"),
            "city": preset.get("city", ""),
            "prayers": None,
            "prayerOfDay": {
                "prayerDate": f"{ymd_in(tz)}T00:00:00",
                "singlePrayers": [
                    {"prayerName": "Fajr",    "prayerBegins": hm(t["Fajr"]),    "prayerAdhan": hm(t["Fajr"]),    "prayerIqamah": None},
                    {"prayerName": "Sunrise", "prayerBegins": None,             "prayerAdhan": hm(t["Sunrise"]), "prayerIqamah": None},
                    {"prayerName": "Dhuhr",   "prayerBegins": hm(t["Dhuhr"]),   "prayerAdhan": hm(t["Dhuhr"]),   "prayerIqamah": None},
                    {"prayerName": "Asr",     "prayerBegins": hm(t["Asr"]),     "prayerAdhan": hm(t["Asr"]),     "prayerIqamah": None},
                    {"prayerName": "Sunset",  "prayerBegins": None,             "prayerAdhan": hm(t["Sunset"]),  "prayerIqamah": None},
                    {"prayerName": "Maghrib", "prayerBegins": hm(t["Maghrib"]), "prayerAdhan": hm(t["Maghrib"]), "prayerIqamah": None},
                    {"prayerName": "Isha",    "prayerBegins": hm(t["Isha"]),    "prayerAdhan": hm(t["Isha"]),    "prayerIqamah": None},
                ],
            },
        },
        "status": "ALADHAN",
        "message": "Success",
        "source": "aladhan",
    }
    return payload, []


REQUIRED = ("Fajr", "Dhuhr", "Asr", "Maghrib", "Isha")


def _valid_time(v) -> bool:
    if not isinstance(v, str):
        return False
    parts = v.split(":")
    if len(parts) not in (2, 3):
        return False
    return all(p.isdigit() for p in parts)


def validate(payload: dict, tz: str):
    day = ((payload or {}).get("data") or {}).get("prayerOfDay")
    if not day:
        raise RuntimeError("payload has no data.prayerOfDay")

    lst = day.get("singlePrayers")
    if not isinstance(lst, list) or not lst:
        raise RuntimeError("singlePrayers is empty")

    date_str = str(day.get("prayerDate") or "")[:10]
    allowed = [ymd_in(tz, -1), ymd_in(tz), ymd_in(tz, 1)]
    if date_str not in allowed:
        raise RuntimeError(
            f"timetable is for {date_str or '(none)'}, expected one of {', '.join(allowed)}"
        )
    if date_str != ymd_in(tz):
        print(f"  note: payload date {date_str} is not today ({ymd_in(tz)})", file=sys.stderr)

    by_name = {e.get("prayerName"): e for e in lst if isinstance(e, dict)}
    missing = [n for n in REQUIRED if n not in by_name]
    if missing:
        raise RuntimeError(f"missing prayers: {', '.join(missing)}")

    for n in REQUIRED:
        p = by_name[n]
        anchors = [p.get("prayerAdhan"), p.get("prayerBegins"), p.get("prayerIqamah")]
        if not any(_valid_time(a) for a in anchors):
            raise RuntimeError(
                f"{n} has no usable time (adhan/begins/iqamah all absent or malformed)"
            )

    return date_str, len(lst)


PROVIDERS = {"masjidal": fetch_masjidal, "aladhan": fetch_aladhan}


def main() -> int:
    config = read_json(ROOT / "config.json", {}) or {}
    mosque_id = os.environ.get("PRAYER_MOSQUE") or config.get("mosque") or "masjid-el-noor"
    preset = read_json(ROOT / "mosques" / f"{mosque_id}.json", {}) or {}
    tz = config.get("timezone") or preset.get("timezone") or "UTC"
    provider = (preset.get("timetable") or {}).get("provider", "masjidal")

    print(f"mosque={mosque_id} tz={tz} provider={provider} today={ymd_in(tz)}")

    fn = PROVIDERS.get(provider)
    if fn is None:
        raise RuntimeError(f"unknown timetable provider '{provider}'")

    payload, _secrets = fn(tz, preset)
    date_str, count = validate(payload, tz)
    print(f"validated: {count} entries for {date_str}")

    nxt = json.dumps(payload, indent=2) + "\n"
    prev = OUT.read_text(encoding="utf-8") if OUT.exists() else ""
    if prev == nxt:
        print("unchanged")
        return 0

    # Write via a temp file so an interrupted run cannot leave a truncated
    # prayers.json for every device to download.
    tmp = OUT.parent / f"{OUT.name}.tmp"
    tmp.write_text(nxt, encoding="utf-8")
    tmp.replace(OUT)
    print(f"wrote prayers.json for {date_str}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001
        print(f"fatal: {exc}", file=sys.stderr)
        sys.exit(1)
