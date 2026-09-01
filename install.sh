#!/bin/sh
# prayer-sync installer — Raspberry Pi OS, Debian/Ubuntu (bare metal, VM or
# Proxmox LXC), Fedora, Arch and Alpine. Safe to re-run at any time.
#
#   curl -fsSL https://raw.githubusercontent.com/YZAHMED/prayer-times-sync/main/install.sh | sudo sh
#
# Options (flags or environment variables):
#   --repo URL           source repository raw base   (PS_REPO)
#   --mosque NAME        mosque preset to use         (PS_MOSQUE)
#   --output MODE        auto|bluetooth|aux|hdmi|usb  (PS_OUTPUT)
#   --bluetooth MAC      pair/trust a speaker         (PS_BT_MAC)
#   --volume N           0-100                        (PS_VOLUME)
#   --user NAME          account that owns audio      (PS_USER)
#   --no-service         install files only
#   --uninstall          remove everything

set -eu

REPO="${PS_REPO:-https://raw.githubusercontent.com/YZAHMED/prayer-times-sync/main}"
MOSQUE="${PS_MOSQUE:-}"
OUTPUT="${PS_OUTPUT:-}"
BT_MAC="${PS_BT_MAC:-}"
VOLUME="${PS_VOLUME:-}"
AUDIO_USER="${PS_USER:-}"
NO_SERVICE=0
DO_UNINSTALL=0

BIN_DIR=/usr/local/bin
CONF_DIR=/etc/prayer-sync
STATE_DIR=/var/lib/prayer-sync
SHARE_DIR=/usr/local/share/prayer-sync
LOG_FILE=/var/log/prayer-sync.log

say()  { printf '\033[1;36m::\033[0m %s\n' "$*"; }
ok()   { printf '   \033[1;32mok\033[0m %s\n' "$*"; }
warn() { printf '   \033[1;33m!!\033[0m %s\n' "$*" >&2; }
fail() { printf '\033[1;31mxx\033[0m %s\n' "$*" >&2; exit 1; }

while [ $# -gt 0 ]; do
    case "$1" in
        --repo)      REPO="$2"; shift 2 ;;
        --mosque)    MOSQUE="$2"; shift 2 ;;
        --output)    OUTPUT="$2"; shift 2 ;;
        --bluetooth) BT_MAC="$2"; shift 2 ;;
        --volume)    VOLUME="$2"; shift 2 ;;
        --user)      AUDIO_USER="$2"; shift 2 ;;
        --no-service) NO_SERVICE=1; shift ;;
        --uninstall) DO_UNINSTALL=1; shift ;;
        -h|--help)   sed -n '2,25p' "$0"; exit 0 ;;
        *) fail "unknown option: $1" ;;
    esac
done

[ "$(id -u)" = "0" ] || fail "run as root (prefix the command with sudo)"

have() { command -v "$1" >/dev/null 2>&1; }

# --------------------------------------------------------------------------
# Uninstall
# --------------------------------------------------------------------------
if [ "$DO_UNINSTALL" = "1" ]; then
    say "Removing prayer-sync"
    if have systemctl; then
        systemctl stop prayer-sync.service    2>/dev/null || true
        systemctl disable prayer-sync.service 2>/dev/null || true
        rm -f /etc/systemd/system/prayer-sync.service
        systemctl daemon-reload 2>/dev/null || true
    fi
    if have crontab; then
        crontab -l 2>/dev/null | grep -v 'prayer-sync' | crontab - 2>/dev/null || true
    fi
    # Legacy v1 leftovers.
    if have crontab; then
        crontab -l 2>/dev/null | grep -v 'mpv --volume' | grep -v 'prayer_stream.sh' | crontab - 2>/dev/null || true
    fi
    rm -f /usr/local/bin/prayer_stream.sh
    have pkill && pkill -f 'mpv .*mixlr' 2>/dev/null || true
    rm -f "$BIN_DIR/prayer-sync"
    rm -rf "$STATE_DIR" /var/run/prayer-sync "$SHARE_DIR/__pycache__"
    printf 'Keep %s (config) and %s (log)? [Y/n] ' "$CONF_DIR" "$LOG_FILE"
    if [ -t 0 ]; then read -r a; else a=Y; fi
    case "$a" in [Nn]*) rm -rf "$CONF_DIR" "$LOG_FILE" "$SHARE_DIR" ;; esac
    ok "removed"
    exit 0
