#!/usr/bin/env python3
"""Idempotent GoRouter daily check-in using a system access token."""

from __future__ import annotations

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


def request_json(method: str, path: str, *, include_cookie: bool = False) -> dict:
    url = f"{BASE_URL}{path}"
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {TOKEN}",
        "New-Api-User": USER_ID,
        "User-Agent": "gorouter-checkin/1.0 (+github-actions)",
    }
    if include_cookie:
        if not COOKIE_HEADER:
            fail("session cookie is required for Turnstile-protected check-in")
        headers["Cookie"] = COOKIE_HEADER
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
    if stats.get("checked_in_today") is True:
        print_success("ALREADY_CHECKED", stats, today)
        return 0

    payload = request_json("POST", "/api/user/checkin", include_cookie=True)
    if payload.get("success") is not True:
        fail(_safe_message(payload))

    _, verified_stats = read_status(today)
    if verified_stats.get("checked_in_today") is not True:
        fail("server did not verify today's check-in")
    print_success("CHECKED_IN", verified_stats, today)
    return 0


if __name__ == "__main__":
    sys.exit(main())
