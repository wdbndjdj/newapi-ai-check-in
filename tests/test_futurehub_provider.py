import inspect
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from checkin import CheckIn
from utils.config import AppConfig


def test_futurehub_is_available_when_custom_providers_are_invalid(monkeypatch):
    monkeypatch.setenv("PROVIDERS", "[]")

    provider = AppConfig._load_providers("PROVIDERS")["futurehub"]

    assert provider.origin == "https://api.futureppo.top"
    assert provider.get_login_url() == "https://api.futureppo.top/login"
    assert provider.get_check_in_url("1") == "https://api.futureppo.top/api/user/checkin"
    assert provider.check_in_status is True


def test_cookie_checkin_accepts_browser_impersonation():
    parameter = inspect.signature(CheckIn.check_in_with_cookies).parameters["impersonate"]

    assert parameter.default is None
