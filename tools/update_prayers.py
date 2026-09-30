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


def get_text(url: str) -> str:
    last_err = None
    for attempt in range(1, RETRIES + 1):
        try:
            req = urllib.request.Request(url, headers={"user-agent": UA})
            with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            print(f"  attempt {attempt}/{RETRIES} failed: {exc}", file=sys.stderr)
            if attempt < RETRIES:
                time.sleep(attempt * 3)
    raise RuntimeError(f"giving up: {last_err or 'unknown'}")


def envelope(preset: dict, day: str, rows: list, source: str) -> dict:
    """Wrap (name, begins, adhan, iqamah) rows ("HH:MM" or None) in the
    envelope the devices already understand."""
    def hms(v):
        return f"{v}:00" if v else None
    return {
        "data": {
            "name": preset.get("name") or preset.get("id"),
            "city": preset.get("city", ""),
            "prayers": None,
            "prayerOfDay": {
                "prayerDate": f"{day}T00:00:00",
                "singlePrayers": [
                    {"prayerName": n, "prayerBegins": hms(b), "prayerAdhan": hms(a), "prayerIqamah": hms(i)}
                    for n, b, a, i in rows
                ],
            },
        },
        "mosqueId": preset.get("id"),
        "status": source.upper(),
        "message": "Success",
        "source": source,
    }


# --- GalaxyStream widget (www.galaxystream.com/apps/prayer-times) -----------
# A JS file with beginTime[] / adhanTime[] / iqamaTime[] arrays of "h:mm<span
# class='ampm'>AM</span>" in the order Fajr, Sunrise, Zawal, Zuhr, Asr,
# Maghrib, Isha, Jumah 1-3. It carries NO date: it is always "today" for the
# mosque, so fetch_galaxystream cross-checks Fajr against a calculation.
GS_ORDER = ["Fajr", "Sunrise", "Zawal", "Dhuhr", "Asr", "Maghrib", "Isha"]


def _to24(text: str):
    import re
    # Strip tags first: the markup's class name "ampm" would otherwise match AM.
    plain = re.sub(r"<[^>]*>", " ", text or "")
    m = re.search(r"(\d{1,2}):(\d{2})\s*(AM|PM)", plain, re.I)
    if not m:
        return None
    h, mnt, ap = int(m.group(1)), int(m.group(2)), m.group(3).upper()
    if ap == "PM" and h != 12:
        h += 12
    if ap == "AM" and h == 12:
        h = 0
    return f"{h:02d}:{mnt:02d}"


def parse_galaxystream(js: str) -> list:
    import re
    arrays = {}
    for arr in ("beginTime", "adhanTime", "iqamaTime"):
        vals = {}
        for m in re.finditer(re.escape(arr) + r'\[(\d+)\]\s*=\s*"(.*?)";', js, re.S):
            vals[int(m.group(1))] = _to24(m.group(2))
        arrays[arr] = vals
    if not arrays["beginTime"]:
        raise RuntimeError("galaxystream: no beginTime[] entries found")
    rows = []
    for i, name in enumerate(GS_ORDER):
        b, a, q = arrays["beginTime"].get(i), arrays["adhanTime"].get(i), arrays["iqamaTime"].get(i)
        if name in ("Sunrise", "Zawal"):
            rows.append((name, None, b, None))
        else:
            rows.append((name, b, a or b, q))
    return rows


def _minutes(hm: str) -> int:
    h, m = hm.split(":")[:2]
    return int(h) * 60 + int(m)


def fetch_galaxystream(tz: str, preset: dict):
    uid = (preset.get("timetable") or {}).get("uid")
    if uid is None:
        raise RuntimeError("galaxystream provider needs timetable.uid")
    js = get_text(f"https://www.galaxystream.com/apps/prayer-times/PrayerTimesJS.asp?uid={int(uid)}&displayType=small")
    rows = parse_galaxystream(js)
    # The widget has no date. Guard against a stale/yesterday page: its Fajr
    # "begins" must be within 10 minutes of a calculation for today.
    try:
        calc, _ = fetch_aladhan(tz, preset)
        calc_fajr = calc["data"]["prayerOfDay"]["singlePrayers"][0]["prayerBegins"][:5]
        gs_fajr = rows[0][1]
        if gs_fajr and abs(_minutes(gs_fajr) - _minutes(calc_fajr)) > 10:
            raise RuntimeError(f"galaxystream Fajr {gs_fajr} vs calculated {calc_fajr}: not today's table?")
    except RuntimeError as exc:
        if "not today's table" in str(exc):
            raise
        print(f"  note: could not cross-check the date ({exc})", file=sys.stderr)
    return envelope(preset, ymd_in(tz), rows, "galaxystream"), []


