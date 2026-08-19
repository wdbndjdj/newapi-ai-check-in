#!/usr/bin/env python3
"""Idempotent GoRouter daily check-in using a system access token."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo


BASE_URL = os.environ.get("GOROUTER_BASE_URL", "https://gorouter.app").rstrip("/")
TOKEN = os.environ.get("GOROUTER_SYSTEM_ACCESS_TOKEN", "")
USER_ID = os.environ.get("GOROUTER_USER_ID", "")
FORCE_POST = os.environ.get("GOROUTER_FORCE_POST", "false").lower() == "true"
QUOTA_PER_USD = 500_000
TIMEZONE = ZoneInfo("Asia/Shanghai")


def fail(message: str) -> "NoReturn":
    print(f"RESULT=FAILED REASON={message}")
    raise SystemExit(1)


def _safe_message(payload: object) -> str:
    if isinstance(payload, dict):
        value = payload.get("message")
        if isinstance(value, str) and value.strip():
            return " ".join(value.split())[:160]
    return "unexpected API response"


def request_json(method: str, path: str) -> dict:
    url = f"{BASE_URL}{path}"
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {TOKEN}",
        "New-Api-User": USER_ID,
        "User-Agent": "gorouter-checkin/1.0 (+github-actions)",
    }
    body = b"{}" if method == "POST" else None
    if body is not None:
        headers["Content-Type"] = "application/json"

    for attempt, delay in enumerate((0, 5, 15), start=1):
        if delay:
            time.sleep(delay)
        request = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                payload = json.loads(response.read().decode("utf-8"))
                if not isinstance(payload, dict):
                    fail("response is not a JSON object")
                return payload
        except urllib.error.HTTPError as exc:
            raw = exc.read(4096).decode("utf-8", errors="replace")
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                payload = {}
            retryable = exc.code == 429 or 500 <= exc.code < 600
            if retryable and attempt < 3:
                continue
            fail(f"HTTP_{exc.code} {_safe_message(payload)}")
        except (urllib.error.URLError, TimeoutError) as exc:
            if attempt < 3:
                continue
            fail(f"network_error {type(exc).__name__}")

    fail("request retries exhausted")


async def solve_turnstile_token() -> str:
    """Get a short-lived Turnstile token from the site's own login page."""
    try:
        from camoufox.async_api import AsyncCamoufox
        from playwright_captcha import CaptchaType, ClickSolver, FrameworkType
    except ImportError:
        fail("Turnstile browser dependencies are unavailable")

    print("TURNSTILE=START")
    async with AsyncCamoufox(
        headless=False,
        humanize=True,
        locale="zh-CN",
        os="windows",
        config={"forceScopeAccess": True},
    ) as browser:
        page = await browser.new_page()
        async with ClickSolver(
            framework=FrameworkType.CAMOUFOX,
            page=page,
            max_attempts=5,
            attempt_delay=3,
        ) as solver:
            await page.goto(f"{BASE_URL}/login", wait_until="domcontentloaded", timeout=60_000)
            await page.wait_for_timeout(4_000)

            async def read_token() -> str:
                for selector in (
                    'input[name="cf-turnstile-response"]',
                    'textarea[name="cf-turnstile-response"]',
                ):
                    element = await page.query_selector(selector)
                    if element:
                        value = await element.input_value()
                        if value:
                            return value
                return ""

            token = await read_token()
            if not token:
                await solver.solve_captcha(
                    captcha_container=page,
                    captcha_type=CaptchaType.CLOUDFLARE_TURNSTILE,
                )
                for _ in range(30):
                    token = await read_token()
                    if token:
                        break
                    await page.wait_for_timeout(1_000)

            await page.close()
            if not token:
                fail("Turnstile token was not produced")
            print("TURNSTILE=SOLVED")
            return token


def read_status(today: str) -> tuple[dict, dict]:
    month = today[:7]
    payload = request_json("GET", f"/api/user/checkin?month={urllib.parse.quote(month)}")
    if payload.get("success") is not True:
        fail(_safe_message(payload))
    data = payload.get("data")
    if not isinstance(data, dict) or data.get("enabled") is not True:
        fail("check-in is disabled")
    stats = data.get("stats")
    if not isinstance(stats, dict):
        fail("missing check-in status")
    return data, stats


def today_record(stats: dict, today: str) -> dict:
    records = stats.get("records")
    if not isinstance(records, list):
        return {}
    for record in records:
        if isinstance(record, dict) and record.get("checkin_date") == today:
            return record
    return {}


def print_success(state: str, stats: dict, today: str) -> None:
    record = today_record(stats, today)
    quota = int(record.get("quota_awarded", 0) or 0)
    total = int(stats.get("total_checkins", stats.get("checkin_count", 0)) or 0)
    print(
        f"RESULT={state} DATE={today} AWARDED_QUOTA={quota} "
        f"AWARDED_USD={quota / QUOTA_PER_USD:.2f} TOTAL_CHECKINS={total}"
    )


def main() -> int:
    if not TOKEN or not USER_ID:
        fail("required secret is missing")
    if not USER_ID.isdigit():
        fail("user id is invalid")

    today = datetime.now(TIMEZONE).date().isoformat()
    _, stats = read_status(today)
    if stats.get("checked_in_today") is True and not FORCE_POST:
        print_success("ALREADY_CHECKED", stats, today)
        return 0

    turnstile_token = asyncio.run(solve_turnstile_token())
    encoded = urllib.parse.quote(turnstile_token, safe="")
    payload = request_json("POST", f"/api/user/checkin?turnstile={encoded}")
    already_checked = "已签到" in str(payload.get("message", ""))
    if payload.get("success") is not True and not already_checked:
        fail(_safe_message(payload))

    _, verified_stats = read_status(today)
    if verified_stats.get("checked_in_today") is not True:
        fail("server did not verify today's check-in")
    print_success("CHECKED_IN", verified_stats, today)
    return 0


if __name__ == "__main__":
    sys.exit(main())
