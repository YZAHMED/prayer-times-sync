⚡ Stateless Edge Orchestration for Time-Critical Media Streams

A production-grade system designed to reliably trigger real-world events based on dynamic, time-sensitive data — without running a traditional backend.

🧠 The Problem

Certain real-world workflows depend on time-critical external data that changes daily:

Prayer times
Live stream URLs (ephemeral tokens)
Event-based triggers tied to external APIs

The typical approach:

Run a backend server
Maintain state
Handle scheduling + scraping + execution

This introduces:

Infrastructure overhead
Cost
Complexity at the edge (especially on low-power devices)
💡 The Approach

This project rethinks the architecture completely:

Move intelligence to the cloud. Keep the edge dumb, reliable, and stateless.

Instead of running logic on the device:

All heavy computation, scraping, and data preparation happens in CI/CD
The edge device becomes a pure executor of precomputed state
🏗️ Architecture (High-Level)
External APIs + Dynamic Web Players
            ↓
   CI/CD Pipeline (GitHub Actions)
   - Scrapes dynamic content (Puppeteer)
   - Extracts stream URLs
   - Computes daily schedule
            ↓
      Git Repository (State Layer)
   - prayers.json
   - stream_url.txt
   - volume.txt
            ↓
   Edge Device (Raspberry Pi)
   - Pulls latest state
   - Rebuilds cron jobs daily
   - Executes tasks at exact times
🚀 Key Design Decisions
1. Stateless Edge Execution

The Raspberry Pi:

Holds no persistent logic
Stores no sensitive credentials
Rebuilds its entire execution plan daily

This makes it:

Highly reliable
Easy to reset/redeploy
Resistant to drift or corruption
2. CI/CD as a Compute Layer

Instead of just deploying code, the pipeline:

Acts as a data processor
Runs headless browser automation (Puppeteer)
Extracts values that are otherwise inaccessible (SPA/network calls)
3. Idempotent Scheduling

Every day:

Existing cron jobs are wiped safely
A fresh schedule is generated

No duplication. No drift. No hidden state.

4. Zero Backend, Zero Hosting Cost
No servers
No databases
No APIs to maintain

Git becomes the source of truth.

🔧 Core Components
🔹 update_prayers.mjs
Fetches daily prayer times from external API
Normalizes and stores them as structured JSON
🔹 stream_link_retriever.js
Uses headless Chromium (Puppeteer)
Navigates dynamic player pages
Extracts ephemeral stream URLs
🔹 GitHub Actions Workflows
Scheduled automation (CRON)
Runs scraping + data updates
Commits fresh state back to repo
🔹 prayer_stream.sh
Runs on Raspberry Pi
Pulls latest state
Rebuilds execution schedule
Triggers playback at exact times
⚙️ Why This Matters

This pattern is powerful beyond this use case:

It can be applied to:

Digital signage systems
Smart home automations
Event-triggered IoT systems
Low-cost distributed devices
🧪 Engineering Principles Applied
Separation of concerns (compute vs execution)
Idempotency (safe repeated runs)
Stateless design
Edge reliability over edge intelligence
Cost minimization without sacrificing capability
📦 Deployment Philosophy

If the Raspberry Pi dies, replace it — no migration needed.

Setup is intentionally minimal:

Flash OS
Add cron job
Done

No secrets. No onboarding complexity.

🔮 Future Extensions
Multi-location support
Redundant edge nodes
Real-time fallback streams
Web dashboard for monitoring
Event-driven (non-cron) triggers
🧩 Final Thought

This project isn’t about prayer times or streaming.

It’s about proving that:

You don’t need heavy infrastructure to build reliable, real-world systems.