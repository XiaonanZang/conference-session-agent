#!/usr/bin/env python3
"""
conference-session-agent — fetch_sessions.py

Authenticates to a Rainfocus-powered conference portal via browser,
clicks "Show more" until all sessions are loaded, extracts structured
session data, and saves it as JSON for AI agent querying.

Usage:
  python fetch_sessions.py --conference gtc2026
  python fetch_sessions.py --conference gtc2026 --no-cache
  python fetch_sessions.py --conference gtc2026 --headless

Supported platforms: Claude Code, Cursor (via MCP), standalone CLI
"""

import asyncio
import json
import re
import sys
import argparse
from pathlib import Path
from datetime import datetime, timezone

from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout


# ─────────────────────────────────────────────
# Config + cache helpers
# ─────────────────────────────────────────────

def load_config(conference_id: str) -> dict | None:
    """Return config dict, or None if no config file exists for this conference."""
    config_path = Path(__file__).parent / "conferences" / f"{conference_id}.json"
    if not config_path.exists():
        return None
    with open(config_path) as f:
        return json.load(f)


def create_default_config(conference_id: str, conference_name: str, catalog_url: str) -> dict:
    """
    Generate a default Rainfocus conference config.
    Default selectors work for most Rainfocus deployments (GTC, re:Invent, VMware Explore, etc.).
    Inspect the portal DOM and update selectors in conferences/{id}.json if sessions don't load.
    """
    from urllib.parse import urlparse
    parsed = urlparse(catalog_url)
    base_url = f"{parsed.scheme}://{parsed.netloc}"

    return {
        "conference_id": conference_id,
        "conference_name": conference_name,
        "catalog_url": catalog_url,
        "base_url": base_url,
        "login_url_pattern": "login|signin|identityiq|sso|auth",
        "selectors": {
            "session_title_text": "div.title-text",
            "session_title_container": "div.catalog-result-title",
            "badge": "div.badge",
            "load_more_button_text": "Show more"
        },
        "session_code_pattern": "\\[([A-Z0-9\\-]+)\\]",
        "timing": {
            "page_load_timeout_ms": 30000,
            "first_session_timeout_ms": 15000,
            "load_more_click_wait_s": 2.5,
            "load_more_stuck_threshold": 3,
            "max_fetch_duration_s": 600,
            "auth_poll_interval_s": 5,
            "auth_wait_max_s": 300
        },
        "cache_ttl_hours": 24,
        "browser": {
            "profile_dir": "sessions/.browser_profile",
            "headless": False
        }
    }


def is_cache_fresh(output_path: Path, cache_ttl_hours: int) -> bool:
    if not output_path.exists():
        return False
    try:
        with open(output_path) as f:
            data = json.load(f)
        extracted_at = datetime.fromisoformat(
            data["meta"]["extracted_at"].replace("Z", "+00:00")
        )
        age_hours = (datetime.now(timezone.utc) - extracted_at).total_seconds() / 3600
        return age_hours < cache_ttl_hours
    except Exception:
        return False


# ─────────────────────────────────────────────
# Session extraction from rendered HTML
# ─────────────────────────────────────────────

