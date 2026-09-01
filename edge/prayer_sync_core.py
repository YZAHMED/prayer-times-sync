#!/usr/bin/env python3
"""Data layer for prayer-sync.

Everything that involves parsing, arithmetic or time zones lives here. The
shell side keeps what shell is genuinely good at — supervising mpv, probing
audio devices, talking to bluetoothctl, handling signals.

This split exists for a concrete reason: every portability bug this project
hit came from awk. mawk (the default awk on Raspberry Pi OS and Debian) has no
`{n,m}` regex intervals and no `0x` constants, and `%c` behaves differently
across awks and locales. Those failures were invisible except on the exact
target build. Python's json, datetime and zoneinfo have none of that variance.

Commands (all print plain text on stdout; diagnostics go to stderr):

    config              merged configuration as "path<TAB>value"
    get PATH [DEFAULT]  one configuration value
    windows             today's play windows: "NAME<TAB>start_sec<TAB>end_sec"
    times               locally computed prayer times: "Name<TAB>HH:MM:SS"
    resolve             candidate stream URLs, best first
    refresh             fetch and validate timetable/config, then install them
    status              key/value facts about cached data, for `prayer-sync doctor`
    selftest            regression suite for this layer
"""

from __future__ import annotations

import json
import math
import os
import re
import sys
import urllib.error
import time
import urllib.request
from datetime import date, datetime, timedelta

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - Python < 3.9
    print("prayer-sync requires Python 3.9 or newer (for zoneinfo)", file=sys.stderr)
    sys.exit(2)

VERSION = "2.0.0"
UA = f"prayer-sync/{VERSION}"

CONF_DIR = os.environ.get("PS_CONF_DIR", "/etc/prayer-sync")
STATE_DIR = os.environ.get("PS_STATE_DIR", "/var/lib/prayer-sync")


