#!/usr/bin/env python3
"""Idempotent GoRouter daily check-in for one workflow account."""

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
COOKIE_HEADER = os.environ.get("GOROUTER_COOKIE_HEADER", "")
ACCOUNT_NAME = os.environ.get("GOROUTER_ACCOUNT_NAME", "gorouter")
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


def request_json(method: str, path: str, *, use_token: bool = False) -> dict:
    url = f"{BASE_URL}{path}"
    headers = {
        "Accept": "application/json",
        "New-Api-User": USER_ID,
        "User-Agent": "gorouter-checkin/1.0 (+github-actions)",
    }
    if not COOKIE_HEADER:
        fail("session cookie is required")
    headers["Cookie"] = COOKIE_HEADER
    if use_token and TOKEN:
        # new-api dashboard access tokens are sent verbatim (they are not
        # OAuth bearer tokens). This also keeps an expired browser session
        # recoverable without changing the primary session-first flow.
        headers["Authorization"] = TOKEN
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
            if exc.code == 401 and isinstance(payload, dict):
                payload["_http_status"] = exc.code
                return payload
            retryable = exc.code == 429 or 500 <= exc.code < 600
            if retryable and attempt < 3:
                continue
            fail(f"HTTP_{exc.code} {_safe_message(payload)}")
        except (urllib.error.URLError, TimeoutError) as exc:
            if attempt < 3:
                continue
            fail(f"network_error {type(exc).__name__}")

    fail("request retries exhausted")


def is_unauthorized(payload: dict) -> bool:
    message = str(payload.get("message", "")).lower()
    return "unauthorized" in message or "未登录" in message or "无权" in message


def authenticated_request(method: str, path: str) -> dict:
    payload = request_json(method, path)
    if payload.get("success") is not True and is_unauthorized(payload) and TOKEN:
        payload = request_json(method, path, use_token=True)
    return payload


def needs_turnstile(payload: dict) -> bool:
    message = str(payload.get("message", "")).lower()
    return "turnstile" in message or "验证令牌" in message


async def solve_turnstile_token() -> str:
    try:
        from camoufox.async_api import AsyncCamoufox
        from playwright_captcha import CaptchaType, ClickSolver, FrameworkType
    except ImportError:
        fail("Turnstile browser dependencies are unavailable")

    print(f"ACCOUNT={ACCOUNT_NAME} TURNSTILE=START")
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
            max_attempts=1,
            attempt_delay=3,
        ) as solver:
            await page.goto(f"{BASE_URL}/sign-in", wait_until="domcontentloaded", timeout=60_000)
            await page.wait_for_timeout(5_000)

            async def read_token() -> str:
                callback_token = await page.evaluate(
                    "() => window.__gorouterTurnstileToken || ''"
                )
                if callback_token:
                    return str(callback_token)
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
                # GoRouter's built-in widget uses interaction-only appearance,
                # so its iframe can remain 0x0 until a form action. Render a
                # normal visible widget with the same public site key.
                rendered = await page.evaluate(
                    """
                    async () => {
                      const response = await fetch('/api/status');
                      const payload = await response.json();
                      const data = payload.data || payload;
                      const sitekey = data.turnstile_site_key ||
                        data.turnstile_sitekey || data.turnstile_key;
                      if (!sitekey || !window.turnstile) return false;
                      document.querySelector('#gorouter-checkin-turnstile')?.remove();
                      const host = document.createElement('div');
                      host.id = 'gorouter-checkin-turnstile';
                      host.style.cssText = 'position:fixed;left:24px;top:24px;z-index:2147483647;background:white;padding:12px';
                      document.body.appendChild(host);
                      window.__gorouterTurnstileToken = '';
                      window.turnstile.render(host, {
                        sitekey,
                        appearance: 'always',
                        callback: token => { window.__gorouterTurnstileToken = token; }
                      });
                      return true;
                    }
                    """
                )
                print(
                    f"ACCOUNT={ACCOUNT_NAME} TURNSTILE_RENDER="
                    f"{'READY' if rendered else 'UNAVAILABLE'}"
                )
                await page.wait_for_timeout(3_000)
                token = await read_token()

            if not token:
                inputs = len(await page.query_selector_all('[name="cf-turnstile-response"]'))
                frames = len(await page.query_selector_all("iframe"))
                print(f"ACCOUNT={ACCOUNT_NAME} TURNSTILE_WIDGET inputs={inputs} iframes={frames}")
                try:
                    await solver.solve_captcha(
                        captcha_container=page,
                        captcha_type=CaptchaType.CLOUDFLARE_TURNSTILE,
                    )
                except Exception:
                    # Some Turnstile builds localize the iframe metadata that
                    # playwright-captcha uses for detection. Click the visible
                    # widget frame directly without reading its cross-origin
                    # contents, then wait for the hidden response input.
                    clicked = False
                    for iframe in await page.query_selector_all("iframe"):
                        box = await iframe.bounding_box()
                        if box and box["width"] >= 100 and box["height"] >= 40:
                            await page.mouse.click(
                                box["x"] + min(32, box["width"] / 4),
                                box["y"] + box["height"] / 2,
                            )
                            clicked = True
                            break
                    print(
                        f"ACCOUNT={ACCOUNT_NAME} TURNSTILE_FALLBACK="
                        f"{'CLICKED' if clicked else 'NO_VISIBLE_FRAME'}"
                    )
                for _ in range(30):
                    token = await read_token()
                    if token:
                        break
                    await page.wait_for_timeout(1_000)

            await page.close()
            if not token:
                fail("Turnstile token was not produced")
            print(f"ACCOUNT={ACCOUNT_NAME} TURNSTILE=SOLVED")
            return token


def read_status(today: str) -> tuple[dict, dict]:
    month = today[:7]
    payload = authenticated_request("GET", f"/api/user/checkin?month={urllib.parse.quote(month)}")
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
        f"ACCOUNT={ACCOUNT_NAME} RESULT={state} DATE={today} AWARDED_QUOTA={quota} "
        f"AWARDED_USD={quota / QUOTA_PER_USD:.2f} TOTAL_CHECKINS={total}"
    )


def main() -> int:
    if not USER_ID or not COOKIE_HEADER:
        fail("required secret is missing")
    if not USER_ID.isdigit():
        fail("user id is invalid")

    today = datetime.now(TIMEZONE).date().isoformat()
    _, stats = read_status(today)
    if stats.get("checked_in_today") is True and not FORCE_POST:
        print_success("ALREADY_CHECKED", stats, today)
        return 0

    payload = authenticated_request("POST", "/api/user/checkin")
    if payload.get("success") is not True and needs_turnstile(payload):
        turnstile_token = asyncio.run(solve_turnstile_token())
        encoded = urllib.parse.quote(turnstile_token, safe="")
        payload = authenticated_request("POST", f"/api/user/checkin?turnstile={encoded}")
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