fi

# --------------------------------------------------------------------------
# Platform detection
# --------------------------------------------------------------------------
say "Detecting platform"
OS_NAME="$( (. /etc/os-release 2>/dev/null && echo "${PRETTY_NAME:-$NAME}") || uname -s)"
ARCH="$(uname -m)"
MODEL="$(tr -d '\0' < /proc/device-tree/model 2>/dev/null || true)"
VIRT="$(systemd-detect-virt 2>/dev/null || echo unknown)"
ok "$OS_NAME ($ARCH)${MODEL:+, $MODEL}${VIRT:+, virt=$VIRT}"

PKG=none
for p in apt-get apk dnf yum pacman zypper; do have "$p" && { PKG="$p"; break; }; done
INIT=none
if have systemctl && [ -d /run/systemd/system ]; then INIT=systemd
elif have rc-update; then INIT=openrc
elif have crontab; then INIT=cron
fi
ok "package manager: $PKG, init: $INIT"
[ "$INIT" = none ] && warn "no systemd/openrc/cron found — you will have to start prayer-sync yourself"

# --------------------------------------------------------------------------
# Which account owns the sound server?
#
# On a desktop Raspberry Pi, PipeWire/PulseAudio runs inside a user session.
# A service running as root cannot see it, which silently breaks Bluetooth
# audio. So we run the service as that user and keep their session alive.
# --------------------------------------------------------------------------
detect_audio_user() {
    [ -n "$AUDIO_USER" ] && { echo "$AUDIO_USER"; return; }
    for proc in pipewire pulseaudio wireplumber; do
        u=$(ps -eo user=,comm= 2>/dev/null | $(command -v awk) -v c="$proc" '$2==c && $1!="root"{print $1; exit}')
        [ -n "${u:-}" ] && { echo "$u"; return; }
    done
    [ -n "${SUDO_USER:-}" ] && [ "$SUDO_USER" != root ] && { echo "$SUDO_USER"; return; }
    if have getent; then
        u=$(getent group audio 2>/dev/null | cut -d: -f4 | tr ',' '\n' | grep -v '^root$' | head -1)
        [ -n "${u:-}" ] && { echo "$u"; return; }
    fi
    echo root
}
AUDIO_USER="$(detect_audio_user)"
AUDIO_UID="$(id -u "$AUDIO_USER" 2>/dev/null || echo 0)"
ok "audio account: $AUDIO_USER (uid $AUDIO_UID)"

# --------------------------------------------------------------------------
# Migrate away from v1
#
# v1 rebuilt the root crontab every night with one `mpv --volume=...` job per
# prayer. Those entries survive a v2 install and would play a second, unmanaged
# stream on top of this one, so they must go first — on every init system, not
# just the cron fallback.
# --------------------------------------------------------------------------
say "Removing any v1 installation"
if have crontab; then
    if crontab -l 2>/dev/null | grep -qE 'mpv --volume|prayer_stream\.sh'; then
        crontab -l 2>/dev/null \
            | grep -v 'mpv --volume' \
            | grep -v 'prayer_stream.sh' \
            | crontab - 2>/dev/null && ok "removed v1 cron jobs" || warn "could not clean the crontab"
    else
        ok "no v1 cron jobs found"
    fi
fi
[ -f /usr/local/bin/prayer_stream.sh ] && rm -f /usr/local/bin/prayer_stream.sh && ok "removed /usr/local/bin/prayer_stream.sh"
# v1's separate Bluetooth reconnect daemon polled every 30 seconds forever;
# prayer-sync now reconnects on demand before each prayer instead.
if have systemctl && systemctl list-unit-files 2>/dev/null | grep -q '^connect-speaker.service'; then
    systemctl disable --now connect-speaker.service >/dev/null 2>&1 || true
    rm -f /etc/systemd/system/connect-speaker.service /usr/local/bin/connect-speaker.sh
    systemctl daemon-reload >/dev/null 2>&1 || true
    ok "removed the old connect-speaker service"
fi

