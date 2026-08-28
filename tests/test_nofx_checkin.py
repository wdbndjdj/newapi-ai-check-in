import base64
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))

from nofx_checkin import classify_checkin_button, load_storage_state


def test_classify_checkin_button_states():
	assert classify_checkin_button('领取积分', False) == 'claim'
	assert classify_checkin_button('已签到', True) == 'already'
	assert classify_checkin_button('Check-in', False) == 'claim'
	assert classify_checkin_button('设置', False) is None


def test_load_storage_state_from_base64(monkeypatch):
	state = {'cookies': [{'name': 'session', 'value': 'masked'}]}
	encoded = base64.b64encode(json.dumps(state).encode()).decode()
	monkeypatch.delenv('NOFX_STORAGE_STATE_FILE', raising=False)
	monkeypatch.delenv('NOFX_STORAGE_STATE', raising=False)
	monkeypatch.setenv('NOFX_STORAGE_STATE_B64', encoded)
	assert load_storage_state() == state


def test_load_storage_state_requires_cookie_array(monkeypatch):
	monkeypatch.delenv('NOFX_STORAGE_STATE_FILE', raising=False)
	monkeypatch.delenv('NOFX_STORAGE_STATE', raising=False)
	monkeypatch.setenv('NOFX_STORAGE_STATE', json.dumps({'origins': []}))
	with pytest.raises(RuntimeError, match='cookies'):
		load_storage_state()


def test_workflow_uses_session_secret_and_daily_schedule():
	workflow = (Path(__file__).parents[1] / '.github' / 'workflows' / 'nofx.yml').read_text(encoding='utf-8')
	assert "cron: '17 1 * * *'" in workflow
	assert 'NOFX_STORAGE_STATE_B64: ${{ secrets.NOFX_STORAGE_STATE_B64 }}' in workflow
	for index in range(2, 11):
		assert f'NOFX_STORAGE_STATE_B64_{index}: ${{{{ secrets.NOFX_STORAGE_STATE_B64_{index} }}}}' in workflow
	assert 'uv run python -u nofx_checkin.py' in workflow
	assert workflow.count('NOFX_STORAGE_STATE_B64') >= 20
