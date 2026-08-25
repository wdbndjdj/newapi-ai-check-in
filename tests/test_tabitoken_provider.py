import asyncio
import inspect
import json
import sys
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from checkin import CheckIn
from utils.config import AccountConfig, AppConfig, OAuthAccountConfig


def _providers(monkeypatch):
    monkeypatch.setenv("PROVIDERS", "[]")
    return AppConfig._load_providers("PROVIDERS")


def test_tabitoken_provider_uses_new_session_auth(monkeypatch):
    provider = _providers(monkeypatch)["tabitoken"]

    assert provider.origin == "https://tabitoken.com"
    assert provider.get_login_url() == "https://tabitoken.com/sign-in"
    assert provider.get_check_in_url("unused") == "https://tabitoken.com/api/user/checkin"
    assert provider.check_in_status is True
    assert provider.github_oauth is True
    assert provider.session_auth is True
    assert provider.auto_add is True
    assert provider.turnstile_check is True
    assert provider.bypass_method is None


def test_tabitoken_provider_json_matches_builtin(monkeypatch):
    provider = _providers(monkeypatch)["tabitoken"]
    provider_json = json.loads(
        (Path(__file__).parent.parent / "PROVIDERS.json").read_text(encoding="utf-8")
    )["tabitoken"]

    for field in (
        "origin",
        "login_path",
        "status_path",
        "auth_state_path",
        "check_in_path",
        "check_in_status",
        "user_info_path",
        "github_oauth",
        "github_auth_path",
        "github_auth_redirect_path",
        "bypass_method",
        "auto_add",
        "session_auth",
        "turnstile_check",
    ):
        assert provider_json[field] == getattr(provider, field)


def test_tabitoken_pat_does_not_require_api_user(monkeypatch):
    providers = _providers(monkeypatch)
    monkeypatch.setenv(
        "ACCOUNTS",
        '[{"name":"TaBi Token","provider":"tabitoken","system_access_token":"pat-value"}]',
    )

    accounts = AppConfig._load_accounts("ACCOUNTS", [], [], providers)

    assert len(accounts) == 1
    assert accounts[0].provider == "tabitoken"
    assert accounts[0].api_user == ""
    assert accounts[0].system_access_token == "pat-value"


def test_load_from_env_accepts_workflow_pat_configuration(monkeypatch):
    monkeypatch.setenv("PROVIDERS", "")
    monkeypatch.setenv(
        "ACCOUNTS",
        '[{"name":"TaBi Token","provider":"tabitoken","system_access_token":"pat-value"}]',
    )
    monkeypatch.delenv("ACCOUNTS_LINUX_DO", raising=False)
    monkeypatch.delenv("ACCOUNTS_GITHUB", raising=False)
    monkeypatch.delenv("PROXY", raising=False)

    config = AppConfig.load_from_env()

    assert [(account.provider, account.api_user) for account in config.accounts] == [
        ("tabitoken", "")
    ]


def test_tabitoken_auto_adds_global_github_account(monkeypatch):
    providers = _providers(monkeypatch)
    github_accounts = [OAuthAccountConfig(username="octocat", password="secret")]

    accounts = AppConfig._auto_add_accounts_for_custom_providers(
        providers,
        [],
        [],
        github_accounts,
    )

    tabitoken = next(account for account in accounts if account.provider == "tabitoken")
    assert tabitoken.github == github_accounts


def test_session_auth_requests_oauth_flow_token_with_post(monkeypatch):
    provider = _providers(monkeypatch)["tabitoken"]
    checkin = CheckIn("TaBi Token", AccountConfig(provider="tabitoken"), provider)
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {
        "success": True,
        "data": {"flow_token": "flow-token-value"},
    }
    response.cookies = MagicMock()
    response.cookies.__len__.return_value = 0
    response.cookies.jar = []
    session = MagicMock()
    session.post.return_value = response

    result = asyncio.run(checkin.get_auth_state(session, {"Accept": "application/json"}, provider="github"))

    assert result == {"success": True, "state": "flow-token-value", "cookies": []}
    session.get.assert_not_called()
    session.post.assert_called_once_with(
        "https://tabitoken.com/api/oauth/state",
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        json={"provider": "github", "intent": "login"},
        timeout=30,
    )


