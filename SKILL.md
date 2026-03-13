---
name: conference-session-agent
description: Find conference sessions relevant to the user's background and goals. Fetches all sessions from a Rainfocus-powered conference portal (handles auth + "Show more" pagination), saves as local JSON, then uses AI semantic matching to surface the most relevant sessions. Supports NVIDIA GTC 2026 and any Rainfocus conference.
---

# Workflow

```
Step 1a: Identify conference
Step 1b: Resolve config (existing or new)
Step 1c: Fetch sessions (browser opens, user logs in, fetch runs)
Step 2:  Match to user background
Step 3:  Recommend
```

---

## Step 1a — Identify Conference

Glob `conferences/*.json` to list all pre-configured conferences.

If configs exist, say:
> "I found a pre-configured conference: **{conference_name}**. Is that the one you want, or are you looking for a different one? If different, paste your attendee catalog URL — the page in your registration portal where you browse sessions."

If no configs exist, ask:
> "Which conference? Paste your attendee catalog URL — the page in your registration portal where you browse sessions."

---

## Step 1b — Resolve Config

**Config exists** (`conferences/{id}.json` found):
- Confirm: "Found config for {conference_name}. Ready to fetch."
- Skip to Step 1c.

**Config missing** (no file found):
- Tell the user: "No config found for `{id}`. Please paste your attendee catalog URL — the page where you browse sessions after logging in to the registration portal."
- Wait for URL.
- Run to auto-create the config and proceed directly to fetch:
  ```bash
  python fetch_sessions.py \
    --conference {id} \
    --conference-name "{Full Conference Name}" \
    --catalog-url "{url}" \
    --max-sessions {N or omit for all} \
    --no-cache
  ```
  This creates `conferences/{id}.json` with default Rainfocus selectors, then starts the fetch immediately.
- After first run, the config is saved. Future runs need only `--conference {id}`.

---

## Step 1c — Fetch Sessions

Before launching, ask two questions:
1. **How many sessions?** Say `all` for a full fetch, or a number (e.g. `5`) for a quick test.
2. **Fresh or cache?** If sessions were fetched in the last 24h, cached data can be reused. Say `fresh` to force a re-fetch.

Then run the appropriate command from the repo root:

```bash
# All sessions, use cache if fresh (< 24h old)
python fetch_sessions.py --conference {id}

# All sessions, force fresh fetch
python fetch_sessions.py --conference {id} --no-cache

# N sessions only (test mode)
python fetch_sessions.py --conference {id} --max-sessions N --no-cache

# Headless (only if already logged in from a prior run)
python fetch_sessions.py --conference {id} --headless
```

After launching, tell the user:
> "Browser is open — log in if prompted. The fetch starts automatically after login."

Wait for the subprocess to complete. Confirm session count from the output. If count < 50, warn: "Fewer sessions than expected — the catalog may not have fully loaded. Try re-running with `--no-cache`."

---

## Step 2 — Match Sessions to User Background

Read `sessions/{conference_id}.json` (use the Read tool).

Ask the user:
> "Tell me your current role, what you're working on, and what you want to get out of this conference."

Score sessions for relevance based on:
- Title and track alignment to stated work and interests
- Session type (hands-on lab vs. talk vs. panel) if user has a preference
- Day/time constraints if mentioned

Return the top 10–15 sessions ranked by relevance.

---

## Step 3 — Recommend

Output format per session:
```
[{session_code}] {title}
Track: {track} | Day: {day} | Time: {time_slot}
Why: {one sentence relevance reason}
Link: {url}
```

Group by day or track if list is long. Flag hands-on labs separately — they fill up fast.

---

## Output Contract

1. `Fetch status` — cached / freshly fetched, session count, output path
2. `User background` — as stated (confirm before matching)
3. `Ranked sessions` — top 10–15 with relevance notes
4. `Scheduling note` — conflicts, virtual-only flag, sold-out risk for labs

---

## Guardrails

1. Never store credentials — login happens in the browser window the user controls
2. All session matches must come from the JSON file — never hallucinate sessions
3. Confirm user background before scoring — do not guess interests
4. If fetch returns < 50 sessions, warn the user before proceeding to match
5. Sessions JSON is gitignored — it stays on the user's machine only