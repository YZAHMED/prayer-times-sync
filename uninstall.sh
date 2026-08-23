#!/bin/sh
# Remove prayer-sync (v1 and v2) from this device.
#
#   curl -fsSL https://raw.githubusercontent.com/YZAHMED/prayer-times-sync/main/uninstall.sh | sudo sh
#
# Config and logs are kept unless you pass --purge.

set -eu
PURGE=0
[ "${1:-}" = "--purge" ] && PURGE=1
[ "$(id -u)" = "0" ] || { echo "run as root (use sudo)" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }
say() { printf ':: %s\n' "$*"; }

say "Stopping playback"
have pkill && { pkill -f 'prayer-sync' 2>/dev/null || true; pkill -f 'mpv .*mixlr' 2>/dev/null || true; }

say "Removing services"
if have systemctl; then
    for unit in prayer-sync.service connect-speaker.service; do
        systemctl disable --now "$unit" >/dev/null 2>&1 || true
        rm -f "/etc/systemd/system/$unit"
    done
    systemctl daemon-reload >/dev/null 2>&1 || true
fi
if have rc-update; then
    rc-service prayer-sync stop >/dev/null 2>&1 || true
    rc-update del prayer-sync default >/dev/null 2>&1 || true
    rm -f /etc/init.d/prayer-sync
fi

say "Cleaning cron (v1 and v2 entries)"
if have crontab; then
    crontab -l 2>/dev/null \
        | grep -v 'prayer-sync' \
        | grep -v 'prayer_stream.sh' \
        | grep -v 'mpv --volume' \
        | crontab - 2>/dev/null || true
fi

say "Removing binaries and state"
rm -f /usr/local/bin/prayer-sync /usr/local/bin/prayer_stream.sh /usr/local/bin/connect-speaker.sh
rm -rf /var/lib/prayer-sync /var/run/prayer-sync /usr/local/share/prayer-sync/__pycache__

if [ "$PURGE" = "1" ]; then
    say "Purging configuration and logs"
    rm -rf /etc/prayer-sync /usr/local/share/prayer-sync /var/log/prayer-sync.log /var/log/prayer-sync.log.1
else
    say "Kept /etc/prayer-sync and /var/log/prayer-sync.log (re-run with --purge to delete them)"
fi

# Packages (mpv, jq, ...) are intentionally left installed: other things on the
# device may depend on them, and removing them is not ours to decide.
say "Done."
