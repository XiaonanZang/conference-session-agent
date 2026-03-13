# conference-session-agent

> Stop searching with keywords. Tell an AI agent what you work on and let it find your sessions.

An open-source AI agent skill for **Claude Code**, **Cursor**, and **VS Code Copilot** that logs into your conference portal, loads all sessions, and has a conversation with you to surface the ones most relevant to your work.

---

## The Problem

NVIDIA GTC has 700+ sessions. AWS re:Invent has 2,000+.

Their portals give you keyword search and category filters. You still have to know what to search for, read abstracts one by one, and manually track time conflicts.

**These are conferences about AI agents — but none of them use an AI agent to help you find your sessions.**

This tool is that agent. Describe what you work on. It finds your sessions.

---

## Supported Conferences

This tool is built on the **Rainfocus** conference platform. NVIDIA GTC 2026 is confirmed working and pre-configured. Other Rainfocus-based conferences (including AWS re:Invent, VMware Explore, and Red Hat Summit) share the same portal structure and should work — paste your attendee catalog URL and the agent configures itself.

| Conference | Status |
|---|---|
| NVIDIA GTC 2026 | Confirmed — pre-configured |
| Other Rainfocus conferences | Should work — paste catalog URL on first run |

---

## Privacy & Security

- **Your password is never seen by the agent.** Login happens in a real browser window you control. The agent only detects when login is complete — it never reads, stores, or transmits credentials.
- **All session data stays on your machine.** `sessions/*.json` is gitignored and never committed.
- **Your browser session is local.** The saved login profile lives in `sessions/.browser_profile/` on your machine only (gitignored).
- This tool reads conference data you already have access to as a registered attendee.

---

## Quick Start

### 1. Get the repo

```bash
git clone https://github.com/XiaonanZang/conference-session-agent.git
cd conference-session-agent
```

Open Claude Code from inside this folder — the agent needs to be running at the repo root to find the skill and write session data.

### 2. Open Claude Code and load the skill

```bash
claude
```

Then say:

```
Load SKILL.md
```

The agent reads the skill and installs any missing dependencies automatically (Playwright + Chromium, ~1 min first time).

### 3. The conversation

---

**You:**
> Load SKILL.md

**Agent:**
> Skill loaded. I found a pre-configured conference: **NVIDIA GTC 2026**.
> Is that the one you want, or are you looking for a different conference?
> If different, paste your attendee catalog URL — the page in your registration portal where you browse sessions.

**You:**
> Yes, GTC 2026

*(or paste a URL for a different conference)*

**Agent:**
> Got it. How many sessions would you like to fetch — all (~700), or a smaller number for a quick test?

**You:**
> All

**Agent:**
> Launching browser. Log in when it opens — the fetch starts automatically after login.

*[A browser window opens. You log in to your NVIDIA account. The agent clicks "Show more" until all sessions are loaded.]*

**Agent:**
> ✓ 714 sessions loaded. Now tell me: what's your current role, what are you working on, and what do you want to get out of this conference?

**You:**
> I'm a robotics engineer working on AMR navigation. I want to understand NVIDIA's stack for real-world robot deployment — Isaac Perceptor, nvblox, that area.

**Agent:**
> Here are your top sessions:
>
> **[CWES81568] Build Smarter Robots Using NVIDIA Isaac Sim and Isaac Lab**
> Track: Robotics | Tuesday Mar 17, 2:00 PM
> Why: Covers Isaac Lab sim-to-real transfer — directly relevant to your mapping pipeline validation.
> [Link]
>
> *(+ ranked sessions continue...)*

---

## Requirements

- Python 3.9+
- A valid conference attendee registration

Everything else (Playwright, Chromium) is installed automatically on first run.

---

## License

MIT