def warn(msg: str) -> None:
    print(f"{msg}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Configuration
#
# Layered, lowest priority first:
#   1. DEFAULTS below
#   2. mosques/<mosque>.json   the mosque preset
#   3. config.json             fleet-wide, refreshed from the repository
#   4. config.local.json       this device only, never overwritten
# ---------------------------------------------------------------------------

DEFAULTS = {
    "mosque": "masjid-el-noor",
    "timezone": "UTC",
    "location": {"latitude": 0.0, "longitude": 0.0, "elevation": 0.0},
    "stream": {
        "provider": "static",
        "url": None,
        "mixlr_slug": "",
        "fallback_urls": [],
        "fallback_file": "",
        "fallback_files": {},
        "resolve_timeout_seconds": 12,
        "require_live": False,
    },
    "schedule": {
        "prayers": ["Fajr", "Dhuhr", "Asr", "Maghrib", "Isha"],
        "start_anchor": "prayerAdhan",
        "stop_anchor": "prayerIqamah",
        "jumah_replaces_dhuhr": True,
        "jumah_weekday": 5,
        "refresh_at": "02:30",
        "tick_seconds": 20,
        "max_window_minutes": 90,
    },
    "offsets": {"default": {"pre": 5, "post": 15}},
    "offline": {"enabled": True, "assumed_iqamah_gap_minutes": 10, "stale_after_days": 2},
    "data": {"remote_base": "", "refresh_timeout_seconds": 25, "refresh_retries": 3},
    "calculation": {"method": "ISNA", "asr": "standard", "high_latitude": "angle_based"},
    "quiet_hours": {"enabled": False, "from": "23:30", "to": "04:30"},
    "audio": {},
}


def read_json(path: str):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return None
    except (json.JSONDecodeError, OSError) as exc:
        warn(f"[ERROR] {path} is not usable JSON ({exc}); ignoring it")
        return None


def deep_merge(base: dict, overlay: dict) -> dict:
    """Overlay wins. Lists are replaced wholesale, never merged element-wise:
    a shortened list must not leave the base's extra entries behind."""
    out = dict(base)
    for key, value in overlay.items():
        if key.startswith("$"):  # "$comment" keys are documentation
            continue
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def load_config() -> dict:
    cfg = json.loads(json.dumps(DEFAULTS))

    main = read_json(os.path.join(CONF_DIR, "config.json")) or {}
    local = read_json(os.path.join(CONF_DIR, "config.local.json")) or {}

    mosque = local.get("mosque") or main.get("mosque") or DEFAULTS["mosque"]
    preset = read_json(os.path.join(CONF_DIR, "mosques", f"{mosque}.json"))
    if preset is None:
        warn(f"[WARN] no preset for mosque '{mosque}' in {CONF_DIR}/mosques")
        preset = {}

    for layer in (preset, main, local):
        cfg = deep_merge(cfg, layer)
    cfg["mosque"] = mosque
    return cfg


def flatten(obj, prefix: str = ""):
    """Emit (path, value) pairs. Kept identical in shape to what the shell
    expects: dotted keys, [n] for list indices."""
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key.startswith("$"):
                continue
            yield from flatten(value, f"{prefix}.{key}" if prefix else key)
    elif isinstance(obj, list):
        for i, value in enumerate(obj):
            yield from flatten(value, f"{prefix}[{i}]")
    else:
        if obj is None:
            text = "null"
        elif obj is True:
            text = "true"
        elif obj is False:
            text = "false"
        else:
            text = str(obj)
        yield prefix, text.replace("\n", " ").replace("\t", " ")


def cfg_get(cfg: dict, path: str, default=None):
    node = cfg
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return default if node is None else node


def tz_of(cfg: dict) -> ZoneInfo:
    name = cfg_get(cfg, "timezone", "UTC")
    try:
        return ZoneInfo(name)
    except Exception:
        warn(
            f"[ERROR] timezone '{name}' is not in the system tzdata — every prayer "
            f"time would be wrong. Install tzdata (apt install tzdata / apk add tzdata)."
        )
        return ZoneInfo("UTC")


# ---------------------------------------------------------------------------
# Time helpers. Internally everything is seconds since local midnight.
# ---------------------------------------------------------------------------

def hms_to_sec(text) -> int | None:
    """Accepts HH:MM or HH:MM:SS. Rejects everything else — the upstream feed
    contains day-prefixed values such as '1.00:43:30' for Islamic midnight."""
    if not isinstance(text, str):
        return None
    parts = text.strip().split(":")
    if len(parts) not in (2, 3):
        return None
    if not all(p.isdigit() for p in parts):
        return None
    if len(parts[0]) > 2 or len(parts[1]) != 2:
        return None
    if len(parts) == 3 and len(parts[2]) != 2:
        return None
    h, m = int(parts[0]), int(parts[1])
    s = int(parts[2]) if len(parts) == 3 else 0
    if h > 23 or m > 59 or s > 59:
        return None
    return h * 3600 + m * 60 + s


def sec_to_hms(sec: int) -> str:
    sec = int(sec) % 86400
    return f"{sec // 3600:02d}:{(sec % 3600) // 60:02d}:{sec % 60:02d}"


# ---------------------------------------------------------------------------
# Prayer-time computation
#
# Standard solar-position algorithm (equation of time + declination). This is
# the safety net: if the mosque API and this repository are both unreachable,
# the device still calls the adhan at the right minute, indefinitely.
# ---------------------------------------------------------------------------

METHODS = {
    "MWL": (18.0, 17.0, None),
    "ISNA": (15.0, 15.0, None),
    "EGYPT": (19.5, 17.5, None),
    "KARACHI": (18.0, 18.0, None),
    "MAKKAH": (18.5, None, 90),
    "TEHRAN": (17.7, 14.0, None),
    "JAFARI": (16.0, 14.0, None),
}


class SunCalc:
    def __init__(self, lat, lng, elev, tz_offset_hours, day: date, calc: dict):
        self.lat = float(lat)
        self.lng = float(lng)
        self.elev = max(0.0, float(elev))
        self.tz = float(tz_offset_hours)

        method = str(calc.get("method", "ISNA")).upper()
        fajr_angle, isha_angle, isha_minutes = METHODS.get(method, METHODS["ISNA"])
        if calc.get("fajr_angle"):
            fajr_angle = float(calc["fajr_angle"])
        if calc.get("isha_angle"):
            isha_angle, isha_minutes = float(calc["isha_angle"]), None
        if calc.get("isha_minutes"):
            isha_minutes, isha_angle = int(calc["isha_minutes"]), None

        self.fajr_angle = fajr_angle
        self.isha_angle = isha_angle
        self.isha_minutes = isha_minutes
        self.asr_factor = 2 if str(calc.get("asr", "standard")).lower() == "hanafi" else 1
        self.highlat = str(calc.get("high_latitude", "angle_based")).lower()
        self.maghrib_minutes = float(calc.get("maghrib_minutes", 0) or 0)
        self.dhuhr_minutes = float(calc.get("dhuhr_minutes", 0) or 0)

        self.rs_angle = 0.833 + 0.0347 * math.sqrt(self.elev)
        self.jdate = self._julian(day) - self.lng / (15 * 24)

    @staticmethod
    def _julian(d: date) -> float:
        y, m, dd = d.year, d.month, d.day
        if m <= 2:
            y -= 1
            m += 12
        a = y // 100
        b = 2 - a + a // 4
        return math.floor(365.25 * (y + 4716)) + math.floor(30.6001 * (m + 1)) + dd + b - 1524.5

    def _sun(self, jd):
        D = jd - 2451545.0
        g = math.radians((357.529 + 0.98560028 * D) % 360)
        q = (280.459 + 0.98564736 * D) % 360
        L = math.radians((q + 1.915 * math.sin(g) + 0.020 * math.sin(2 * g)) % 360)
        e = math.radians(23.439 - 0.00000036 * D)
        decl = math.degrees(math.asin(math.sin(e) * math.sin(L)))
        ra = math.degrees(math.atan2(math.cos(e) * math.sin(L), math.cos(L))) / 15 % 24
        return decl, q / 15 - ra

    def _midday(self, portion):
        return (12 - self._sun(self.jdate + portion)[1]) % 24

    def _angle_time(self, angle, portion, before_noon):
        decl = math.radians(self._sun(self.jdate + portion)[0])
        lat = math.radians(self.lat)
        den = math.cos(decl) * math.cos(lat)
        if den == 0:
            return None
        x = (-math.sin(math.radians(angle)) - math.sin(decl) * math.sin(lat)) / den
        if not -1.0 <= x <= 1.0:
            # The sun never reaches this angle on this day at this latitude.
            return None
        v = math.degrees(math.acos(x)) / 15
        noon = self._midday(portion)
        return noon - v if before_noon else noon + v

    def _asr(self, portion):
        decl = self._sun(self.jdate + portion)[0]
        angle = -math.degrees(math.atan(1.0 / (self.asr_factor + math.tan(math.radians(abs(self.lat - decl))))))
        return self._angle_time(angle, portion, False)

    def _solve(self):
        t = {"fajr": 5.0, "sunrise": 6.0, "dhuhr": 12.0, "asr": 13.0, "sunset": 18.0, "isha": 18.0}
        for _ in range(3):
            p = {k: (v / 24 if v is not None else DEFAULT_PORTIONS[k] / 24) for k, v in t.items()}
            t = {
                "fajr": self._angle_time(self.fajr_angle, p["fajr"], True),
                "sunrise": self._angle_time(self.rs_angle, p["sunrise"], True),
                "dhuhr": self._midday(p["dhuhr"]),
                "asr": self._asr(p["asr"]),
                "sunset": self._angle_time(self.rs_angle, p["sunset"], False),
                "isha": (t["sunset"] + 1 if self.isha_minutes
                         else self._angle_time(self.isha_angle, p["isha"], False)),
            }
        return t

    def compute(self) -> dict:
        t = self._solve()

        # Above roughly 48.5 degrees the sun can fail to reach the twilight
        # angles at all, and inside the polar circles it may never rise or set.
        # Astronomy has no answer there, so fall back to the widely used
        # nearest-latitude (Aqrab al-Bilad) convention rather than nothing.
        if any(t[k] is None for k in ("sunrise", "sunset", "fajr", "isha")) and self.highlat != "none":
            self.lat = -48.5 if self.lat < 0 else 48.5
            t = self._solve()

        shift = self.tz - self.lng / 15
        t = {k: (v + shift if v is not None else None) for k, v in t.items()}

        if self.highlat != "none" and t["sunrise"] is not None and t["sunset"] is not None:
            night = (t["sunrise"] - t["sunset"]) % 24
            t["fajr"] = self._refine(t["fajr"], t["sunrise"], self.fajr_angle, night, True)
            if not self.isha_minutes:
                t["isha"] = self._refine(t["isha"], t["sunset"], self.isha_angle, night, False)

        if t["sunset"] is not None:
            t["maghrib"] = t["sunset"] + self.maghrib_minutes / 60
            if self.isha_minutes:
                t["isha"] = t["maghrib"] + self.isha_minutes / 60
        else:
            t["maghrib"] = None
        if t["dhuhr"] is not None:
            t["dhuhr"] += self.dhuhr_minutes / 60

        return {
            "Fajr": t["fajr"], "Sunrise": t["sunrise"], "Dhuhr": t["dhuhr"],
            "Asr": t["asr"], "Sunset": t["sunset"], "Maghrib": t["maghrib"], "Isha": t["isha"],
        }

    def _night_portion(self, angle, night):
        if self.highlat == "angle_based":
            return night * (angle or 18.0) / 60
        if self.highlat == "one_seventh":
            return night / 7
        return night / 2

    def _refine(self, value, base, angle, night, before):
        portion = self._night_portion(angle, night)
        if value is None:
            diff = 99.0
        else:
            diff = (base - value) % 24 if before else (value - base) % 24
        if value is None or diff > portion:
            return base - portion if before else base + portion
        return value


DEFAULT_PORTIONS = {"fajr": 5, "sunrise": 6, "dhuhr": 12, "asr": 13, "sunset": 18, "isha": 18}


def computed_times(cfg: dict, when: date, tz: ZoneInfo) -> dict:
    lat = float(cfg_get(cfg, "location.latitude", 0) or 0)
    lng = float(cfg_get(cfg, "location.longitude", 0) or 0)
    if lat == 0 and lng == 0:
        warn("[ERROR] location.latitude/longitude are unset; cannot compute times locally")
        return {}
    offset = datetime(when.year, when.month, when.day, 12, tzinfo=tz).utcoffset()
    tz_hours = offset.total_seconds() / 3600 if offset else 0.0
    calc = cfg_get(cfg, "calculation", {}) or {}
    return SunCalc(lat, lng, cfg_get(cfg, "location.elevation", 0) or 0, tz_hours, when, calc).compute()


# ---------------------------------------------------------------------------
# Timetable
# ---------------------------------------------------------------------------

def prayers_path() -> str:
    return os.path.join(STATE_DIR, "prayers.json")


def timetable_date(doc) -> str | None:
    try:
        raw = doc["data"]["prayerOfDay"]["prayerDate"]
    except (TypeError, KeyError):
        return None
    return str(raw)[:10] if raw else None


def single_prayers(doc) -> list:
    try:
        items = doc["data"]["prayerOfDay"]["singlePrayers"]
    except (TypeError, KeyError):
        return []
    return items if isinstance(items, list) else []


def anchor_value(entry: dict, keys) -> int | None:
    for key in keys:
        got = hms_to_sec(entry.get(key))
        if got is not None:
            return got
    return None


def todays_prayers(cfg: dict, when: date) -> list:
    names = list(cfg_get(cfg, "schedule.prayers", []) or [])
    if (cfg_get(cfg, "schedule.jumah_replaces_dhuhr", True)
            and when.isoweekday() == int(cfg_get(cfg, "schedule.jumah_weekday", 5) or 5)):
        names = ["Jumah" if n == "Dhuhr" else n for n in names]
    return names


def offsets_for(cfg: dict, name: str):
    table = cfg_get(cfg, "offsets", {}) or {}
    default = table.get("default", {}) or {}
    entry = table.get(name, {}) or {}

    def pick(key, fallback):
        for source in (entry, default):
            value = source.get(key)
            if isinstance(value, (int, float)):
                return int(value)
        return fallback

    return pick("pre", 5), pick("post", 15)


def build_windows(cfg: dict, when: date, tz: ZoneInfo):
    """Returns (rows, carry, source). Each row is (name, start_sec, end_sec)."""
    doc = read_json(prayers_path())
    ttdate = timetable_date(doc) if doc else None
    today_str = when.isoformat()
    source = "timetable"

    use_offline = False
    if not doc or not ttdate:
        use_offline = True
    elif ttdate != today_str:
        stale = abs((when - date.fromisoformat(ttdate)).days)
        limit = int(cfg_get(cfg, "offline.stale_after_days", 2) or 2)
        if cfg_get(cfg, "offline.enabled", True) and stale >= limit:
            warn(f"[WARN] timetable is {stale} day(s) old — computing prayer times locally")
            use_offline = True
        else:
            warn(f"[WARN] timetable is for {ttdate}, not {today_str}; still within tolerance")

    rows = []
    gap = int(cfg_get(cfg, "offline.assumed_iqamah_gap_minutes", 10) or 10)

    if not use_offline:
        by_name = {e.get("prayerName"): e for e in single_prayers(doc) if isinstance(e, dict)}
        start_key = cfg_get(cfg, "schedule.start_anchor", "prayerAdhan")
        stop_key = cfg_get(cfg, "schedule.stop_anchor", "prayerIqamah")
        for name in todays_prayers(cfg, when):
            entry = by_name.get(name)
            if not entry:
                warn(f"[WARN] {name} is absent from the timetable — skipped")
                continue
            start = anchor_value(entry, [start_key, "prayerBegins", "prayerIqamah"])
            if start is None:
                warn(f"[WARN] {name} has no usable start time — skipped")
                continue
            stop = anchor_value(entry, [stop_key, "prayerAdhan", "prayerBegins"])
            if stop is None or stop < start:
                stop = start + gap * 60
            pre, post = offsets_for(cfg, name)
            rows.append((name, start - pre * 60, stop + post * 60))
        if not rows:
            warn("[WARN] timetable produced no usable windows — computing locally")
            use_offline = True

    if use_offline:
        source = "computed"
        rows = []
        times = computed_times(cfg, when, tz)
        for name in todays_prayers(cfg, when):
            key = "Dhuhr" if name == "Jumah" else name
            hours = times.get(key)
            if hours is None:
                warn(f"[WARN] no computed time for {name}")
                continue
            start = int(round((hours % 24) * 3600))
            pre, post = offsets_for(cfg, name)
            rows.append((name, start - pre * 60, start + gap * 60 + post * 60))

    return clamp_windows(cfg, rows) + (source,)


def clamp_windows(cfg: dict, rows):
    """Clamp to the day, cap the length, and hand any tail that runs past
    midnight to tomorrow instead of truncating it at 23:59."""
    max_secs = int(cfg_get(cfg, "schedule.max_window_minutes", 90) or 90) * 60
    out, carry = [], []
    for name, start, end in rows:
        start = max(0, start)
        if end <= start:
            warn(f"[WARN] {name}: window collapsed — skipped")
            continue
        if end - start > max_secs:
            warn(f"[WARN] {name}: window longer than {max_secs // 60}m — truncating")
            end = start + max_secs
        if end > 86399:
            tail = end - 86400
            if tail > 0:
                carry.append((name, 0, tail))
            end = 86399
        if end > start:
            out.append((name, start, end))
    out.sort(key=lambda r: r[1])
    return out, carry


# ---------------------------------------------------------------------------
# Network
# ---------------------------------------------------------------------------

def http_get(url: str, timeout: int = 20, retries: int = 3, want_json: bool = True):
    last = None
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": UA,
                "Accept": "application/json, text/plain, */*",
                "Cache-Control": "no-cache",
            })
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read()
            text = body.decode("utf-8", errors="replace")
            return json.loads(text) if want_json else text
        except Exception as exc:  # noqa: BLE001 - any failure is just a retry
            last = exc
            if attempt < retries:
                time.sleep(attempt * 2)
    warn(f"[WARN] {url}: {last}")
    return None


