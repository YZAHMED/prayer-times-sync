# Prayer Times Sync

## Table of Contents
- [The Problem](#-the-problem)
- [The Architecture](#-the-architecture)
- [Key Design Decisions](#-key-design-decisions)
- [Core Components](#-core-components)
- [Edge Device Provisioning](#️-edge-device-provisioning)
- [Future Extensions](#-future-extensions)

---

## 🧠 The Problem

Certain real-world workflows depend on time-critical external data that changes daily:

* **Prayer times** or celestial events
* **Live stream URLs** (ephemeral tokens)
* **Event-based triggers** tied to external APIs

### The Typical Approach
1. Run a backend server on the edge device.
2. Maintain local state and secrets.
3. Handle scheduling, web scraping, and execution simultaneously.

### The Bottleneck
This introduces unnecessary infrastructure overhead, ongoing hosting costs, and complexity at the edge—especially when deploying to low-power IoT devices that are prone to power loss or SD card corruption.

---

## 💡 The Architecture

This project rethinks the architecture completely by decoupling data aggregation from physical execution:

* **Move intelligence to the cloud.** Keep the edge dumb, reliable, and stateless.
* Instead of running complex logic on the device:
  * All heavy computation, scraping, and data preparation happens in a CI/CD pipeline.
  * The edge device acts strictly as a pure executor of precomputed state.

### High-Level Data Flow

```plaintext
[ External APIs + Dynamic Web Players ]
            ↓
[ CI/CD Pipeline (GitHub Actions) ]
   - Scrapes dynamic content via headless Chromium (Puppeteer)
   - Extracts ephemeral stream URLs
   - Computes daily scheduling offsets
            ↓
[ Git Repository (State Layer) ]
   - prayers.json (Core Data)
   - offsets.json (Granular Timing Logic)
   - stream_url.txt (Media Target)
   - volume.txt (Hardware State)
            ↓
[ Edge Device (Raspberry Pi) ]
   - Pulls latest state
   - Wipes and rebuilds cron jobs daily
   - Executes tasks precisely and terminates
```

---

## 🚀 Key Design Decisions

### 1. Stateless Edge Execution
The Raspberry Pi holds no persistent logic, stores no sensitive credentials, and rebuilds its entire execution plan daily. This makes the node highly reliable, trivially easy to reset or redeploy, and entirely resistant to state drift.

### 2. CI/CD as a Compute Layer
Instead of just deploying code, the GitHub Actions pipeline acts as an active data processor. It runs headless browser automation to extract DOM-level values that are otherwise inaccessible via standard API calls, compiling them into a static artifact.

### 3. Idempotent Scheduling
Every day at 2:30 AM, existing execution jobs are safely wiped using targeted process filtering, and a fresh schedule is generated. No duplication, no memory leaks, no hidden state.

### 4. Remote State Configuration
Every aspect of the edge device's behavior—from the volume of the audio output to granular, per-event timing offsets—is controlled via configuration files in this repository. The edge device requires zero SSH intervention to adjust its behavior.

### 5. Zero Backend, Zero Hosting Cost
By utilizing GitHub as the state layer and CI/CD for compute, this architecture requires no servers, no databases, and no internal APIs to maintain.

---

## 🔧 Core Components

- **`update_prayers.mjs`**: Fetches daily temporal data from an external API, normalizes it via timezone-aware date formatting, and stores it as structured JSON.

- **`stream_link_retriever.js`**: Orchestrates headless Chromium to navigate dynamic Single Page Applications (SPAs), bypass autoplay restrictions, and intercept ephemeral network requests to extract media URLs.

- **`prayer_stream.sh`**: The edge execution script. Pulls the latest Git state, parses JSON via jq, calculates exact minute-offsets, and injects self-terminating tasks (timeout) into the Linux cron scheduler.

- **`setup.sh`**: A single-command bootstrapping script for instant edge provisioning.

---

## 🛠️ Edge Device Provisioning

If an edge node dies, you simply replace it. There is no state to migrate and no secrets to onboard.

### Automated Provisioning

Run this single command on a fresh Debian/Raspberry Pi OS installation to fully provision the device in under 60 seconds:

```bash
curl -sL https://raw.githubusercontent.com/YZAHMED/prayer-times-sync/main/setup.sh | sudo bash
```

(This automates package installation, pulls the orchestration script, applies executable permissions, and initializes the self-healing cron queue).

### Manual Provisioning Fallback

If you prefer to provision manually:

```bash
sudo apt update && sudo apt install curl jq mpv -y

sudo curl -o /usr/local/bin/prayer_stream.sh https://raw.githubusercontent.com/YZAHMED/prayer-times-sync/main/prayer_stream.sh

sudo chmod +x /usr/local/bin/prayer_stream.sh

/usr/local/bin/prayer_stream.sh
```

---

## 🔮 Future Extensions

- Multi-location support (fleet management via Git branches)
- Redundant edge nodes
- Web dashboard for monitoring repository state

---

## 🧩 Final Thought

This project is a proof of concept for a broader engineering philosophy: You do not always need heavy infrastructure to build highly reliable, real-world distributed systems.

## Installation

Run the following command to install the setup script:

```bash
curl -sL https://raw.githubusercontent.com/YZAHMED/prayer-times-sync/main/setup.sh | sudo bash
```

## Setting Up Secrets

This project requires the following secrets to fetch prayer times:
- `PRAYER_API_KEY`: Your API key for the prayer times service.
- `PRAYER_API_BASE_URL`: The base URL for the API.

To add these secrets:
1. Go to your repository on GitHub.
2. Navigate to **Settings > Secrets and variables > Actions**.
3. Add the required secrets.

## Troubleshooting Puppeteer

If you encounter issues with Puppeteer, ensure the following dependencies are installed:

```bash
sudo apt-get install -y libnss3 libatk1.0-0 libx11-xcb1 libxcomposite1 libxrandr2 libxdamage1 libgbm1 libasound2
```