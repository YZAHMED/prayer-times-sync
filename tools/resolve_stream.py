#!/usr/bin/env python3
"""Resolve the mosque's current live stream URL for the fallback file.

Note this file only refreshes a *fallback*: broadcast ids rotate every time
the mosque starts a new broadcast, so a URL captured once a day is stale the
moment they restart. Edge devices resolve the same API at play time; this
snapshot only exists for devices that are offline when a prayer begins.

Uses the same resolver the edge devices use, so a Mixlr API change cannot
pass CI while breaking devices (or vice versa).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "stream_url.txt"

# Import the device's own resolver so this cannot drift from what runs on the Pi.
sys.path.insert(0, str(ROOT / "edge"))
import prayer_sync_core as psc  # noqa: E402


def read_json(path: Path, default=None):
    try:
        with path.open(encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return default


def main() -> int:
    config = read_json(ROOT / "config.json", {}) or {}
    mosque_id = os.environ.get("PRAYER_MOSQUE") or config.get("mosque") or "masjid-el-noor"
    preset = read_json(ROOT / "mosques" / f"{mosque_id}.json", {}) or {}

    stream = {**(preset.get("stream") or {}), **(config.get("stream") or {})}
    provider = stream.get("provider", "mixlr")
    print(f"mosque={mosque_id} provider={provider}")

    candidates = []
    timeout = int(stream.get("resolve_timeout_seconds", 20) or 20)
    if provider == "mixlr":
        slug = stream.get("mixlr_slug") or mosque_id
        candidates = psc.resolve_mixlr(slug, timeout)
    elif stream.get("url"):
        candidates = [stream["url"]]

    if not candidates:
        print("no stream candidates resolved", file=sys.stderr)
        return 1

    chosen = None
    for c in candidates:
        if psc.probe_audio(c):
            chosen = c
            break
        print(f"rejected (not audio): {c}", file=sys.stderr)

    previous = OUT.read_text(encoding="utf-8").strip() if OUT.exists() else ""

    if not chosen:
        # Everything failed the audio probe. Keeping the previous value is
        # better than publishing a URL we know is dead.
        print(f"no candidate served audio; keeping previous value: {previous or '(none)'}",
              file=sys.stderr)
        return 0 if previous else 1

    if previous == chosen:
        print(f"unchanged: {chosen}")
    else:
        OUT.write_text(chosen + "\n", encoding="utf-8")
        print(f"updated: {previous or '(none)'} -> {chosen}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001
        print(f"fatal: {exc}", file=sys.stderr)
        sys.exit(1)
