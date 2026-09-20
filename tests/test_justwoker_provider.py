import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from playwright_captcha import CaptchaType

sys.path.insert(0, str(Path(__file__).parent.parent))

from checkin import CheckIn
from utils.config import AccountConfig, AppConfig
from utils.get_turnstile_token import _solve_captcha_with_fresh_checkbox


def _providers(monkeypatch):
	monkeypatch.setenv('PROVIDERS', '[]')
	return AppConfig._load_providers('PROVIDERS')


def test_justwoker_provider_uses_new_session_auth(monkeypatch):
	provider = _providers(monkeypatch)['justwoker']

	assert provider.origin == 'https://api.justwoker.icu'
	assert provider.get_login_url() == 'https://api.justwoker.icu/sign-in'
	assert provider.get_check_in_url('unused') == 'https://api.justwoker.icu/api/user/checkin'
	assert provider.get_user_info_url() == 'https://api.justwoker.icu/api/user/self'
	assert provider.check_in_status is True
	assert provider.github_oauth is True
	assert provider.session_auth is True
	assert provider.auto_add is False
	assert provider.turnstile_check is True
	assert provider.bypass_method is None


def test_justwoker_provider_json_matches_runtime(monkeypatch):
	provider = _providers(monkeypatch)['justwoker']
	provider_json = json.loads((Path(__file__).parent.parent / 'PROVIDERS.json').read_text(encoding='utf-8'))[
		'justwoker'
	]

	for field in (
		'origin',
		'login_path',
		'status_path',
		'auth_state_path',
		'check_in_path',
		'check_in_status',
		'user_info_path',
		'github_oauth',
		'github_auth_path',
		'github_auth_redirect_path',
		'bypass_method',
		'auto_add',
		'session_auth',
		'turnstile_check',
	):
		assert provider_json[field] == getattr(provider, field)


def test_justwoker_checkin_retries_transient_network_error(monkeypatch):
	provider = _providers(monkeypatch)['justwoker']
	checkin = CheckIn('JustWoker', AccountConfig(provider='justwoker'), provider)
	success_response = MagicMock()
	success_response.status_code = 200
	success_response.json.return_value = {'success': True}
	session = MagicMock()
	session.post.side_effect = [RuntimeError('TLS connect error'), success_response]

	with patch('checkin.asyncio.sleep', new=AsyncMock()) as sleep:
		result = asyncio.run(checkin.execute_check_in_with_turnstile(session, {}, None))

	assert result['success'] is True
	assert session.post.call_count == 2
	sleep.assert_awaited_once_with(5)


def test_turnstile_click_reacquires_checkbox_after_detached_frame():
	class FakePage:
		def __init__(self):
			self.waits = []

		async def wait_for_timeout(self, delay):
			self.waits.append(delay)

	class FakeSolver:
		def __init__(self):
			self.calls = []

		async def solve_captcha(self, **kwargs):
			self.calls.append(kwargs)
			if len(self.calls) == 1:
				raise RuntimeError('Frame was detached')

	page = FakePage()
	solver = FakeSolver()

	result = asyncio.run(
		_solve_captcha_with_fresh_checkbox(
			solver,
			page,
			CaptchaType.CLOUDFLARE_TURNSTILE,
		)
	)

	assert result is True
	assert len(solver.calls) == 2
	assert page.waits == [1000]
	for call in solver.calls:
		assert call['checkbox_click_attempts'] == 1
		assert call['wait_checkbox_attempts'] == 4
		assert call['wait_checkbox_delay'] == 1


def test_turnstile_click_reacquire_exhaustion_is_reported_without_token():
	class FakePage:
		def __init__(self):
			self.waits = []

		async def wait_for_timeout(self, delay):
			self.waits.append(delay)

	class FakeSolver:
		def __init__(self):
			self.calls = 0

		async def solve_captcha(self, **kwargs):
			self.calls += 1
			raise RuntimeError('Failed to click checkbox after maximum attempts')

	page = FakePage()
	solver = FakeSolver()

	result = asyncio.run(
		_solve_captcha_with_fresh_checkbox(
			solver,
			page,
			CaptchaType.CLOUDFLARE_TURNSTILE,
			attempts=3,
		)
	)

	assert result is False
	assert solver.calls == 3
	assert page.waits == [1000, 1000]


