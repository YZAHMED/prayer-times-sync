#!/bin/bash
# Exit immediately if any command fails
set -e 

echo "🚀 Starting Automated Edge-Stream Provisioning..."

# 1. Update package lists and install dependencies
echo "📦 Installing required packages (curl, jq, mpv)..."
apt-get update -y
apt-get install -y curl jq mpv

# 2. Download the main orchestration script from your repository
echo "📥 Downloading prayer_stream.sh from GitHub..."
SCRIPT_URL="https://raw.githubusercontent.com/YZAHMED/prayer-times-sync/main/prayer_stream.sh"
DEST_PATH="/usr/local/bin/prayer_stream.sh"

# -sL makes curl silent but forces it to follow redirects (essential for GitHub raw links)
curl -sL "$SCRIPT_URL" -o "$DEST_PATH"

# 3. Apply executable permissions
echo "🔧 Setting execute permissions..."
chmod +x "$DEST_PATH"

# 4. Initialize the system and build the first schedule
echo "⚙️ Initializing local cron schedule..."
$DEST_PATH

echo "✅ Provisioning complete! The edge device is now fully automated."
