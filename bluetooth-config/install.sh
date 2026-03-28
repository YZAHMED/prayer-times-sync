#!/bin/bash

# 1. Check if the script is running with root privileges

if [ "$EUID" -ne 0 ]; then
  echo "Error: This script must be run as root."
  echo "Please run it using: sudo bash $0"
  exit 1
fi

echo "====================================================="
echo "   Persistent Bluetooth Speaker Setup for Raspberry Pi"
echo "====================================================="
echo ""

# 2. Prompt the user for the specific MAC address
read -p "Enter the MAC address of your Bluetooth speaker (e.g., 41:42:8C:C6:B2:80): " MAC_ADDRESS


if [[ -z "$MAC_ADDRESS" ]]; then
  echo "Error: MAC address cannot be empty."
  exit 1
fi

echo ""
echo "Trusting device $MAC_ADDRESS in Bluetooth daemon..."
bluetoothctl trust "$MAC_ADDRESS"

echo "Creating the persistent background script..."


# 3. Create the bash script, injecting the provided MAC address
cat << EOF > /usr/local/bin/connect-speaker.sh
#!/bin/bash

SPEAKER_MAC="${MAC_ADDRESS}"

# Infinite loop to run in the background
while true; do
    if ! bluetoothctl info "\$SPEAKER_MAC" | grep -q "Connected: yes"; then
        bluetoothctl connect "\$SPEAKER_MAC"
    fi
    sleep 30
done
EOF

# Make it executable
chmod +x /usr/local/bin/connect-speaker.sh

echo "Creating the systemd service file..."
# 4. Create the systemd service file
cat << EOF > /etc/systemd/system/connect-speaker.service
[Unit]
Description=Persistent Bluetooth Speaker Connection
After=bluetooth.target
Requires=bluetooth.target

[Service]
Type=simple
ExecStart=/usr/local/bin/connect-speaker.sh
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF


echo "Reloading systemd and starting the service..."
# 5. Reload systemd, enable and start the service

ystemctl daemon-reload
systemctl enable connect-speaker.service
systemctl restart connect-speaker.service

echo ""
echo "====================================================="
echo "Setup Complete!"
echo "Your Raspberry Pi will now automatically keep the"
echo "connection alive with $MAC_ADDRESS."
echo "====================================================="