def test_turnstile_source_stops_after_interstitial_retry_exhaustion():
	source = (Path(__file__).parent.parent / 'utils' / 'get_turnstile_token.py').read_text(
		encoding='utf-8'
	)

	assert 'solved = await _solve_captcha_with_fresh_checkbox(' in source
	assert 'if not solved:' in source
	assert 'Cloudflare interstitial challenge was not solved' in source


def test_justwoker_workflow_uses_twelve_tokens_and_reuses_vmess_proxy():
	workflow = (Path(__file__).parent.parent / '.github' / 'workflows' / 'justwoker.yml').read_text(encoding='utf-8')

	assert workflow.count('${{ secrets.JUSTWOKER_ACCESS_TOKEN') == 7
	assert 'JUSTWOKER_ACCESS_TOKEN: ${{ secrets.JUSTWOKER_ACCESS_TOKEN }}' in workflow
	for index in range(2, 7):
		assert (
			f'JUSTWOKER_ACCESS_TOKEN_{index}: '
			f'${{{{ secrets.JUSTWOKER_ACCESS_TOKEN_{index} }}}}'
		) in workflow
		assert workflow.count(f'"JUSTWOKER_ACCESS_TOKEN_{index}"') == 1
	assert 'JUSTWOKER_ACCESS_TOKEN_7:' not in workflow
	assert (
		'JUSTWOKER_ACCESS_TOKENS_7_12: '
		'${{ secrets.JUSTWOKER_ACCESS_TOKENS_7_12 }}'
	) in workflow
	assert 'ConvertFrom-Json -InputObject $env:JUSTWOKER_ACCESS_TOKENS_7_12' in workflow
	assert '$additionalTokens.Count -ne 6' in workflow
	assert '$tokenEntries.Count' in workflow
	assert 'GITHUB_TOKEN: ${{ github.token }}' in workflow
	assert 'JUSTWOKER_CLASH_CONFIG: ${{ secrets.TABITOKEN_CLASH_CONFIG }}' in workflow
	assert 'provider = "justwoker"' in workflow
	assert 'system_access_token = $token' in workflow
	assert 'Write-Output "::add-mask::$token"' in workflow
	assert '[System.Collections.Generic.HashSet[string]]::new' in workflow
	assert '[System.StringComparer]::Ordinal' in workflow
	assert 'JustWoker 账号 Secret 重复' in workflow
	assert 'name = "JustWoker $($index + 1)"' in workflow
	assert '$accountObjects.Count -ne $requiredCount' in workflow
	assert '[string]::IsNullOrWhiteSpace($rawToken)' in workflow
	assert '$token = $rawToken.Trim()' in workflow
	assert 'REQUIRED_ACCOUNT_SUCCESSES: "12"' in workflow
	assert 'mihomoVersion = "v1.19.30"' in workflow
	assert 'mihomoSha256 = "289fde5e29d37a5b3326480590d8b3551c5bf7f8737290355c19bce74d57a563"' in workflow
	assert '& $mihomoExe -t -f $configPath' in workflow
	assert workflow.count('Start-Process -FilePath $mihomoExe') == 1
	assert 'VMESS_EGRESS_CHANGED=True' in workflow
	assert '$env:PROXY = \'{"server":"http://127.0.0.1:7890"}\'' in workflow
	assert workflow.count('uv run python -u main.py') == 1
	assert 'JUSTWOKER_SUCCESS_COUNT=12/12' in workflow
	assert 'qualification-isbn-improvements-governments.trycloudflare.com' not in workflow
