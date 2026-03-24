# 🕌 Automated Edge-Stream Scheduler

[![CI/CD Pipeline](https://img.shields.io/badge/CI%2FCD-GitHub_Actions-blue.svg)](https://github.com/features/actions)
[![Edge Device](https://img.shields.io/badge/Edge-Raspberry_Pi-C51A4A.svg)](https://www.raspberrypi.org/)
[![Status](https://img.shields.io/badge/Status-Production_Ready-success.svg)]()

A zero-touch, fault-tolerant automation pipeline designed to synchronize dynamic, daily schedules from a remote API and trigger precise audio streams on a headless edge device (Raspberry Pi). 

## 📖 Overview
Scheduling media streams based on static times is simple, but scheduling them based on dynamically shifting daily data (like celestial events or prayer times) typically requires heavy, resource-intensive servers. 

This project solves that by decoupling the data-fetching logic from the execution logic. It utilizes a **stateless architecture** where a cloud CI/CD pipeline acts as the single source of truth, and a lightweight edge device handles the physical execution.

## 🏗️ Architecture & Data Flow

1. **Cloud Synchronization (GitHub Actions):** A scheduled cloud runner wakes up daily at 2:00 AM EST, securely authenticates with the remote API, parses the JSON payload, and commits the updated schedule to this repository as a static artifact.
2. **Edge Provisioning (Raspberry Pi):** At 2:30 AM EST, the local edge device pulls the latest static artifact, avoiding the need to store API secrets locally.
3. **Dynamic Task Queueing:** A local parsing script calculates precise start and stop offsets, clears stale jobs, and injects the new stream tasks into the Linux `cron` scheduler.
4. **Resilience / Self-Healing:** System reboots or power failures trigger a recovery protocol (`@reboot` cron directive) that delays execution until network connectivity is restored, then automatically reconstructs the day's remaining schedule.

## ✨ Key Features
* **Zero-Touch Operation:** Once deployed, the system requires zero human intervention to maintain daily accuracy.
* **Fault-Tolerant:** Survives unexpected power outages and automatically rebuilds its own execution queue upon reboot.
* **Secure Secrets Management:** API keys are injected via GitHub Secrets at runtime, keeping the edge device and public repository completely credential-free.
* **Audio Normalization:** Implements native `mpv` audio filters (`--af=loudnorm`) to compress and normalize live audio streams for consistent hardware output.
* **Zombie Process Prevention:** Utilizes the Linux `timeout` utility to guarantee stream termination, preventing overlapping or hanging background processes.

## 💻 Tech Stack
* **JavaScript / Node.js (Cloud Worker):** Handles asynchronous API fetching and timezone-aware date formatting. 
* **Bash (Edge Scripting):** Native Linux shell scripting for parsing (`jq`), downloading (`curl`), and scheduling.
* **GitHub Actions (CI/CD):** Serverless automation and cron scheduling.
* **Linux / Cron:** Core operating system scheduling and process management.

## 🚀 Deployment

### Cloud Setup
1. Fork this repository.
2. Add your API key to GitHub Secrets as `PRAYER_API_KEY`.
3. Enable GitHub Actions. The workflow will automatically generate `prayers.json` daily.

### Edge Device Setup (Raspberry Pi)
1. Install dependencies: `sudo apt install curl jq mpv`
2. Download the edge script:
   ```bash
   sudo curl -o /usr/local/bin/prayer_stream.sh [https://raw.githubusercontent.com/YOUR_REPO/main/prayer_stream.sh](https://raw.githubusercontent.com/YOUR_REPO/main/prayer_stream.sh)
   sudo chmod +x /usr/local/bin/prayer_stream.sh
Run the script once to initialize the self-healing cron schedule: /usr/local/bin/prayer_stream.sh