# --------------------------------------------------------------------------
# Dependencies
# --------------------------------------------------------------------------
say "Installing dependencies"
BASE="curl python3 mpv ca-certificates tzdata"
case "$PKG" in
    apt-get)
        BASE="$BASE alsa-utils"
        [ -n "$BT_MAC" ] && BASE="$BASE bluez pipewire-pulse"
        export DEBIAN_FRONTEND=noninteractive
        apt-get update -qq || warn "apt-get update failed; continuing with what is cached"
        # Install individually so one unavailable package cannot block the rest.
        for p in $BASE; do
            apt-get install -y -qq --no-install-recommends "$p" >/dev/null 2>&1 && ok "$p" || warn "could not install $p"
        done
        ;;
    apk)
        apk update >/dev/null 2>&1 || true
        for p in curl python3 tzdata mpv ca-certificates alsa-utils; do
            apk add --no-cache "$p" >/dev/null 2>&1 && ok "$p" || warn "could not install $p"
        done
        ;;
    dnf|yum)
        for p in curl python3 mpv ca-certificates tzdata alsa-utils; do
            "$PKG" install -y -q "$p" >/dev/null 2>&1 && ok "$p" || warn "could not install $p"
        done
        ;;
    pacman)
        pacman -Sy --noconfirm --needed curl python mpv ca-certificates tzdata alsa-utils >/dev/null 2>&1 || warn "pacman install had failures"
        ;;
    zypper)
        zypper -n install curl python3 mpv ca-certificates timezone alsa-utils >/dev/null 2>&1 || warn "zypper install had failures"
        ;;
    *)
        warn "unknown package manager — make sure curl and mpv are installed"
        ;;
esac
have curl || have wget || fail "curl (or wget) is required to install"
have mpv  || warn "mpv is missing; prayer-sync will fall back to ffplay/mpg123 if present"

# prayer-sync does all parsing, date maths and prayer-time computation in
# Python. Without it there is nothing to schedule, so fail here rather than
# silently at the first adhan.
PYBIN=""
for p in python3 python; do
    if command -v "$p" >/dev/null 2>&1 &&
       "$p" -c 'import sys,zoneinfo; sys.exit(0 if sys.version_info>=(3,9) else 1)' 2>/dev/null; then
        PYBIN="$p"; break
    fi
done
[ -n "$PYBIN" ] || fail "python3 3.9 or newer is required and could not be installed automatically"
ok "python: $PYBIN ($("$PYBIN" -c 'import sys;print("%d.%d"%sys.version_info[:2])'))"

# --------------------------------------------------------------------------
# Files
# --------------------------------------------------------------------------
say "Installing files"
mkdir -p "$BIN_DIR" "$CONF_DIR/mosques" "$STATE_DIR" "$SHARE_DIR" /var/run/prayer-sync

get() { # get REMOTE_PATH DEST
    if curl -fsSL --retry 3 --retry-delay 2 --connect-timeout 15 --max-time 60 "$REPO/$1" -o "$2.tmp" 2>/dev/null ||
       wget -q -O "$2.tmp" "$REPO/$1" 2>/dev/null; then
        [ -s "$2.tmp" ] || { rm -f "$2.tmp"; return 1; }
        mv -f "$2.tmp" "$2"; return 0
    fi
    rm -f "$2.tmp"; return 1
}

