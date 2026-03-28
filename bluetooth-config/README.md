# Raspberry Pi Persistent Bluetooth Auto-Connect

A lightweight, bulletproof solution to ensure your Raspberry Pi remains connected to a specific Bluetooth speaker. If the speaker disconnects, reboots, or moves out of range, this background service will automatically hunt for it and reconnect.

## Prerequisites

Before running the installation script, ensure your Raspberry Pi has already paired with the speaker at least once:

1. Put your speaker in pairing mode.
2. Run `bluetoothctl` on your Pi.
3. Type `scan on` to find your speaker's MAC address.
4. Type `pair <MAC_ADDRESS>`.
5. Keep the MAC address handy for the installation step.

## Installation

You can install and configure the service in one single command. Run this in your Raspberry Pi terminal:

```bash
curl -sSL https://raw.githubusercontent.com/YZAHMED/prayer-times-sync/main/bluetooth-config/install.sh | sudo bash
```

## Verifying the Service

After installation, verify that the service is running:

```bash
sudo systemctl status connect-speaker.service
```

If the service is not running, check the logs:

```bash
sudo journalctl -u connect-speaker.service
```

## Dependencies

Ensure the following dependencies are installed:

```bash
sudo apt-get install -y bluetooth bluez
```


