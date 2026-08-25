import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

from checkin import CheckIn
from sign_in_with_github import GitHubSignIn
from utils.config import AccountConfig, AppConfig


def _providers(monkeypatch):
    monkeypatch.setenv('PROVIDERS', '[]')
    return AppConfig._load_providers('PROVIDERS')


def test_seekai_provider_uses_new_session_auth(monkeypatch):
    provider = _providers(monkeypatch)['seekai']

    assert provider.origin == 'https://seekai.cc'
    assert provider.get_login_url() == 'https://seekai.cc/sign-in'
    assert provider.get_check_in_url('unused') == 'https://seekai.cc/api/user/checkin'
    assert provider.get_user_info_url() == 'https://seekai.cc/api/user/self'
    assert provider.check_in_status is True
    assert provider.github_oauth is True
    assert provider.session_auth is True
    assert provider.auto_add is False
    assert provider.turnstile_check is True
    assert provider.bypass_method is None


def test_seekai_provider_json_matches_runtime(monkeypatch):
    provider = _providers(monkeypatch)['seekai']
    provider_json = json.loads(
        (Path(__file__).parent.parent / 'PROVIDERS.json').read_text(encoding='utf-8')
    )['seekai']

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


def test_seekai_oauth_state_uses_post_flow_token(monkeypatch):
    provider = _providers(monkeypatch)['seekai']
    checkin = CheckIn('SeekAI', AccountConfig(provider='seekai'), provider)
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {
        'success': True,
        'data': {'flow_token': 'seekai-flow-token'},
    }
    response.cookies = MagicMock()
    response.cookies.__len__.return_value = 0
    response.cookies.jar = []
    session = MagicMock()
    session.post.return_value = response

    result = asyncio.run(
        checkin.get_auth_state(session, {'Accept': 'application/json'}, provider='github')
    )

    assert result == {'success': True, 'state': 'seekai-flow-token', 'cookies': []}
    session.post.assert_called_once_with(
        'https://seekai.cc/api/oauth/state',
        headers={'Accept': 'application/json', 'Content-Type': 'application/json'},
        json={'provider': 'github', 'intent': 'login'},
        timeout=30,
    )


def test_github_oauth_browser_keeps_account_proxy(monkeypatch):
    provider = _providers(monkeypatch)['seekai']
    proxy = {'server': 'http://127.0.0.1:7890'}

    signin = GitHubSignIn('SeekAI', provider, 'octocat', 'secret', proxy_config=proxy)

    assert signin.proxy_config == proxy
    repo_root = Path(__file__).parent.parent
    checkin_source = (repo_root / 'checkin.py').read_text(encoding='utf-8')
    signin_source = (repo_root / 'sign_in_with_github.py').read_text(encoding='utf-8')
    assert 'proxy_config=self.camoufox_proxy_config' in checkin_source
    assert 'proxy=self.proxy_config' in signin_source
    assert "Got auth state for GitHub: {auth_state_result['state']}" not in checkin_source
    assert 'Using client_id: {client_id}, auth_state: {auth_state}' not in signin_source

def test_seekai_workflow_uses_pat_and_reuses_vmess_proxy():
    workflow = (
        Path(__file__).parent.parent / '.github' / 'workflows' / 'seekai.yml'
    ).read_text(encoding='utf-8')

    assert 'SEEKAI_ACCESS_TOKEN: ${{ secrets.SEEKAI_ACCESS_TOKEN }}' in workflow
    assert 'GITHUB_TOKEN: ${{ github.token }}' in workflow
    assert 'SEEKAI_CLASH_CONFIG: ${{ secrets.TABITOKEN_CLASH_CONFIG }}' in workflow
    assert 'provider = "seekai"' in workflow
    assert 'system_access_token = $env:SEEKAI_ACCESS_TOKEN' in workflow
    assert 'Write-Output "::add-mask::$env:SEEKAI_ACCESS_TOKEN"' in workflow
    assert "headers={'Authorization': f\\\"Bearer {os.environ.get('GITHUB_TOKEN', '')}\\\"}" in workflow
    assert 'seekai-storage-' not in workflow
    assert 'REQUIRED_ACCOUNT_SUCCESSES: "1"' in workflow
    assert 'mihomoVersion = "v1.19.30"' in workflow
    assert (
        'mihomoSha256 = "289fde5e29d37a5b3326480590d8b3551c5bf7f8737290355c19bce74d57a563"'
        in workflow
    )
    assert '& $mihomoExe -t -f $configPath' in workflow
    assert 'VMESS_EGRESS_CHANGED=True' in workflow
    assert '$env:PROXY = \'{"server":"http://127.0.0.1:7890"}\'' in workflow
    assert 'SEEKAI_SUCCESS_COUNT=1/1' in workflow
    assert 'qualification-isbn-improvements-governments.trycloudflare.com' not in workflow
