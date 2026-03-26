# 📡 Stateless Edge-Stream Orchestrator

[![CI/CD Pipeline](https://img.shields.io/badge/CI%2FCD-GitHub_Actions-blue.svg)](https://github.com/features/actions)
[![Edge Device](https://img.shields.io/badge/Edge-Raspberry_Pi-C51A4A.svg)](https://www.raspberrypi.org/)
[![Status](https://img.shields.io/badge/Status-Production_Ready-success.svg)]()

A zero-touch, fault-tolerant automation pipeline designed to orchestrate dynamic media streams on a headless edge device (Raspberry Pi). 

## 📖 Overview
Scheduling edge execution based on dynamic, shifting daily data (such as celestial events, dynamic stream URLs, or temporary audio tokens) typically requires deploying heavy, resource-intensive servers directly to the edge. 

This project solves that by decoupling the data-aggregation layer from the edge-execution layer. It utilizes a **stateless IoT architecture**: a cloud CI/CD pipeline acts as the heavy-lifting data aggregator and single source of truth, while a lightweight, credential-free edge device handles physical execution.

## 🏗️ System Architecture & Data Flow

```text
[ External API ]      [ Dynamic SPA Web Player ]
       │                          │
       ▼                          ▼
[ GitHub Actions (Ubuntu Runner) ] ──────▶ Runs Headless Chrome (Puppeteer)
       │ (Daily CRON: Data Aggregation)
       ▼
[ Git Repository ] ◀── Updates JSON & TXT Artifacts (State)
       │
       ▼
[ Edge Device (Raspberry Pi) ] ◀── Fetches state via curl
       │ (Daily CRON: Parsing & Provisioning)
       ▼
[ Local Linux Cron ] ──▶ Idempotent execution queue rebuilt daily
✨ Key Engineering Features
Headless CI/CD Scraping: Utilizes puppeteer within a GitHub Actions Ubuntu runner to navigate dynamic Single Page Applications (SPAs), bypass autoplay policies, and intercept ephemeral network requests to extract daily stream URLs.

Idempotent Edge Scheduling: The edge bash script safely wipes and rebuilds the local cron queue daily using precise grep -v filtering, preventing zombie processes and overlapping stream schedules.

Secure, Credential-Free Edge: API keys and sensitive tokens are injected via GitHub Secrets at the CI/CD level. The edge device only pulls public, sanitized artifacts, entirely eliminating the risk of edge-side secret leakage.

Remote State Management: System configurations (like hardware volume levels) are managed as code in the cloud repository and dynamically applied at the edge, allowing remote administration without SSH access.

Self-Healing & Fault Tolerance: System reboots or power failures trigger a recovery protocol (@reboot cron directive) that delays execution until the network resolves, then automatically reconstructs the day's remaining schedule.

Process Isolation: Utilizes the native Linux timeout utility to guarantee stream termination based on dynamically calculated minute-offsets, ensuring system resources are always freed.

💻 Tech Stack
Cloud Worker / Automation: Node.js (ES6), Puppeteer, GitHub Actions.

Edge Scripting: Advanced Bash, jq (JSON parsing), curl.

Execution Environment: Linux cron, mpv (with --af=loudnorm for native audio compression/normalization).

🚀 Deployment
1. Cloud Infrastructure Setup
Fork this repository.

Add your external API keys to GitHub Secrets (e.g., PRAYER_API_KEY).

Enable GitHub Actions. The workflow will automatically generate and commit prayers.json, stream_url.txt, and volume.txt daily.

2. Edge Device Provisioning (Raspberry Pi)
The edge device requires zero application-level configuration.

Install system dependencies:

Bash
sudo apt update && sudo apt install curl jq mpv -y
Download the provisioning script:

Bash
sudo curl -o /usr/local/bin/prayer_stream.sh [https://raw.githubusercontent.com/YOUR_REPO/main/prayer_stream.sh](https://raw.githubusercontent.com/YOUR_REPO/main/prayer_stream.sh)
sudo chmod +x /usr/local/bin/prayer_stream.sh
Run the script once to initialize the self-healing local queue:

Bash
/usr/local/bin/prayer_stream.sh