def extract_sessions_from_html(html: str, config: dict, base_url: str) -> list[dict]:
    """
    Parse all session cards from the fully-loaded page HTML.
    Uses regex on rendered HTML — avoids fragile Playwright element handle chains.
    """
    sessions = []
    seen_codes = set()

    # Pre-extract descriptions keyed by uppercase session code
    # HTML: <div id="s81902" class="description"><div>TEXT...</div></div>
    desc_map = {}
    for id_val, desc_html in re.findall(
        r'<div id="([^"]+)" class="description">(.*?)</div>\s*</div>',
        html, re.DOTALL
    ):
        text = re.sub(r'<[^>]+>', ' ', desc_html)
        text = re.sub(r'\s+', ' ', text).strip()
        desc_map[id_val.upper()] = text

    # Find all session blocks: URL + title
    # href may be relative (/flow/...) or absolute (https://...) depending on the portal
    matches = re.finditer(
        r'href="(/flow/nvidia/gtc26/ap/page/catalogv/session/[^"]+)"'
        r'[^>]*>.*?'
        r'<div class="title-text">([^<]+)</div>',
        html, re.DOTALL
    )

    code_pattern = config.get("session_code_pattern", r"\[([A-Z0-9\-]+)\]")

    for m in matches:
        raw_url = m.group(1).strip()
        url = raw_url if raw_url.startswith("http") else base_url.rstrip("/") + raw_url
        title_raw = m.group(2).strip().replace("\n", " ").strip()

        # Parse session code
        code_match = re.search(code_pattern, title_raw)
        if code_match:
            session_code = code_match.group(1)
            title = title_raw[:title_raw.rfind("[")].strip()
        else:
            session_code = ""
            title = title_raw

        # Skip duplicates
        dedup_key = session_code or title
        if dedup_key in seen_codes:
            continue
        seen_codes.add(dedup_key)

        # Extract badges from the context before this session's link
        pos = m.start()
        surrounding = html[max(0, pos - 1200):pos]

        time_matches = re.findall(r'class="badge rf-time[^"]*">([^<]+)</div>', surrounding)
        time_str = time_matches[-1].strip() if time_matches else ""

        day_matches = re.findall(r'class="badge rf-day[^"]*">([^<]+)</div>', surrounding)
        day_str = day_matches[-1].strip() if day_matches else ""

        # All badge texts (for track/type)
        all_badges = re.findall(r'<div class="badge[^"]*">([^<]+)</div>', surrounding[-600:])
        all_badges = [b.strip() for b in all_badges if b.strip()]

        # Track = badges that aren't day or time
        tracks = [
            b for b in all_badges
            if b != day_str and b != time_str
            and not re.search(r"\d+:\d+|\d+\s*[ap]\.?m", b, re.IGNORECASE)
            and not re.search(r"\b(Mon|Tue|Wed|Thu|Fri|Sat|Sun)\w*", b)
        ]

        sessions.append({
            "session_code": session_code,
            "title": title,
            "day": day_str,
            "time_slot": time_str,
            "track": tracks,
            "badges_raw": all_badges,
            "description": desc_map.get(session_code, ""),
            "url": url,
            "extracted_at": datetime.now(timezone.utc).isoformat()
        })

    return sessions


# ─────────────────────────────────────────────
# Playwright: auth + load-more loop
# ─────────────────────────────────────────────

