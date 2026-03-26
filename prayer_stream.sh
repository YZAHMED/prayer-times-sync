#!/bin/sh
# The line above is the "shebang". It tells the OS to use the standard shell to run this file.

# Grab today's day of the week (1=Monday, 7=Sunday)
DAY_OF_WEEK=$(date +%u) 

# Fetch the latest dynamically generated settings from GitHub
STREAM_URL=$(curl -s "https://raw.githubusercontent.com/YZAHMED/prayer-times-sync/main/stream_url.txt")
VOLUME_LEVEL=$(curl -s "https://raw.githubusercontent.com/YZAHMED/prayer-times-sync/main/volume.txt")

# Safety Fallback: If curl fails or volume.txt is empty, default to 100
if [ -z "$VOLUME_LEVEL" ]; then
    VOLUME_LEVEL="100"
fi

CRON_TMP="/tmp/prayer_crontab"
GITHUB_RAW_URL="https://raw.githubusercontent.com/YZAHMED/prayer-times-sync/main/prayers.json"

# 1. Clean the crontab of old stream jobs
# FIX: Searching only for "mpv --volume" ignores the actual number. 
# It will successfully delete the old jobs whether the volume was 50, 100, or 150.
crontab -l 2>/dev/null | grep -v "mpv --volume" | grep -v "/usr/local/bin/prayer_stream.sh" > "$CRON_TMP"

# 2. Schedule THIS script to run every day at 2:30 AM
echo "30 2 * * * /usr/local/bin/prayer_stream.sh" >> "$CRON_TMP"
echo "@reboot sleep 60 && /usr/local/bin/prayer_stream.sh" >> "$CRON_TMP"

# 3. Fetch the public JSON data
# The -s flag means "silent", so curl doesn't print a downloading progress bar to the screen.
JSON_DATA=$(curl -s "$GITHUB_RAW_URL")

# 4. Time conversion helper (converts HH:MM:SS to total minutes)
time_to_mins() {
    IFS=: read -r h m s <<EOF
$1
EOF
    h=${h#0}
    m=${m#0}
    echo $(( h * 60 + m ))
}

# 5. Determine the 5 Daily Prayers
if [ "$DAY_OF_WEEK" -eq 5 ]; then
    MIDDAY_PRAYER="Jumah"
else
    MIDDAY_PRAYER="Dhuhr"
fi

PRAYERS="Fajr $MIDDAY_PRAYER Asr Maghrib Isha"

# 6. Process and Schedule Each Prayer
for PRAYER in $PRAYERS; do
    
    # jq -r extracts raw text. We find the specific prayer and pull out its Adhan and Iqamah strings.
    ADHAN=$(echo "$JSON_DATA" | jq -r ".data.prayerOfDay.singlePrayers[] | select(.prayerName==\"$PRAYER\") | .prayerAdhan // empty")
    IQAMAH=$(echo "$JSON_DATA" | jq -r ".data.prayerOfDay.singlePrayers[] | select(.prayerName==\"$PRAYER\") | .prayerIqamah // empty")

    if [ -n "$ADHAN" ] && [ -n "$IQAMAH" ] && [ "$ADHAN" != "null" ] && [ "$IQAMAH" != "null" ]; then
        
        ADHAN_MINS=$(time_to_mins "$ADHAN")
        IQAMAH_MINS=$(time_to_mins "$IQAMAH")

        # Math: -5 mins for start, +15 for stop
        START_MINS=$((ADHAN_MINS - 5))
        STOP_MINS=$((IQAMAH_MINS + 15))
        DURATION_SEC=$(( (STOP_MINS - START_MINS) * 60 ))

        START_H=$((START_MINS / 60))
        START_M=$((START_MINS % 60))

        # Build the exact crontab string using the dynamic $VOLUME_LEVEL
        # timeout $DURATION_SEC: Automatically kills mpv when the time is up.
        # >/dev/null 2>&1: Plugs the output into a "black hole" so the system doesn't try to email you logs.
        echo "$START_M $START_H * * * /usr/bin/timeout $DURATION_SEC mpv --volume=$VOLUME_LEVEL --af=loudnorm \"$STREAM_URL\" >/dev/null 2>&1" >> "$CRON_TMP"
    fi
done

# 7. Apply the Configuration
# This replaces the live system schedule with our newly built temporary file.
crontab "$CRON_TMP"