def test_cookie_checkin_accepts_new_session_access_token():
    parameter = inspect.signature(CheckIn.check_in_with_cookies).parameters["access_token"]

    assert parameter.default is None


def test_tabitoken_pat_uses_bearer_without_legacy_user_header(monkeypatch):
    provider = replace(_providers(monkeypatch)["tabitoken"], check_in_status=False)
    checkin = CheckIn("TaBi Token", AccountConfig(provider="tabitoken"), provider)
    checkin_response = MagicMock()
    checkin_response.status_code = 200
    checkin_response.json.return_value = {
        "success": True,
        "data": {"checkin_date": "2026-08-25", "quota_awarded": 500000},
    }
    user_response = MagicMock()
    user_response.status_code = 200
    user_response.json.return_value = {
        "success": True,
        "data": {"quota": 1000000, "used_quota": 0, "bonus_quota": 500000},
    }
    session = MagicMock()
    session.post.return_value = checkin_response
    session.get.return_value = user_response

    with patch("checkin.curl_requests.Session", return_value=session):
        success, result = asyncio.run(
            checkin.check_in_with_system_access_token("pat-value", {}, {}, None)
        )

    assert success is True
    assert result["quota"] == 2.0
    post_headers = session.post.call_args.kwargs["headers"]
    get_headers = session.get.call_args.kwargs["headers"]
    assert post_headers["Authorization"] == "Bearer pat-value"
    assert get_headers["Authorization"] == "Bearer pat-value"
    assert provider.api_user_key not in post_headers
    assert provider.api_user_key not in get_headers


def test_turnstile_retry_uses_encoded_token(monkeypatch):
    provider = _providers(monkeypatch)["tabitoken"]
    checkin = CheckIn("TaBi Token", AccountConfig(provider="tabitoken"), provider)
    challenge_response = MagicMock()
    challenge_response.status_code = 403
    challenge_response.json.return_value = {
        "success": False,
        "message": "Turnstile token required",
    }
    success_response = MagicMock()
    success_response.status_code = 200
    success_response.json.return_value = {"success": True}
    session = MagicMock()
    session.post.side_effect = [challenge_response, success_response]
    headers = {}
    solution = ("token+/=", {"cf_clearance": "cookie"}, {"User-Agent": "browser"})
    with patch("checkin.get_turnstile_token", new=AsyncMock(return_value=solution)):
        result = asyncio.run(checkin.execute_check_in_with_turnstile(session, headers, None))

    assert result["success"] is True
    assert session.post.call_args_list[1].args[0] == (
        "https://tabitoken.com/api/user/checkin?turnstile=token%2B%2F%3D"
    )
    session.cookies.update.assert_called_once_with({"cf_clearance": "cookie"})
    assert headers["User-Agent"] == "browser"


def test_turnstile_missing_message_on_403_requests_challenge(monkeypatch):
    provider = _providers(monkeypatch)["tabitoken"]
    checkin = CheckIn("TaBi Token", AccountConfig(provider="tabitoken"), provider)
    response = MagicMock()
    response.status_code = 403
    response.json.return_value = {"success": False}
    session = MagicMock()
    session.post.return_value = response

    result = checkin.execute_check_in(session, {}, None)

    assert result == {
        "success": False,
        "error": "Unknown error",
        "turnstile_required": True,
    }


def test_tabitoken_workflow_starts_pinned_local_vmess_proxy():
    workflow = (
        Path(__file__).parent.parent / ".github" / "workflows" / "tabitoken.yml"
    ).read_text(encoding="utf-8")

    assert "TABITOKEN_CLASH_CONFIG: ${{ secrets.TABITOKEN_CLASH_CONFIG }}" in workflow
    assert 'mihomoVersion = "v1.19.30"' in workflow
    assert (
        'mihomoSha256 = "289fde5e29d37a5b3326480590d8b3551c5bf7f8737290355c19bce74d57a563"'
        in workflow
    )
    assert '& $mihomoExe -t -f $configPath' in workflow
    assert 'VMESS_EGRESS_CHANGED=True' in workflow
    assert '$env:PROXY = \'{"server":"http://127.0.0.1:7890"}\'' in workflow
    assert "qualification-isbn-improvements-governments.trycloudflare.com" not in workflow
