"""Perform one low-frequency NOFX daily check-in with a saved browser session.

The session is created by the user after completing the site's magic-link login.
This script intentionally clicks the visible check-in button instead of calling
the site's internal server action directly.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

BASE_URL = os.getenv('NOFX_BASE_URL', 'https://nofx.one').rstrip('/')
if urlparse(BASE_URL).scheme != 'https' or urlparse(BASE_URL).hostname != 'nofx.one':
	raise RuntimeError('NOFX_BASE_URL 必须是 https://nofx.one')
LOCALE = os.getenv('NOFX_LOCALE', 'zh-CN')
if not re.fullmatch(r'[a-z]{2}-[A-Z]{2}', LOCALE):
	raise RuntimeError('NOFX_LOCALE 格式无效')
TASKS_URL = f'{BASE_URL}/{LOCALE}/tasks'
CHECKIN_TEXT = re.compile(r'(领取|每日签到|签到|claim|check.?in)', re.IGNORECASE)
ALREADY_TEXT = re.compile(r'(已签到|checked.?in|already)', re.IGNORECASE)


def classify_checkin_button(text: str, disabled: bool) -> str | None:
	"""Return ``claim``, ``already`` or ``None`` for a visible button."""

	normalized = ' '.join(text.split())
	if not CHECKIN_TEXT.search(normalized):
		return None
	if ALREADY_TEXT.search(normalized):
		return 'already'
	if disabled:
		return 'already'
	return 'claim'


def is_discord_only_task(text: str) -> bool:
	"""Return whether NOFX has replaced web claiming with Discord check-in."""

	return bool(re.search(r'Discord|/checkin|网页签到已关闭|签到已关闭', text, re.IGNORECASE))


def load_storage_state() -> dict[str, Any]:
	"""Load storage state from a file, raw JSON, or base64 environment secret."""

	path = os.getenv('NOFX_STORAGE_STATE_FILE')
	raw = os.getenv('NOFX_STORAGE_STATE')
	encoded = os.getenv('NOFX_STORAGE_STATE_B64')
	if path:
		raw = Path(path).read_text(encoding='utf-8')
	elif encoded:
		raw = base64.b64decode(encoded).decode('utf-8')
	if not raw:
		raise RuntimeError(
			'未配置 NOFX_STORAGE_STATE_B64、NOFX_STORAGE_STATE 或 NOFX_STORAGE_STATE_FILE；'
			'请先完成一次 Magic Link 登录并导出会话状态。'
		)
	try:
		state = json.loads(raw)
	except json.JSONDecodeError as exc:
		raise RuntimeError('NOFX 会话状态不是有效 JSON') from exc
	if not isinstance(state, dict) or not isinstance(state.get('cookies'), list):
		raise RuntimeError('NOFX 会话状态缺少 cookies 数组')
	return state


async def capture_storage_state(path: Path) -> None:
	"""Open a headed browser so the user can complete the magic-link login once."""

	from playwright.async_api import async_playwright

	path.parent.mkdir(parents=True, exist_ok=True)
	async with async_playwright() as playwright:
		browser = await playwright.chromium.launch(headless=False)
		context = await browser.new_context()
		try:
			page = await context.new_page()
			await page.goto(f'{BASE_URL}/{LOCALE}/sign-in', wait_until='domcontentloaded')
			print('请在打开的浏览器中完成 NOFX 邮箱 Magic Link 登录。完成后回到终端按回车保存会话。')
			await asyncio.to_thread(input)
			if '/sign-in' in page.url:
				raise RuntimeError('仍停留在登录页，未检测到登录完成')
			await context.storage_state(path=str(path))
		finally:
			await context.close()
			await browser.close()
	print(f'会话已保存到 {path}。请将该文件的 base64 内容作为 GitHub Secret NOFX_STORAGE_STATE_B64。')


async def check_in() -> None:
	from playwright.async_api import async_playwright

	state = load_storage_state()
	async with async_playwright() as playwright:
		browser = await playwright.chromium.launch(headless=True)
		context = await browser.new_context(storage_state=state)
		page = await context.new_page()
		try:
			await page.goto(TASKS_URL, wait_until='domcontentloaded', timeout=60_000)
			await page.wait_for_timeout(1_000)
			if '/sign-in' in page.url:
				raise RuntimeError('NOFX 会话已失效，请重新完成 Magic Link 登录并更新 Secret')

			daily_task = page.locator('article').filter(has_text='每日签到').first
			task_text = ' '.join((await daily_task.inner_text()).split())
			buttons = daily_task.get_by_role('button')
			if await buttons.count() == 0:
				# NOFX has moved daily check-in to its Discord bot. The task card
				# remains visible and links to Discord instead of exposing a web
				# claim button; treat that as a known unavailable web action.
				if is_discord_only_task(task_text):
					print('NOFX_CHECKIN_STATUS=already_or_unavailable')
					print('NOFX_CHECKIN_NOTE=web_checkin_closed_use_discord')
					return
				all_buttons = await page.get_by_role('button').all_text_contents()
				labels = '|'.join(' '.join(label.split())[:80] for label in all_buttons[:20])
				print(f'NOFX_DEBUG_BUTTON_LABELS={labels}')
				raise RuntimeError('未找到可识别的 NOFX 签到按钮，页面结构可能已变化')
			candidate = buttons.first
			state_name = classify_checkin_button(await candidate.inner_text(), await candidate.is_disabled())
			if state_name == 'already':
				print('NOFX_CHECKIN_STATUS=already_or_unavailable')
				return
			if state_name != 'claim':
				print(
					f'NOFX_DEBUG_DAILY_BUTTON=text:{" ".join((await candidate.inner_text()).split())[:100]}'
					f' disabled:{await candidate.is_disabled()}'
				)
				raise RuntimeError('未找到可领取状态的 NOFX 签到按钮，页面结构可能已变化')

			await candidate.click(timeout=30_000)
			await page.wait_for_timeout(2_000)
			await page.reload(wait_until='domcontentloaded', timeout=60_000)
			await page.wait_for_timeout(1_000)
			if '/sign-in' in page.url:
				raise RuntimeError('签到后会话跳回登录页，登录状态可能已失效')
			daily_task = page.locator('article').filter(has_text='每日签到').first
			buttons = daily_task.get_by_role('button')
			if await buttons.count() and classify_checkin_button(
				await buttons.first.inner_text(), await buttons.first.is_disabled()
			) == 'already':
				print('NOFX_CHECKIN_STATUS=credited_or_pending')
				return
			raise RuntimeError('点击签到后未确认页面显示已签到')
		finally:
			await context.close()
			await browser.close()


def parse_args() -> argparse.Namespace:
	parser = argparse.ArgumentParser(description='NOFX daily check-in')
	parser.add_argument('--capture-state', type=Path, help='headed login helper: save storage state to this path')
	return parser.parse_args()


def main() -> None:
	args = parse_args()
	try:
		if args.capture_state:
			asyncio.run(capture_storage_state(args.capture_state))
		else:
			asyncio.run(check_in())
	except Exception as exc:
		slot = os.getenv('NOFX_ACCOUNT_SLOT', 'NOFX_STORAGE_STATE_B64')
		print(f'NOFX_CHECKIN_ERROR[{slot}]={exc}')
		raise SystemExit(1) from exc


if __name__ == '__main__':
	main()