# Running from a git checkout? Prefer the local files.
SRC_DIR="$(unset CDPATH; cd -- "$(dirname -- "$0")" 2>/dev/null && pwd)"
if [ -f "$SRC_DIR/edge/prayer-sync" ]; then
    cp "$SRC_DIR/edge/prayer-sync" "$BIN_DIR/prayer-sync"
    cp "$SRC_DIR/edge/prayer_sync_core.py" "$SHARE_DIR/prayer_sync_core.py"
    # config.json is fleet-wide and is refreshed from the repo; per-device
    # settings belong in config.local.json, which is never overwritten.
    [ -f "$SRC_DIR/config.json" ] && cp "$SRC_DIR/config.json" "$CONF_DIR/config.json"
    for m in "$SRC_DIR"/mosques/*.json; do [ -f "$m" ] && cp "$m" "$CONF_DIR/mosques/"; done
    for a in "$SRC_DIR"/share/*.mp3; do [ -f "$a" ] && cp "$a" "$SHARE_DIR/"; done
    ok "installed from local checkout"
else
    get "edge/prayer-sync" "$BIN_DIR/prayer-sync" || fail "could not download prayer-sync from $REPO"
    get "edge/prayer_sync_core.py" "$SHARE_DIR/prayer_sync_core.py" || fail "could not download prayer_sync_core.py from $REPO"
    get "config.json" "$CONF_DIR/config.json"     || warn "could not download config.json"
    # Adhan fallback files — used only when every stream candidate fails. If
    # they don't download the daemon still runs, just silent in that edge case.
    get "share/adhan.mp3"      "$SHARE_DIR/adhan.mp3"      || warn "could not download adhan.mp3 fallback"
    get "share/adhan-fajr.mp3" "$SHARE_DIR/adhan-fajr.mp3" || warn "could not download adhan-fajr.mp3 fallback"
    ok "downloaded from $REPO"
fi
chmod 0755 "$BIN_DIR/prayer-sync"
chmod 0644 "$SHARE_DIR/prayer_sync_core.py"

# Sanity-check before wiring it into init: a corrupted download must not be
# promoted to a running service.
sh -n "$BIN_DIR/prayer-sync" || fail "downloaded prayer-sync failed its syntax check"
"$PYBIN" -m py_compile "$SHARE_DIR/prayer_sync_core.py" || fail "prayer_sync_core.py failed its syntax check"

# Fetch the mosque preset named by the config.
if [ -z "$MOSQUE" ] && [ -f "$CONF_DIR/config.json" ]; then
    MOSQUE=$(sed -n 's/.*"mosque"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$CONF_DIR/config.json" | head -1)
fi
MOSQUE="${MOSQUE:-masjid-el-noor}"
[ -f "$CONF_DIR/mosques/$MOSQUE.json" ] || get "mosques/$MOSQUE.json" "$CONF_DIR/mosques/$MOSQUE.json" \
    || warn "no preset for '$MOSQUE'; add $CONF_DIR/mosques/$MOSQUE.json yourself"

# Device-local overrides live in their own file so a config refresh from the
# repository can never clobber them.
if [ -n "$OUTPUT" ] || [ -n "$VOLUME" ] || [ -n "$BT_MAC" ] || [ -n "$MOSQUE" ]; then
    tmp="$CONF_DIR/config.local.json"
    {
        printf '{\n'
        printf '  "$comment": "Device-local overrides. Never overwritten by refresh.",\n'
        [ -n "$MOSQUE" ] && printf '  "mosque": "%s",\n' "$MOSQUE"
        printf '  "audio": {\n'
        printf '    "output": "%s"' "${OUTPUT:-auto}"
        [ -n "$VOLUME" ] && printf ',\n    "volume": %s' "$VOLUME"
        [ -n "$BT_MAC" ] && printf ',\n    "bluetooth_mac": "%s"' "$BT_MAC"
        printf '\n  }\n}\n'
    } > "$tmp"
    ok "wrote $tmp"
fi

touch "$LOG_FILE" 2>/dev/null || true
chown -R "$AUDIO_USER" "$STATE_DIR" /var/run/prayer-sync 2>/dev/null || true
chown "$AUDIO_USER" "$LOG_FILE" 2>/dev/null || true
chmod 0644 "$LOG_FILE" 2>/dev/null || true

# --------------------------------------------------------------------------
# Bluetooth speaker
# --------------------------------------------------------------------------
if [ -n "$BT_MAC" ]; then
    say "Configuring Bluetooth speaker $BT_MAC"
    if have bluetoothctl; then
        have rfkill && rfkill unblock bluetooth 2>/dev/null || true
        systemctl enable --now bluetooth 2>/dev/null || true
        bluetoothctl power on        >/dev/null 2>&1 || true
        bluetoothctl --timeout 5 scan on >/dev/null 2>&1 || true
        bluetoothctl pair  "$BT_MAC" >/dev/null 2>&1 || true
        bluetoothctl trust "$BT_MAC" >/dev/null 2>&1 || true
        bluetoothctl connect "$BT_MAC" >/dev/null 2>&1 || true
        if bluetoothctl info "$BT_MAC" 2>/dev/null | grep -q 'Connected: yes'; then
            ok "connected"
        else
            warn "not connected yet — put the speaker in pairing mode and run: bluetoothctl pair $BT_MAC"
        fi
        # prayer-sync reconnects on its own before every prayer, so no extra
        # reconnect daemon is needed.
    else
        warn "bluetoothctl not installed"
    fi
fi

# --------------------------------------------------------------------------
# Service
# --------------------------------------------------------------------------
if [ "$NO_SERVICE" = "1" ]; then
    say "Skipping service installation (--no-service)"
elif [ "$INIT" = systemd ]; then
    say "Installing systemd service"
    if [ "$AUDIO_USER" != root ] && have loginctl; then
        # Without lingering the user session (and its PipeWire) does not exist
        # until somebody logs in, so audio would be dead after an unattended boot.
        loginctl enable-linger "$AUDIO_USER" 2>/dev/null && ok "lingering enabled for $AUDIO_USER" || true
    fi
    cat > /etc/systemd/system/prayer-sync.service <<UNIT
[Unit]
Description=Prayer time stream scheduler
Documentation=https://github.com/YZAHMED/prayer-times-sync
After=network-online.target sound.target bluetooth.target time-sync.target
Wants=network-online.target

[Service]
Type=simple
User=$AUDIO_USER
ExecStart=$BIN_DIR/prayer-sync run
Restart=always
RestartSec=15
TimeoutStopSec=20
Environment=XDG_RUNTIME_DIR=/run/user/$AUDIO_UID
Environment=PULSE_RUNTIME_PATH=/run/user/$AUDIO_UID/pulse
Environment=HOME=$(getent passwd "$AUDIO_USER" 2>/dev/null | cut -d: -f6 || echo /root)
StateDirectory=prayer-sync
RuntimeDirectory=prayer-sync
# Survive transient failures rather than entering a permanent failed state.
StartLimitIntervalSec=0

[Install]
WantedBy=multi-user.target
UNIT
    systemctl daemon-reload
    systemctl enable prayer-sync.service >/dev/null 2>&1 || warn "could not enable service"
    systemctl restart prayer-sync.service || warn "could not start service — check: journalctl -u prayer-sync -n 50"
    sleep 2
    systemctl is-active --quiet prayer-sync.service && ok "prayer-sync.service running" || warn "service is not active"
elif [ "$INIT" = openrc ]; then
    say "Installing OpenRC service"
    cat > /etc/init.d/prayer-sync <<'RC'
#!/sbin/openrc-run
name="prayer-sync"
command="/usr/local/bin/prayer-sync"
command_args="run"
command_background=true
pidfile="/run/prayer-sync.pid"
depend() { need net; after bluetooth; }
RC
    chmod +x /etc/init.d/prayer-sync
    rc-update add prayer-sync default >/dev/null 2>&1 || true
    rc-service prayer-sync restart >/dev/null 2>&1 || warn "could not start service"
    ok "openrc service installed"
else
    say "Installing cron fallback (no systemd on this host)"
    CRON_TMP=$(mktemp 2>/dev/null || echo /tmp/ps-cron.$$)
    crontab -l 2>/dev/null | grep -v 'prayer-sync' | grep -v 'prayer_stream.sh' | grep -v 'mpv --volume' > "$CRON_TMP" || true
    # 'once' is a no-op outside a prayer window and self-locks, so a minutely
    # tick is cheap and needs no schedule rewriting.
    echo "* * * * * $BIN_DIR/prayer-sync once >/dev/null 2>&1" >> "$CRON_TMP"
    echo "@reboot $BIN_DIR/prayer-sync refresh >/dev/null 2>&1" >> "$CRON_TMP"
    crontab "$CRON_TMP" && ok "cron entries installed" || warn "could not install crontab"
    rm -f "$CRON_TMP"
fi

# --------------------------------------------------------------------------
# First run
# --------------------------------------------------------------------------
say "Fetching initial data"
"$BIN_DIR/prayer-sync" refresh >/dev/null 2>&1 || warn "initial refresh failed (offline computation will cover it)"

echo
"$BIN_DIR/prayer-sync" doctor || true
echo
say "Done."
cat <<NEXT

  prayer-sync today          what will play today
  prayer-sync devices        audio outputs, in the order they will be tried
  prayer-sync test-audio     play the live stream now for 15 seconds
  prayer-sync doctor         full health check
  journalctl -u prayer-sync -f     live logs

  Config: $CONF_DIR/config.json
          $CONF_DIR/config.local.json   (this device only)
NEXT