async def fetch_all_sessions(config: dict, headless: bool = False, max_sessions: int = 0, save_html: bool = False) -> list[dict]:
    timing = config["timing"]
    browser_cfg = config["browser"]

    # Browser profile stored locally (gitignored) — persists login between runs
    profile_dir = Path(__file__).parent / browser_cfg["profile_dir"]
    profile_dir.mkdir(parents=True, exist_ok=True)

    print(f"Launching browser (profile: {profile_dir})")
    print("If this is your first run, log in when the browser opens.\n")

    async with async_playwright() as p:
        try:
            context = await p.chromium.launch_persistent_context(
                user_data_dir=str(profile_dir),
                headless=headless,
                viewport={"width": 1280, "height": 900},
                args=["--no-first-run", "--no-default-browser-check"]
            )
        except Exception as e:
            print(f"\nFailed to launch browser: {e}")
            print("Tip: If Chromium is already running with this profile, close it first.")
            sys.exit(1)

        page = context.pages[0] if context.pages else await context.new_page()

        # ── Navigate to catalog ──
        print(f"Navigating to {config['catalog_url']} ...")
        try:
            await page.goto(config["catalog_url"], timeout=timing["page_load_timeout_ms"])
        except Exception as e:
            print(f"Navigation error: {e}")
            await context.close()
            sys.exit(1)

        # ── Auth + session wait loop ──
        # Detects login pages two ways:
        #   1. URL matches login_url_pattern (handles known SSO URLs)
        #   2. Page contains a password input field (catches any SSO provider)
        # Only proceeds once sessions are actually visible on the catalog page.
        login_notified = False
        waited = 0
        poll = timing["auth_poll_interval_s"]
        deadline = timing["auth_wait_max_s"]

        while True:
            # Detect login page by URL or by presence of a password field
            on_login_url = bool(re.search(config["login_url_pattern"], page.url, re.IGNORECASE))
            try:
                has_password_field = await page.locator('input[type="password"]').count() > 0
            except Exception:
                has_password_field = False
            on_login_page = on_login_url or has_password_field

            if on_login_page:
                if not login_notified:
                    print("\n" + "="*60)
                    print("  LOGIN REQUIRED")
                    print("  Complete login in the browser window that just opened.")
                    print("  This script will resume automatically after login.")
                    print("="*60 + "\n")
                    login_notified = True
                await asyncio.sleep(poll)
                waited += poll
                if waited > deadline:
                    print("Timed out waiting for login. Please try again.")
                    await context.close()
                    sys.exit(1)
                continue

            # Not on login page — check if sessions are visible
            try:
                session_count = await page.locator(
                    config["selectors"]["session_title_text"]
                ).count()
            except Exception:
                session_count = 0

            if session_count > 0:
                if login_notified:
                    print("✓ Login complete — sessions found.\n")
                break

            # Not on login page but no sessions yet — still loading or mid-redirect
            await asyncio.sleep(2)
            waited += 2
            if waited > deadline:
                print("\nError: No sessions found after waiting.")
                print("Check that you're logged in and the catalog URL is correct.")
                await context.close()
                sys.exit(1)

            # If we just came off a login page, navigate back to catalog
            if login_notified:
                print("  Navigating to catalog...")
                try:
                    await page.goto(config["catalog_url"], timeout=timing["page_load_timeout_ms"])
                    await asyncio.sleep(3)
                except Exception:
                    pass

        # ── Load-more loop ──
        if max_sessions:
            print(f"Loading sessions (test mode: stopping at {max_sessions})...\n")
        else:
            print("Loading all sessions (clicking 'Show more' until done)...\n")
        stuck_count = 0
        prev_count = 0
        max_stuck = timing["load_more_stuck_threshold"]
        load_more_text = config["selectors"]["load_more_button_text"]

        while True:
            # Count current sessions
            current_cards = await page.locator(
                config["selectors"]["session_title_text"]
            ).count()

            print(f"  Sessions loaded: {current_cards}", end="\r", flush=True)

            # Test mode: stop early
            if max_sessions and current_cards >= max_sessions:
                print(f"\n  Test mode: reached {max_sessions} sessions — stopping.")
                break

            # Stuck guard
            if current_cards == prev_count:
                stuck_count += 1
                if stuck_count >= max_stuck:
                    print(f"\n  No new sessions after {max_stuck} tries — stopping.")
                    break
            else:
                stuck_count = 0
                prev_count = current_cards

            # Find "Show more" button
            btn = page.locator(f'button:has-text("{load_more_text}")')
            btn_count = await btn.count()

            if btn_count == 0:
                print(f"\n✓ All sessions loaded (no more 'Show more' button).")
                break

            # Check if disabled
            is_disabled = await btn.first.get_attribute("disabled")
            if is_disabled is not None:
                print(f"\n✓ All sessions loaded ('Show more' is disabled).")
                break

            # Click it
            try:
                await btn.first.scroll_into_view_if_needed()
                await asyncio.sleep(0.3)
                await btn.first.click()
                await asyncio.sleep(timing["load_more_click_wait_s"])
            except Exception as e:
                print(f"\n  Warning: click failed — {e}")
                stuck_count += 1
                if stuck_count >= max_stuck:
                    print("  Too many click failures — stopping.")
                    break

        # ── Extract from rendered HTML ──
        print(f"\nExtracting session data...")
        html = await page.content()
        if save_html:
            html_path = Path(__file__).parent / "sessions" / f"{config['conference_id']}_debug.html"
            html_path.write_text(html, encoding="utf-8")
            print(f"  Debug HTML saved to {html_path}")
        sessions = extract_sessions_from_html(html, config, config["base_url"])

        await context.close()
        return sessions


