#!/bin/bash
# Exit immediately if a critical command fails
set -e 

echo "🧹 Starting Automated Edge-Stream Teardown..."

# 1. Stop any actively playing streams
echo "🛑 Halting active media processes..."
pkill mpv || true

# 2. Clean the root crontab safely
echo "🗑️ Wiping orchestration schedule..."
crontab -l 2>/dev/null | grep -v "mpv --volume" | grep -v "/usr/local/bin/prayer_stream.sh" | crontab - || true

# 3. Delete the orchestration script
echo "🔥 Removing executable binaries..."
rm -f /usr/local/bin/prayer_stream.sh

# 4. Uninstall dependencies (keeping curl)
echo "📦 Uninstalling mpv and jq..."
apt-get remove --purge -y mpv jq
apt-get autoremove -y

echo "✅ Teardown complete! The edge device is back to a clean slate."