def probe_audio(url: str, timeout: int = 8) -> bool:
    """A live Icecast stream never ends, so judge on the response headers and
    close the connection rather than reading the body."""
    if url.startswith("/"):
        return os.path.isfile(url)
    for method in ("HEAD", "GET"):
        try:
            req = urllib.request.Request(url, method=method, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                ctype = (resp.headers.get("Content-Type") or "").lower()
                if 200 <= resp.status < 300 and (
                    ctype.startswith("audio/") or ctype.startswith("video/")
                    or "ogg" in ctype or "mpegurl" in ctype or "octet-stream" in ctype
                ):
                    return True
        except Exception:  # noqa: BLE001
            continue
    return False


def resolve_mixlr(slug: str, timeout: int):
    """Mixlr mints a new broadcast id every time the mosque goes live, so a URL
    cached once a day is stale the moment they restart. Resolve it live."""
    found = []
    doc = http_get(f"https://api.mixlr.com/v3/channel_view/{slug}", timeout, 2)
    if isinstance(doc, dict):
        for item in doc.get("included") or []:
            url = (item.get("attributes") or {}).get("progressive_stream_url")
            if url:
                found.append(url)
    doc = http_get(f"https://api.mixlr.com/users/{slug}", timeout, 2)
    if isinstance(doc, dict):
        for bid in doc.get("broadcast_ids") or []:
            found.append(f"https://listen.mixlr.com/{bid}")
    return found


def stream_candidates(cfg: dict, prayer: str = ""):
    provider = cfg_get(cfg, "stream.provider", "static")
    timeout = int(cfg_get(cfg, "stream.resolve_timeout_seconds", 12) or 12)
    out = []

    if provider == "mixlr":
        slug = cfg_get(cfg, "stream.mixlr_slug", "") or cfg_get(cfg, "mosque", "")
        out.extend(resolve_mixlr(slug, timeout))
        if not out:
            warn(f"[WARN] could not resolve Mixlr stream for '{slug}' — trying cached URLs")

    url = cfg_get(cfg, "stream.url")
    if url:
        out.append(url)
    out.extend(cfg_get(cfg, "stream.fallback_urls", []) or [])

    for name in ("last_good_stream.txt", "stream_url.txt"):
        try:
            with open(os.path.join(STATE_DIR, name), encoding="utf-8") as fh:
                for line in fh:
                    if line.strip():
                        out.append(line.strip())
        except OSError:
            pass

    # Per-prayer fallback file wins over the generic one — Fajr's adhan
    # contains "as-salatu khayrun min an-nawm" and belongs only at Fajr.
    if prayer:
        per_prayer = cfg_get(cfg, "stream.fallback_files", {}) or {}
        specific = per_prayer.get(prayer)
        if specific and os.path.isfile(specific):
            out.append(specific)
    local = cfg_get(cfg, "stream.fallback_file", "")
    if local and os.path.isfile(local):
        out.append(local)

    seen, unique = set(), []
    for item in out:
        if item and item not in seen:
            seen.add(item)
            unique.append(item)
    return unique


def atomic_write(path: str, text: str) -> None:
    tmp = f"{path}.tmp.{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)


def cmd_refresh(cfg: dict) -> int:
    base = str(cfg_get(cfg, "data.remote_base", "") or "").rstrip("/")
    if not base:
        warn("[WARN] data.remote_base is not set; nothing to refresh")
        return 1
    timeout = int(cfg_get(cfg, "data.refresh_timeout_seconds", 25) or 25)
    retries = int(cfg_get(cfg, "data.refresh_retries", 3) or 3)
    ok = False

    doc = http_get(f"{base}/prayers.json", timeout, retries)
    if isinstance(doc, dict) and timetable_date(doc) and single_prayers(doc):
        atomic_write(prayers_path(), json.dumps(doc, indent=2) + "\n")
        print(f"[INFO] timetable refreshed (for {timetable_date(doc)})")
        ok = True
    else:
        warn("[WARN] could not refresh the timetable — keeping the cached copy")

    for remote, local in (
        ("config.json", os.path.join(CONF_DIR, "config.json")),
        (f"mosques/{cfg_get(cfg, 'mosque', '')}.json",
         os.path.join(CONF_DIR, "mosques", f"{cfg_get(cfg, 'mosque', '')}.json")),
    ):
        got = http_get(f"{base}/{remote}", timeout, 2)
        if isinstance(got, dict):
            atomic_write(local, json.dumps(got, indent=2) + "\n")

    text = http_get(f"{base}/stream_url.txt", 15, 2, want_json=False)
    if isinstance(text, str):
        first = text.strip().splitlines()[0].strip() if text.strip() else ""
        if first.startswith("http"):
            atomic_write(os.path.join(STATE_DIR, "stream_url.txt"), first + "\n")

    return 0 if ok else 1


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def carry_path(day: date) -> str:
    return os.path.join(STATE_DIR, f"carry.{day.isoformat()}")


def cmd_windows(cfg: dict) -> int:
    tz = tz_of(cfg)
    today = datetime.now(tz).date()
    rows, carry, _ = build_windows(cfg, today, tz)

    tomorrow = today + timedelta(days=1)
    if carry:
        try:
            atomic_write(carry_path(tomorrow),
                         "".join(f"{n}\t{s}\t{e}\n" for n, s, e in carry))
            warn(f"[INFO] {carry[0][0]}: {carry[0][2] // 60}m of this window falls "
                 f"after midnight and was carried into {tomorrow.isoformat()}")
        except OSError:
            pass

    merged = list(rows)
    try:
        with open(carry_path(today), encoding="utf-8") as fh:
            for line in fh:
                parts = line.rstrip("\n").split("\t")
                if len(parts) == 3:
                    merged.append((parts[0], int(parts[1]), int(parts[2])))
    except (OSError, ValueError):
        pass

    keep = {carry_path(today), carry_path(tomorrow)}
    try:
        for entry in os.listdir(STATE_DIR):
            if entry.startswith("carry.") and os.path.join(STATE_DIR, entry) not in keep:
                os.unlink(os.path.join(STATE_DIR, entry))
    except OSError:
        pass

    merged.sort(key=lambda r: r[1])
    for name, start, end in merged:
        print(f"{name}\t{start}\t{end}")
    return 0 if merged else 1


def cmd_status(cfg: dict) -> int:
    tz = tz_of(cfg)
    today = datetime.now(tz).date()
    doc = read_json(prayers_path())
    ttdate = timetable_date(doc) if doc else None
    print(f"timezone\t{cfg_get(cfg, 'timezone', 'UTC')}")
    print(f"today\t{today.isoformat()}")
    print(f"now_sec\t{int(datetime.now(tz).hour) * 3600 + datetime.now(tz).minute * 60 + datetime.now(tz).second}")
    print(f"timetable_date\t{ttdate or 'none'}")
    if ttdate:
        try:
            print(f"stale_days\t{abs((today - date.fromisoformat(ttdate)).days)}")
        except ValueError:
            print("stale_days\t9999")
    else:
        print("stale_days\t9999")
    _, _, source = build_windows(cfg, today, tz)
    print(f"source\t{source}")
    times = computed_times(cfg, today, tz)
    print(f"offline_ok\t{'yes' if times else 'no'}")
    if times:
        print("offline_times\t" + "  ".join(
            f"{k} {sec_to_hms(int(round((v % 24) * 3600)))[:5]}" for k, v in times.items() if v is not None))
    return 0


HISTORY_RE_TS      = re.compile(r'(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2}:\d{2})')
HISTORY_RE_OPEN    = re.compile(r'([A-Za-z]+): window open (\d{2}:\d{2})-(\d{2}:\d{2})')
HISTORY_RE_PLAYED  = re.compile(r'([A-Za-z]+): window complete \((\d+)s of audio\)')
HISTORY_RE_FAILED  = re.compile(r'([A-Za-z]+): window ended with no successful playback')
HISTORY_RE_START   = re.compile(r'prayer-sync \S+ starting')
HISTORY_RE_STOP    = re.compile(r'shutting down')


def _history_events(lines):
    """Yield (date, time, kind, name, detail) for lines this parser understands.
    kind is one of open/played/failed/start/stop; other lines are ignored."""
    for line in lines:
        m = HISTORY_RE_TS.search(line)
        if not m:
            continue
        d, t = m.group(1), m.group(2)
        om = HISTORY_RE_OPEN.search(line)
        if om:
            yield d, t, "open", om.group(1), f"{om.group(2)}-{om.group(3)}"
            continue
        pm = HISTORY_RE_PLAYED.search(line)
        if pm:
            yield d, t, "played", pm.group(1), f"{pm.group(2)}s"
            continue
        fm = HISTORY_RE_FAILED.search(line)
        if fm:
            yield d, t, "failed", fm.group(1), "no successful playback"
            continue
        if HISTORY_RE_START.search(line):
            yield d, t, "start", "-", "-"
            continue
        if HISTORY_RE_STOP.search(line):
            yield d, t, "stop", "-", "-"


def _history_pair(events):
    """Pair each 'open' with the next matching 'played'/'failed'/'stop', so a
    caller sees one row per prayer window with a clear outcome."""
    rows = []
    pending = None
    for d, t, kind, name, detail in events:
        if kind == "open":
            if pending:
                rows.append(pending + ("UNKNOWN", "no completion recorded"))
            pending = (d, t, name, detail)
        elif kind == "played" and pending and pending[2] == name:
            rows.append(pending + ("PLAYED", detail))
            pending = None
        elif kind == "failed" and pending and pending[2] == name:
            rows.append(pending + ("MISSED", detail))
            pending = None
        elif kind == "stop" and pending:
            rows.append(pending + ("MISSED", "service stopped mid-window"))
            pending = None
    if pending:
        rows.append(pending + ("RUNNING", "in progress"))
    return rows


def cmd_history(argv) -> int:
    if not argv:
        warn("usage: history <log-file>")
        return 2
    path = argv[0]
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            events = list(_history_events(fh))
    except OSError as exc:
        warn(f"cannot read {path}: {exc}")
        return 1

    rows = _history_pair(events)
    headers = ("DATE", "TIME", "PRAYER", "WINDOW", "STATUS", "NOTE")
    all_rows = [headers] + rows
    widths = [max(len(str(r[i])) for r in all_rows) for i in range(6)]
    for r in all_rows:
        print("  ".join(str(r[i]).ljust(widths[i]) for i in range(6)).rstrip())

    played  = sum(1 for r in rows if r[4] == "PLAYED")
    missed  = sum(1 for r in rows if r[4] == "MISSED")
    unknown = sum(1 for r in rows if r[4] == "UNKNOWN")
    running = sum(1 for r in rows if r[4] == "RUNNING")
    starts  = sum(1 for e in events if e[2] == "start")
    parts = [f"{played} played"]
    if missed:  parts.append(f"{missed} missed")
    if unknown: parts.append(f"{unknown} unknown")
    if running: parts.append(f"{running} in progress")
    parts.append(f"service started {starts} time(s) in this range")
    print()
    print("summary: " + ", ".join(parts))
    return 0


def main(argv) -> int:
    if not argv:
        print(__doc__)
        return 2
    command, args = argv[0], argv[1:]

    if command == "version":
        print(VERSION)
        return 0
    if command == "selftest":
        return run_selftest()
    if command == "history":
        return cmd_history(args)

    cfg = load_config()

    if command == "config":
        for path, value in flatten(cfg):
            print(f"{path}\t{value}")
        return 0
    if command == "get":
        if not args:
            return 2
        value = cfg_get(cfg, args[0], args[1] if len(args) > 1 else "")
        if isinstance(value, bool):
            value = "true" if value else "false"
        elif isinstance(value, (list, dict)):
            value = "\n".join(str(v) for v in value) if isinstance(value, list) else ""
        print(value)
        return 0
    if command == "windows":
        return cmd_windows(cfg)
    if command == "times":
        tz = tz_of(cfg)
        times = computed_times(cfg, datetime.now(tz).date(), tz)
        for name, hours in times.items():
            print(f"{name}\t{sec_to_hms(int(round((hours % 24) * 3600))) if hours is not None else 'INVALID'}")
        return 0 if times else 1
    if command == "resolve":
        found = stream_candidates(cfg, args[0] if args else "")
        for url in found:
            print(url)
        return 0 if found else 1
    if command == "probe":
        return 0 if args and probe_audio(args[0]) else 1
    if command == "refresh":
        return cmd_refresh(cfg)
    if command == "status":
        return cmd_status(cfg)

    warn(f"unknown command: {command}")
    return 2


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

def run_selftest() -> int:
    passed = failed = 0

    def check(desc, expected, actual):
        nonlocal passed, failed
        if expected == actual:
            passed += 1
        else:
            failed += 1
            print(f"  FAIL {desc}\n       expected {expected!r}\n       actual   {actual!r}")

    def near(desc, expected_hms, hours, tolerance=120):
        nonlocal passed, failed
        if hours is None:
            failed += 1
            print(f"  FAIL {desc}: no value")
            return
        actual = int(round((hours % 24) * 3600))
        want = hms_to_sec(expected_hms)
        if abs(actual - want) <= tolerance:
            passed += 1
        else:
            failed += 1
            print(f"  FAIL {desc}: {sec_to_hms(actual)} vs {expected_hms} "
                  f"({abs(actual - want)}s apart, tolerance {tolerance}s)")

    print(f"prayer_sync_core {VERSION} self-test (python {sys.version_info.major}.{sys.version_info.minor})")

    print("\ntime parsing")
    check("HH:MM:SS", 77700, hms_to_sec("21:35:00"))
    check("HH:MM", 77700, hms_to_sec("21:35"))
    check("leading zeros are decimal, not octal", 30600, hms_to_sec("08:30:00"))
    check("09 is not octal", 34200, hms_to_sec("09:30:00"))
    check("midnight", 0, hms_to_sec("00:00:00"))
    check("last second", 86399, hms_to_sec("23:59:59"))
    check("reject 24:00", None, hms_to_sec("24:00:00"))
    check("reject 12:60", None, hms_to_sec("12:60:00"))
    check("reject day-prefixed", None, hms_to_sec("1.00:43:30"))
    check("reject empty", None, hms_to_sec(""))
    check("reject None", None, hms_to_sec(None))
    check("format 77700", "21:35:00", sec_to_hms(77700))

    print("configuration")
    merged = deep_merge({"a": {"x": 1, "y": 2}, "l": [1, 2, 3]}, {"a": {"y": 9}, "l": [7]})
    check("nested override", 9, merged["a"]["y"])
    check("sibling preserved", 1, merged["a"]["x"])
    check("list replaced wholesale", [7], merged["l"])
    check("$comment dropped", {"k": 1}, deep_merge({}, {"$comment": "doc", "k": 1}))
    flat = dict(flatten({"a": {"b": None}, "c": False, "d": [1, 2]}))
    check("null preserved", "null", flat["a.b"])
    check("false preserved", "false", flat["c"])
    check("list indices", "2", flat["d[1]"])

    print("prayer calculation (Toronto 2026-03-28, mosque's published times)")
    calc = {"method": "ISNA", "asr": "hanafi", "high_latitude": "angle_based"}
    times = SunCalc(43.6893245, -79.4718826, 0, -4, date(2026, 3, 28), calc).compute()
    near("Fajr", "05:47:00", times["Fajr"])
    near("Sunrise", "07:07:00", times["Sunrise"])
    near("Dhuhr", "13:23:00", times["Dhuhr"])
    near("Asr (hanafi)", "17:47:00", times["Asr"])
    near("Maghrib", "19:40:00", times["Maghrib"])
    near("Isha", "21:00:00", times["Isha"])
    std = SunCalc(43.6893245, -79.4718826, 0, -4, date(2026, 3, 28),
                  {**calc, "asr": "standard"}).compute()
    check("standard Asr differs from hanafi", True, abs(std["Asr"] - times["Asr"]) > 0.5)

    polar = SunCalc(78.2232, 15.6469, 0, 1, date(2026, 12, 21),
                    {"method": "MWL", "asr": "standard", "high_latitude": "angle_based"}).compute()
    check("polar night still yields every time", True, all(v is not None for v in polar.values()))
    midnight_sun = SunCalc(69.6496, 18.9560, 0, 2, date(2026, 6, 21),
                           {"method": "MWL", "asr": "standard", "high_latitude": "angle_based"}).compute()
    check("midnight sun still yields every time", True, all(v is not None for v in midnight_sun.values()))

    ordered = True
    for month in range(1, 13):
        for day in (1, 15, 28):
            t = SunCalc(43.6893245, -79.4718826, 0, -5, date(2026, month, day), calc).compute()
            seq = [t["Fajr"], t["Sunrise"], t["Dhuhr"], t["Asr"], t["Sunset"], t["Isha"]]
            if any(v is None for v in seq) or seq != sorted(seq):
                ordered = False
    check("36 dates across the year stay correctly ordered", True, ordered)

    print("scheduling")
    cfg = json.loads(json.dumps(DEFAULTS))
    cfg["schedule"]["max_window_minutes"] = 90
    rows, carry = clamp_windows(cfg, [("T", 3000, 4800)])
    check("plain window", [("T", 3000, 4800)], rows)
    rows, carry = clamp_windows(cfg, [("T", -300, 600)])
    check("negative start clamped", [("T", 0, 600)], rows)
    rows, carry = clamp_windows(cfg, [("T", 500, 400)])
    check("collapsed window dropped", [], rows)
    rows, carry = clamp_windows(cfg, [("T", 0, 99999)])
    check("over-long window truncated", [("T", 0, 5400)], rows)
    rows, carry = clamp_windows(cfg, [("T", 86000, 86800)])
    check("midnight tail clamped", [("T", 86000, 86399)], rows)
    check("midnight tail carried", [("T", 0, 400)], carry)

    cfg["offsets"] = {"default": {"pre": 5, "post": 15}, "Maghrib": {"pre": 2, "post": 10}}
    check("offset override", (2, 10), offsets_for(cfg, "Maghrib"))
    check("offset default", (5, 15), offsets_for(cfg, "Fajr"))
    cfg["offsets"]["Isha"] = {"pre": 0}
    check("partial override falls back for post", (0, 15), offsets_for(cfg, "Isha"))

    cfg["schedule"]["prayers"] = ["Fajr", "Dhuhr", "Asr", "Maghrib", "Isha"]
    friday = date(2026, 8, 21)
    check("Jumah replaces Dhuhr on Friday", True, "Jumah" in todays_prayers(cfg, friday))
    check("Dhuhr on other days", True, "Dhuhr" in todays_prayers(cfg, date(2026, 8, 20)))

    print("timetable parsing")
    doc = {"data": {"prayerOfDay": {"prayerDate": "2026-08-23T00:00:00", "singlePrayers": [
        {"prayerName": "Isha", "prayerBegins": "21:35:00", "prayerAdhan": "21:35:00", "prayerIqamah": "21:45:00"},
        {"prayerName": "IslamicMidnight", "prayerBegins": "1.00:37:00", "prayerAdhan": None, "prayerIqamah": None},
    ]}}}
    check("date extracted", "2026-08-23", timetable_date(doc))
    check("entries found", 2, len(single_prayers(doc)))
    entry = single_prayers(doc)[0]
    check("anchor resolves", 77700, anchor_value(entry, ["prayerAdhan", "prayerBegins"]))
    check("anchor falls through nulls", None,
          anchor_value(single_prayers(doc)[1], ["prayerAdhan", "prayerIqamah"]))
    check("day-prefixed value rejected", None,
          anchor_value(single_prayers(doc)[1], ["prayerBegins"]))
    check("missing timetable is not fatal", None, timetable_date(None))
    check("html is not a timetable", None, timetable_date("<html>404</html>"))

    print("fallback file selection")
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        generic = os.path.join(td, "adhan.mp3")
        fajr    = os.path.join(td, "adhan-fajr.mp3")
        open(generic, "wb").close()
        open(fajr, "wb").close()
        cfg = json.loads(json.dumps(DEFAULTS))
        cfg["stream"]["provider"] = "static"
        cfg["stream"]["url"] = "https://example.org/stream.mp3"
        cfg["stream"]["fallback_file"]  = generic
        cfg["stream"]["fallback_files"] = {"Fajr": fajr}
        check("Fajr picks Fajr-specific file first", True,
              stream_candidates(cfg, "Fajr").index(fajr) <
              stream_candidates(cfg, "Fajr").index(generic))
        check("Dhuhr does not include Fajr file", False,
              fajr in stream_candidates(cfg, "Dhuhr"))
        check("no-prayer call skips per-prayer files", False,
              fajr in stream_candidates(cfg, ""))
        # If per-prayer file goes missing (e.g. install stripped) the caller
        # must still see the generic file — silence is worse than the wrong adhan.
        os.unlink(fajr)
        check("missing per-prayer file falls through to generic",
              True, generic in stream_candidates(cfg, "Fajr"))

    print("history parser")
    sample = [
        # journalctl short-iso prepends its own timestamp; the app also
        # timestamps its own lines. The parser must handle both.
        "2026-08-31T05:29:00+0400 homepi prayer-sync[1]: 2026-08-31T05:29:00+0400 [INFO] prayer-sync 2.0.0 starting",
        "2026-08-31T05:30:00+0400 [INFO] Fajr: window open 05:30-06:00",
        "2026-08-31T05:34:22+0400 [INFO] Fajr: window complete (262s of audio)",
        "2026-08-31T13:30:00+0400 [INFO] Dhuhr: window open 13:30-14:00",
        "2026-08-31T14:00:00+0400 [ERROR] Dhuhr: window ended with no successful playback",
        "2026-08-31T17:59:00+0400 [INFO] Asr: window open 18:00-18:30",
        "2026-08-31T18:15:00+0400 [INFO] shutting down",
        "-- Boot 12345 --",  # journalctl noise; must be ignored
    ]
    ev = list(_history_events(sample))
    check("start recognised", "start", ev[0][2])
    check("plain-app line recognised", "open", ev[1][2])
    check("played extracted with seconds", ("Fajr", "262s"), (ev[2][3], ev[2][4]))
    check("failed recognised", "failed", ev[4][2])
    check("stop recognised", "stop", ev[6][2])
    check("noise line skipped", 7, len(ev))

    rows = _history_pair(ev)
    check("three windows paired", 3, len(rows))
    check("Fajr played", ("Fajr", "PLAYED", "262s"), (rows[0][2], rows[0][4], rows[0][5]))
    check("Dhuhr missed", ("Dhuhr", "MISSED"), (rows[1][2], rows[1][4]))
    check("Asr cut short by service stop", ("Asr", "MISSED", "service stopped mid-window"),
          (rows[2][2], rows[2][4], rows[2][5]))

    print()
    if failed:
        print(f"CORE SELF-TEST FAILED: {failed} failed, {passed} passed")
        return 1
    print(f"CORE SELF-TEST PASSED: {passed} checks")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
