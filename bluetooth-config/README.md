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
curl -sSL [https://raw.githubusercontent.com/YOUR_GITHUB_USERNAME/YOUR_REPO_NAME/main/install.sh](https://raw.githubusercontent.com/YOUR_GITHUB_USERNAME/YOUR_REPO_NAME/main/install.sh) | sudo bash