# ─────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────

async def main():
    parser = argparse.ArgumentParser(
        description="Fetch all sessions from a Rainfocus conference portal"
    )
    parser.add_argument(
        "--conference", required=True,
        help="Conference ID matching a file in conferences/ (e.g. gtc2026)"
    )
    parser.add_argument(
        "--headless", action="store_true",
        help="Run without visible browser window (only works if already logged in)"
    )
    parser.add_argument(
        "--no-cache", action="store_true",
        help="Force re-fetch even if a fresh local cache exists"
    )
    parser.add_argument(
        "--out", default="sessions",
        help="Output directory (default: sessions/)"
    )
    parser.add_argument(
        "--max-sessions", type=int, default=0,
        help="Stop after loading this many sessions (0 = all). Useful for testing."
    )
    parser.add_argument(
        "--save-html", action="store_true",
        help="Save raw page HTML to sessions/{id}_debug.html for extraction debugging"
    )
    parser.add_argument(
        "--catalog-url",
        help="Catalog URL for a new conference (required if no config exists yet)"
    )
    parser.add_argument(
        "--conference-name",
        help="Full conference name for a new conference (required if no config exists yet)"
    )
    args = parser.parse_args()

    config = load_config(args.conference)

    if config is None:
        if not args.catalog_url or not args.conference_name:
            available = [p.stem for p in (Path(__file__).parent / "conferences").glob("*.json")]
            print(f"Error: No config found for '{args.conference}'.")
            print(f"  Known conferences: {available}")
            print(f"  To add a new conference, re-run with:")
            print(f"    --catalog-url <attendee catalog URL>")
            print(f"    --conference-name \"<Full Conference Name>\"")
            sys.exit(1)

        print(f"Creating new config for '{args.conference}'...")
        config = create_default_config(args.conference, args.conference_name, args.catalog_url)
        config_path = Path(__file__).parent / "conferences" / f"{args.conference}.json"
        with open(config_path, "w") as f:
            json.dump(config, f, indent=2)
        print(f"✓ Config saved to {config_path}")
        print(f"  Default Rainfocus selectors applied. If sessions don't load,")
        print(f"  inspect the portal DOM and update selectors in {config_path}\n")

    output_dir = Path(args.out)
    output_dir.mkdir(exist_ok=True)
    output_path = output_dir / f"{args.conference}.json"

    # Cache check
    if not args.no_cache and is_cache_fresh(output_path, config["cache_ttl_hours"]):
        with open(output_path) as f:
            data = json.load(f)
        count = data["meta"]["total_sessions"]
        age = data["meta"]["extracted_at"]
        print(f"✓ Using cached data: {count} sessions (fetched {age})")
        print(f"  Run with --no-cache to force a fresh fetch.")
        return

    start_time = datetime.now(timezone.utc)

    sessions = await fetch_all_sessions(config, headless=args.headless, max_sessions=args.max_sessions, save_html=args.save_html)

    duration = (datetime.now(timezone.utc) - start_time).total_seconds()

    output = {
        "meta": {
            "conference_id": args.conference,
            "conference_name": config["conference_name"],
            "catalog_url": config["catalog_url"],
            "extracted_at": start_time.isoformat(),
            "total_sessions": len(sessions),
            "fetch_duration_s": round(duration, 1)
        },
        "sessions": sessions
    }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"\n{'='*60}")
    print(f"  ✓ Done: {len(sessions)} sessions saved to {output_path}")
    print(f"  Fetch time: {duration:.1f}s")
    print(f"{'='*60}")
    print(f"\nNext step: ask your AI agent to find sessions relevant to your work.")
    print(f"  Claude Code: load SKILL.md and say 'find me sessions for a robotics engineer'")


if __name__ == "__main__":
    asyncio.run(main())