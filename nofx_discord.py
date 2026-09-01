"""Local Discord workflow for NOFX verification and daily check-in.

Discord profiles and encrypted credentials live under LOCALAPPDATA. The repo
contains only automation code and non-sensitive slot metadata.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import subprocess
import time
import urllib.error
import urllib.request
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from playwright.async_api import BrowserContext, Page, async_playwright

GUILD_ID = '1543428593667809331'
START_CHANNEL_ID = '1543433516019359835'
INVITE_URL = 'https://discord.gg/2NcYW3sCtq'
CHANNEL_URL = f'https://discord.com/channels/{GUILD_ID}/{START_CHANNEL_ID}'
DISCORD_API = 'https://discord.com/api/v10'

LOCAL_ROOT = Path(os.environ.get('LOCALAPPDATA', Path.home())) / 'NOFXDiscord'
PROFILE_ROOT = LOCAL_ROOT / 'profiles'
LOG_ROOT = LOCAL_ROOT / 'logs'
STATE_ROOT = LOCAL_ROOT / 'state'
EDGE_PATHS = (
	Path(r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe'),
	Path(r'C:\Program Files\Microsoft\Edge\Application\msedge.exe'),
	Path(r'C:\Program Files\Google\Chrome\Application\chrome.exe'),
)


@dataclass(frozen=True)
class Account:
	slot: int
	token: str


def load_accounts() -> list[Account]:
	reader = Path(__file__).parent / 'tools' / 'read_nofx_discord_accounts.ps1'
	completed = subprocess.run(
		['pwsh', '-NoProfile', '-File', str(reader)],
		capture_output=True,
		text=True,
		encoding='utf-8',
		check=True,
	)
	payload = json.loads(completed.stdout)
	return [Account(slot=int(item['slot']), token=str(item['token'])) for item in payload]


def account_for_slot(slot: int) -> Account:
	for account in load_accounts():
		if account.slot == slot:
			return account
	raise RuntimeError(f'Discord account slot {slot} is not configured')


def api_json(account: Account, path: str) -> Any:
	request = urllib.request.Request(
		f'{DISCORD_API}{path}',
		headers={'Authorization': account.token, 'User-Agent': 'Mozilla/5.0'},
	)
	with urllib.request.urlopen(request, timeout=30) as response:
		return json.load(response)


def doctor_account(account: Account) -> dict[str, Any]:
	try:
		user = api_json(account, '/users/@me')
		guilds = api_json(account, '/users/@me/guilds')
		return {
			'slot': account.slot,
			'token': 'valid',
			'user_id': user.get('id'),
			'nofx_member': any(guild.get('id') == GUILD_ID for guild in guilds),
		}
	except urllib.error.HTTPError as exc:
		return {'slot': account.slot, 'token': f'http_{exc.code}', 'nofx_member': False}
	except Exception as exc:
		return {'slot': account.slot, 'token': type(exc).__name__, 'nofx_member': False}


def edge_path() -> Path:
	for candidate in EDGE_PATHS:
		if candidate.exists():
			return candidate
	raise RuntimeError('Microsoft Edge or Google Chrome was not found')


def profile_path(slot: int) -> Path:
	return PROFILE_ROOT / f'{slot:02d}'


@asynccontextmanager
async def discord_profile(account: Account, *, headed: bool):
	PROFILE_ROOT.mkdir(parents=True, exist_ok=True)
	async with async_playwright() as playwright:
		context = await playwright.chromium.launch_persistent_context(
			user_data_dir=str(profile_path(account.slot)),
			executable_path=str(edge_path()),
			headless=not headed,
			viewport=None if headed else {'width': 1365, 'height': 900},
			args=['--disable-blink-features=AutomationControlled'],
		)
		try:
			yield context
		finally:
			await context.close()


async def ensure_login(page: Page, account: Account) -> None:
	await page.goto('https://discord.com/login', wait_until='domcontentloaded', timeout=90_000)
	await page.wait_for_timeout(1_500)
	if '/channels/' in page.url:
		return
	await page.evaluate(
		"""token => {
			const frame = document.createElement('iframe');
			document.body.appendChild(frame);
			frame.contentWindow.localStorage.setItem('token', JSON.stringify(token));
			frame.remove();
		}""",
		account.token,
	)
	await page.reload(wait_until='domcontentloaded', timeout=90_000)
	try:
		await page.wait_for_url('**/channels/**', timeout=45_000)
	except Exception as exc:
		raise RuntimeError('Discord profile login did not complete') from exc


async def get_page(context: BrowserContext) -> Page:
	return context.pages[0] if context.pages else await context.new_page()


async def open_profile(slot: int) -> None:
	account = account_for_slot(slot)
	async with discord_profile(account, headed=True) as context:
		page = await get_page(context)
		await ensure_login(page, account)
		member = doctor_account(account)['nofx_member']
		await page.goto(CHANNEL_URL if member else INVITE_URL, wait_until='domcontentloaded', timeout=90_000)
		print(f'SLOT={slot:02d} PROFILE_READY=true NOFX_MEMBER={str(member).lower()}')
		print('Complete any visible Discord confirmation, then return here and press Enter.')
		await asyncio.to_thread(input)


async def select_slash_command(page: Page, command: str) -> None:
	box = page.locator('[role="textbox"][contenteditable="true"]').last
	await box.wait_for(state='visible', timeout=60_000)
	await box.click()
	await box.fill(f'/{command}')
	await page.wait_for_timeout(1_000)
	options = page.locator('[role="option"]').filter(has_text=f'/{command}')
	if await options.count():
		await options.first.click()
	else:
		await box.press('Enter')


async def invoke_command(slot: int, command: str, argument: str | None = None, *, headed: bool) -> str:
	account = account_for_slot(slot)
	if not doctor_account(account)['nofx_member']:
		return 'not_in_guild'
	async with discord_profile(account, headed=headed) as context:
		page = await get_page(context)
		await ensure_login(page, account)
		await page.goto(CHANNEL_URL, wait_until='domcontentloaded', timeout=90_000)
		await page.wait_for_timeout(2_000)
		await select_slash_command(page, command)
		if argument:
			box = page.locator('[role="textbox"][contenteditable="true"]').last
			await box.type(argument)
		try:
			async with page.expect_response(
				lambda response: '/interactions' in response.url and response.request.method == 'POST',
				timeout=30_000,
			) as response_info:
				await page.locator('[role="textbox"][contenteditable="true"]').last.press('Enter')
			response = await response_info.value
		except Exception:
			return 'interaction_timeout'
		if response.status not in (200, 204):
			return f'interaction_http_{response.status}'
		await page.wait_for_timeout(3_000)
		body = (await page.locator('body').inner_text()).lower()
		if any(text in body for text in ('签到成功', '领取成功', 'check-in successful', 'checked in successfully')):
			return 'success'
		if any(text in body for text in ('已签到', 'already checked', 'cooldown')):
			return 'already'
		return 'interaction_sent'


def build_daily_plan(day: date, slots: list[int]) -> list[dict[str, Any]]:
	"""Build a stable, serial plan spread over roughly four to seven hours."""
	rng = random.Random(f'nofx-discord:{day.isoformat()}')
	order = slots[:]
	rng.shuffle(order)
	current = datetime.combine(day, datetime.min.time()) + timedelta(minutes=rng.randint(8, 35))
	plan = []
	for slot in order:
		plan.append({'slot': slot, 'scheduled_at': current.isoformat(timespec='minutes')})
		current += timedelta(minutes=rng.randint(12, 22))
	return plan


def write_log(record: dict[str, Any]) -> None:
	LOG_ROOT.mkdir(parents=True, exist_ok=True)
	path = LOG_ROOT / f'{date.today().isoformat()}.jsonl'
	with path.open('a', encoding='utf-8') as stream:
		stream.write(json.dumps(record, ensure_ascii=False) + '\n')


async def run_day(*, headed: bool) -> None:
	STATE_ROOT.mkdir(parents=True, exist_ok=True)
	plan_path = STATE_ROOT / f'plan-{date.today().isoformat()}.json'
	if plan_path.exists():
		plan = json.loads(plan_path.read_text(encoding='utf-8'))
	else:
		plan = build_daily_plan(date.today(), [account.slot for account in load_accounts()])
		plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding='utf-8')
	for item in plan:
		slot = int(item['slot'])
		scheduled = datetime.fromisoformat(item['scheduled_at'])
		if datetime.now() < scheduled:
			await asyncio.sleep((scheduled - datetime.now()).total_seconds())
		started = time.monotonic()
		try:
			status = await invoke_command(slot, 'checkin', headed=headed)
		except Exception as exc:  # keep one account failure from aborting the daily run
			status = f'error_{type(exc).__name__}'
		write_log(
			{
				'timestamp': datetime.now().isoformat(timespec='seconds'),
				'slot': slot,
				'command': 'checkin',
				'status': status,
				'duration_seconds': round(time.monotonic() - started, 1),
			}
		)
		print(f'SLOT={slot:02d} CHECKIN_STATUS={status}')


def parse_args() -> argparse.Namespace:
	parser = argparse.ArgumentParser(description='NOFX Discord local automation')
	sub = parser.add_subparsers(dest='command', required=True)
	sub.add_parser('doctor')
	open_parser = sub.add_parser('open-profile')
	open_parser.add_argument('--slot', type=int, required=True)
	checkin = sub.add_parser('checkin')
	checkin.add_argument('--slot', type=int, required=True)
	checkin.add_argument('--headed', action='store_true')
	verify = sub.add_parser('verify')
	verify.add_argument('--slot', type=int, required=True)
	verify.add_argument('--code', required=True)
	verify.add_argument('--headed', action='store_true')
	plan = sub.add_parser('plan')
	plan.add_argument('--date', default=date.today().isoformat())
	run = sub.add_parser('run-day')
	run.add_argument('--headed', action='store_true')
	return parser.parse_args()


def main() -> None:
	args = parse_args()
	if args.command == 'doctor':
		for account in load_accounts():
			result = doctor_account(account)
			print(
				f"SLOT={account.slot:02d} TOKEN={result['token']} "
				f"NOFX_MEMBER={str(result['nofx_member']).lower()}"
			)
	elif args.command == 'open-profile':
		asyncio.run(open_profile(args.slot))
	elif args.command == 'checkin':
		status = asyncio.run(invoke_command(args.slot, 'checkin', headed=args.headed))
		print(f'SLOT={args.slot:02d} CHECKIN_STATUS={status}')
	elif args.command == 'verify':
		status = asyncio.run(invoke_command(args.slot, 'verify', args.code, headed=args.headed))
		print(f'SLOT={args.slot:02d} VERIFY_STATUS={status}')
	elif args.command == 'plan':
		day = date.fromisoformat(args.date)
		print(json.dumps(build_daily_plan(day, [a.slot for a in load_accounts()]), ensure_ascii=False, indent=2))
	elif args.command == 'run-day':
		asyncio.run(run_day(headed=args.headed))


if __name__ == '__main__':
	main()
