#!/usr/bin/env python3
"""Offline tests for tools/update_prayers.py (no network)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import update_prayers as u  # noqa: E402

FAILS = 0


def check(name, want, got):
    global FAILS
    ok = want == got
    FAILS += not ok
    print(f"{'ok  ' if ok else 'FAIL'} {name}" + ("" if ok else f": want {want!r}, got {got!r}"))


# 12-hour parsing, including the "ampm" class-name trap and 12 AM/PM
check("PM after tags", "13:07", u._to24("1:07<span class='ampm'>PM</span>"))
check("AM", "05:57", u._to24("5:57<span class='ampm'>AM</span>"))
check("12 PM is noon", "12:30", u._to24("12:30<span class='ampm'>PM</span>"))
check("12 AM is midnight", "00:15", u._to24("12:15 AM"))
check("blank", None, u._to24(" "))

JS = "\n".join(
    [f'beginTime[{i}] = "{t}";' for i, t in enumerate([
        "5:57<span class='ampm'>AM</span>", "7:15<span class='ampm'>AM</span>",
        "1:02<span class='ampm'>PM</span>", "1:07<span class='ampm'>PM</span>",
        "5:10<span class='ampm'>PM</span>", "7:01<span class='ampm'>PM</span>",
        "8:11<span class='ampm'>PM</span>"])]
    + [f'adhanTime[{i}] = "{t}";' for i, t in enumerate([
        "6:15<span class='ampm'>AM</span>", " ", " ", "1:30<span class='ampm'>PM</span>",
        "5:35<span class='ampm'>PM</span>", "7:01<span class='ampm'>PM</span>",
        "8:45<span class='ampm'>PM</span>"])]
    + [f'iqamaTime[{i}] = "{t}";' for i, t in enumerate([
        "6:30<span class='ampm'>AM</span>", " ", " ", "1:45<span class='ampm'>PM</span>",
        "5:45<span class='ampm'>PM</span>", "7:04<span class='ampm'>PM</span>",
        "9:00<span class='ampm'>PM</span>"])])
rows = {r[0]: r for r in u.parse_galaxystream(JS)}
check("galaxystream Fajr", ("Fajr", "05:57", "06:15", "06:30"), rows["Fajr"])
check("galaxystream Dhuhr (Zuhr)", ("Dhuhr", "13:07", "13:30", "13:45"), rows["Dhuhr"])
check("galaxystream Isha", ("Isha", "20:11", "20:45", "21:00"), rows["Isha"])
env = u.envelope({"id": "x", "name": "X"}, u.ymd_in("UTC"), list(rows.values()), "galaxystream")
check("envelope mosqueId", "x", env["mosqueId"])
check("envelope HH:MM:SS", "06:15:00", env["data"]["prayerOfDay"]["singlePrayers"][0]["prayerAdhan"])
check("envelope validates", u.ymd_in("UTC"), u.validate(env, "UTC")[0])

base = "https://portal.example/v1/masjid/Prayer/GetPrayerTimesOfDay?masjidId=11"
check("masjid_url swaps id", base.replace("=11", "=359"), u.masjid_url(base, 359))
check("masjid_url keeps base", base, u.masjid_url(base, None))



def rejects(name):
    try:
        u.preset_id(name)
    except RuntimeError:
        return True
    return False


check("preset id accepted", "masjid-el-noor", u.preset_id("masjid-el-noor"))
check("unknown preset rejected", True, rejects("no-such-mosque"))
check("example preset rejected", True, rejects("example-second-mosque"))
check("path rejected", True, rejects("../config"))
check("empty rejected", True, rejects(""))

sys.exit(1 if FAILS else 0)
