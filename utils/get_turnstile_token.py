#!/usr/bin/env python3
"""Render and solve a provider's visible Cloudflare Turnstile widget."""

from __future__ import annotations

from camoufox.async_api import AsyncCamoufox
from playwright_captcha import CaptchaType, ClickSolver, FrameworkType

from utils.get_headers import get_browser_headers


async def get_turnstile_token(
    origin: str,
    account_name: str,
    proxy: dict | None = None,
) -> tuple[str, dict, dict] | None:
    print(f"ℹ️ {account_name}: Starting Turnstile verification")
    async with AsyncCamoufox(
        headless=False,
        humanize=True,
        locale="en-US",
        os="macos",
        proxy=proxy,
        geoip=bool(proxy),
        config={"forceScopeAccess": True},
    ) as browser:
        page = await browser.new_page()
        async with ClickSolver(
            framework=FrameworkType.CAMOUFOX,
            page=page,
            max_attempts=2,
            attempt_delay=3,
        ) as solver:
            try:
                await page.goto(f"{origin}/sign-in", wait_until="domcontentloaded", timeout=60_000)
                await page.wait_for_timeout(3_000)

                title = await page.title()
                content = await page.content()
                if "Just a moment" in title or "Checking your browser" in content:
                    await solver.solve_captcha(
                        captcha_container=page,
                        captcha_type=CaptchaType.CLOUDFLARE_INTERSTITIAL,
                    )
                    await page.wait_for_timeout(5_000)

                async def read_token() -> str:
                    callback_token = await page.evaluate(
                        "() => window.__newApiCheckinTurnstileToken || ''"
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
                    rendered = await page.evaluate(
                        """async () => {
                            const response = await fetch('/api/status', { credentials: 'include' });
                            const payload = await response.json();
                            const data = payload.data || payload;
                            const sitekey = data.turnstile_site_key ||
                                data.turnstile_sitekey || data.turnstile_key;
                            if (!sitekey) return false;
                            if (!window.turnstile) {
                                await new Promise((resolve, reject) => {
                                    const existing = document.querySelector(
                                        'script[data-newapi-checkin-turnstile]'
                                    );
                                    if (existing) {
                                        const timer = setInterval(() => {
                                            if (window.turnstile) {
                                                clearInterval(timer);
                                                resolve();
                                            }
                                        }, 100);
                                        setTimeout(() => {
                                            clearInterval(timer);
                                            reject(new Error('Turnstile script timeout'));
                                        }, 10000);
                                        return;
                                    }
                                    const script = document.createElement('script');
                                    script.dataset.newapiCheckinTurnstile = 'true';
                                    script.src = 'https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit';
                                    script.onload = resolve;
                                    script.onerror = reject;
                                    document.head.appendChild(script);
                                });
                            }
                            if (!window.turnstile) return false;
                            document.querySelector('#newapi-checkin-turnstile')?.remove();
                            const host = document.createElement('div');
                            host.id = 'newapi-checkin-turnstile';
                            host.style.cssText = 'position:fixed;left:24px;top:24px;width:330px;height:90px;z-index:2147483647;background:white;padding:12px';
                            document.body.appendChild(host);
                            window.__newApiCheckinTurnstileToken = '';
                            window.turnstile.render(host, {
                                sitekey,
                                appearance: 'always',
                                size: 'normal',
                                theme: 'light',
                                callback: value => {
                                    window.__newApiCheckinTurnstileToken = value;
                                }
                            });
                            return true;
                        }"""
                    )
                    print(
                        f"ℹ️ {account_name}: Turnstile widget "
                        f"{'rendered' if rendered else 'unavailable'}"
                    )
                    await page.wait_for_timeout(3_000)
                    token = await read_token()

                if not token:
                    try:
                        await solver.solve_captcha(
                            captcha_container=page,
                            captcha_type=CaptchaType.CLOUDFLARE_TURNSTILE,
                        )
                    except Exception:
                        clicked = False
                        host = await page.query_selector("#newapi-checkin-turnstile")
                        host_box = await host.bounding_box() if host else None
                        if host_box:
                            await page.mouse.click(host_box["x"] + 42, host_box["y"] + 42)
                            clicked = True
                        if not clicked:
                            for iframe in await page.query_selector_all("iframe"):
                                box = await iframe.bounding_box()
                                if box and box["width"] >= 100 and box["height"] >= 40:
                                    await page.mouse.click(
                                        box["x"] + min(32, box["width"] / 4),
                                        box["y"] + box["height"] / 2,
                                    )
                                    break

                    for _ in range(30):
                        token = await read_token()
                        if token:
                            break
                        await page.wait_for_timeout(1_000)

                if token:
                    cookies = {
                        cookie["name"]: cookie["value"]
                        for cookie in await page.context.cookies()
                        if cookie.get("name") and cookie.get("value")
                    }
                    browser_headers = await get_browser_headers(page)
                    print(f"✅ {account_name}: Turnstile verification completed")
                    return token, cookies, browser_headers

                print(f"❌ {account_name}: Turnstile token was not produced")
                return None
            except Exception as exc:
                print(f"❌ {account_name}: Turnstile verification failed: {exc}")
                return None
            finally:
                await page.close()