def masjid_url(base_url: str, masjid_id) -> str:
    """Point the (secret) portal URL at another masjid on the same platform."""
    if masjid_id is None:
        return base_url
    import re
    return re.sub(r"(?i)(masjidid=)\d+", lambda m: f"{m.group(1)}{int(masjid_id)}", base_url)


def fetch_masjidal(tz: str, preset: dict):
    api_key = os.environ.get("PRAYER_API_KEY", "")
    base_url = os.environ.get("PRAYER_API_BASE_URL", "")
    if not api_key or not base_url:
        raise RuntimeError("PRAYER_API_KEY / PRAYER_API_BASE_URL are not set")
    # The secret base URL names one masjid; any preset on the same platform
    # (ad-din / Masjidal portal) swaps in its own timetable.masjid_id.
    base_url_for = masjid_url(base_url, (preset.get("timetable") or {}).get("masjid_id"))
    url = f"{base_url_for}&day={ymd_in(tz)}&time={hms_in(tz)}"
    secrets = [api_key, base_url, base_url_for]
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


PROVIDERS = {"masjidal": fetch_masjidal, "aladhan": fetch_aladhan,
             "galaxystream": fetch_galaxystream}


def publish(mosque_id: str, config: dict, out: Path) -> str:
    """Fetch, validate and (atomically) write one mosque's timetable."""
    preset = read_json(ROOT / "mosques" / f"{mosque_id}.json", {}) or {}
    tz = (config.get("timezone") if mosque_id == config.get("mosque") else None) \
        or preset.get("timezone") or "UTC"
    provider = (preset.get("timetable") or {}).get("provider", "masjidal")
    print(f"mosque={mosque_id} tz={tz} provider={provider} today={ymd_in(tz)}")
    fn = PROVIDERS.get(provider)
    if fn is None:
        raise RuntimeError(f"unknown timetable provider '{provider}'")

    payload, _secrets = fn(tz, preset)
    payload.setdefault("mosqueId", mosque_id)
    date_str, count = validate(payload, tz)
    print(f"  validated: {count} entries for {date_str}")

    nxt = json.dumps(payload, indent=2) + "\n"
    prev = out.read_text(encoding="utf-8") if out.exists() else ""
    if prev == nxt:
        print("  unchanged")
        return date_str
    # Write via a temp file so an interrupted run cannot leave a truncated
    # file for every device to download.
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.parent / f"{out.name}.tmp"
    tmp.write_text(nxt, encoding="utf-8")
    tmp.replace(out)
    print(f"  wrote {out.relative_to(ROOT)} for {date_str}")
    return date_str


def main() -> int:
    config = read_json(ROOT / "config.json", {}) or {}
    default = os.environ.get("PRAYER_MOSQUE") or config.get("mosque") or "masjid-el-noor"

    if "--all" not in sys.argv[1:]:
        # Single-mosque mode (original behaviour): prayers.json for the default.
        publish(default, config, OUT)
        return 0

    # --all: data/<id>/prayers.json for every preset (example-* skipped), plus
    # the root prayers.json for the default mosque (older devices read it).
    # One mosque failing must not stop the others; the run fails only if the
    # default mosque fails.
    failed = []
    ids = sorted(p.stem for p in (ROOT / "mosques").glob("*.json") if not p.stem.startswith("example-"))
    for mosque_id in ids:
        try:
            publish(mosque_id, config, ROOT / "data" / mosque_id / "prayers.json")
        except Exception as exc:  # noqa: BLE001
            failed.append(mosque_id)
            print(f"  FAILED {mosque_id}: {exc}", file=sys.stderr)
    src = ROOT / "data" / default / "prayers.json"
    if default in failed or not src.exists():
        raise RuntimeError(f"default mosque '{default}' failed")
    if not OUT.exists() or OUT.read_text(encoding="utf-8") != src.read_text(encoding="utf-8"):
        OUT.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    print(f"published {len(ids) - len(failed)}/{len(ids)} mosques" + (f"; failed: {', '.join(failed)}" if failed else ""))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001
        print(f"fatal: {exc}", file=sys.stderr)
        sys.exit(1)
