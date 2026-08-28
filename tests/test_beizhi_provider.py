import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from checkin import CheckIn
from utils.config import AccountConfig, AppConfig


def _providers(monkeypatch):
	monkeypatch.setenv('PROVIDERS', '[]')
	return AppConfig._load_providers('PROVIDERS')


def test_beizhi_provider_uses_new_session_auth(monkeypatch):
	provider = _providers(monkeypatch)['beizhi']

	assert provider.origin == 'https://beizhi.sylu.cc'
	assert provider.get_login_url() == 'https://beizhi.sylu.cc/sign-in'
	assert provider.get_check_in_url('unused') == 'https://beizhi.sylu.cc/api/user/checkin'
	assert provider.get_user_info_url() == 'https://beizhi.sylu.cc/api/user/self'
	assert provider.check_in_status is True
	assert provider.github_oauth is False
	assert provider.session_auth is True
	assert provider.auto_add is False
	assert provider.turnstile_check is False
	assert provider.bypass_method is None


def test_beizhi_provider_json_matches_runtime(monkeypatch):
	provider = _providers(monkeypatch)['beizhi']
	provider_json = json.loads((Path(__file__).parent.parent / 'PROVIDERS.json').read_text(encoding='utf-8'))[
		'beizhi'
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


def test_beizhi_checkin_retries_transient_network_error(monkeypatch):
	provider = _providers(monkeypatch)['beizhi']
	checkin = CheckIn('Beizhi', AccountConfig(provider='beizhi'), provider)
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


def test_beizhi_workflow_uses_six_tokens_and_reuses_vmess_proxy():
	workflow = (Path(__file__).parent.parent / '.github' / 'workflows' / 'beizhi.yml').read_text(
		encoding='utf-8'
	)

	assert workflow.count('${{ secrets.BEIZHI_ACCESS_TOKEN') == 6
	assert 'BEIZHI_ACCESS_TOKEN: ${{ secrets.BEIZHI_ACCESS_TOKEN }}' in workflow
	for index in range(2, 7):
		assert f'BEIZHI_ACCESS_TOKEN_{index}: ${{{{ secrets.BEIZHI_ACCESS_TOKEN_{index} }}}}' in workflow
		assert workflow.count(f'"BEIZHI_ACCESS_TOKEN_{index}"') == 1
	assert 'BEIZHI_ACCESS_TOKEN_7:' not in workflow
	assert 'GITHUB_TOKEN: ${{ github.token }}' in workflow
	assert 'BEIZHI_CLASH_CONFIG: ${{ secrets.TABITOKEN_CLASH_CONFIG }}' in workflow
	assert 'provider = "beizhi"' in workflow
	assert 'system_access_token = $token' in workflow
	assert 'Write-Output "::add-mask::$token"' in workflow
	assert '[System.Collections.Generic.HashSet[string]]::new' in workflow
	assert '[System.StringComparer]::Ordinal' in workflow
	assert '北栀账号 Secret 重复' in workflow
	assert 'name = "北栀 $($index + 1)"' in workflow
	assert '$accountObjects.Count -ne $requiredCount' in workflow
	assert '[string]::IsNullOrWhiteSpace($rawToken)' in workflow
	assert '$token = $rawToken.Trim()' in workflow
	assert 'REQUIRED_ACCOUNT_SUCCESSES: "6"' in workflow
	assert 'mihomoVersion = "v1.19.30"' in workflow
	assert 'mihomoSha256 = "289fde5e29d37a5b3326480590d8b3551c5bf7f8737290355c19bce74d57a563"' in workflow
	assert '& $mihomoExe -t -f $configPath' in workflow
	assert workflow.count('Start-Process -FilePath $mihomoExe') == 1
	assert 'VMESS_EGRESS_CHANGED=True' in workflow
	assert '$env:PROXY = \'{"server":"http://127.0.0.1:7890"}\'' in workflow
	assert workflow.count('uv run python -u main.py') == 1
	assert 'BEIZHI_SUCCESS_COUNT=6/6' in workflow
