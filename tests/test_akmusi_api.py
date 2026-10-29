"""Offline tests for the Akmusi New API daily check-in profile."""

import importlib.util
import io
import json
import os
import pathlib
import sys
import tempfile
import unittest
from email.message import Message
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from unittest.mock import patch


ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('orbitix_checkin', ROOT / 'orbitix_checkin.py')
app = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = app
spec.loader.exec_module(app)
sys.path.insert(0, str(ROOT))

DAY = '2026-10-29'
NOW = datetime(2026, 10, 29, 12, 0, tzinfo=timezone(timedelta(hours=8)))
TOKEN = 'fixture-private-akmusi-token'
IDENTITY = {'success': True, 'data': {'id': 42}}
CHECKED = {'success': True, 'data': {'stats': {
    'checked_in_today': True,
    'records': [{'checkin_date': DAY, 'quota_awarded': 1.25}],
}}}


class AkmusiProfileTest(unittest.TestCase):
    def test_profile_uses_only_the_requested_origin_and_dedicated_secret(self):
        profile = getattr(app, 'SITE_PROFILES', {}).get('akmusi', {})
        self.assertEqual(profile.get('origin'), 'https://ai.akmusi.online')
        self.assertEqual(profile.get('token_env'), 'AKMUSI_ACCESS_TOKEN')
        self.assertEqual(profile.get('name'), 'Akmusi AI')

    def test_runner_uses_the_akmusi_secret_masks_it_and_restores_default_profile(self):
        from akmusi_checkin import main as akmusi_main

        out, err = io.StringIO(), io.StringIO()
        with patch.object(app, 'datetime') as clock, \
                patch.object(app, 'request_json', side_effect=[IDENTITY, CHECKED]) as request, \
                patch.dict(os.environ, {'AKMUSI_ACCESS_TOKEN': TOKEN, 'GITHUB_ACTIONS': 'true'}, clear=True), \
                redirect_stdout(out), redirect_stderr(err):
            clock.now.return_value = NOW
            code = akmusi_main()

        lines = out.getvalue().splitlines()
        self.assertEqual(code, 0)
        self.assertEqual(lines[0], '::add-mask::' + TOKEN)
        receipt = json.loads(lines[1])
        self.assertEqual(receipt, {'site': 'Akmusi AI', 'date': DAY, 'record_date': DAY,
                                   'result': 'already_checked_in', 'quota_awarded': 1.25})
        self.assertNotIn(TOKEN, lines[-1])
        self.assertEqual(err.getvalue(), '')
        self.assertEqual(request.call_count, 2)
        self.assertEqual(app.ORIGIN, 'https://new.orbitix.site')

    def test_transport_sends_bearer_only_to_the_exact_akmusi_host(self):
        class Reply:
            def __init__(self):
                self.headers = Message()
                self.headers['Content-Type'] = 'application/json'

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return None

            def geturl(self):
                return 'https://ai.akmusi.online/api/user/self'

            def getcode(self):
                return 200

            def read(self, size):
                return json.dumps(IDENTITY).encode()

        original = (app.PROFILE, app.ORIGIN, app.SITE_NAME, app.TOKEN_ENV, app.USER_AGENT)
        try:
            app.configure_profile('akmusi')
            with patch.object(app.urllib.request, 'build_opener') as factory:
                factory.return_value.open.return_value = Reply()
                app.request_json('/api/user/self', TOKEN)
            call = factory.return_value.open.call_args
            request = call.args[0]
            self.assertEqual(request.full_url, 'https://ai.akmusi.online/api/user/self')
            self.assertEqual(request.get_header('Authorization'), 'Bearer ' + TOKEN)
            self.assertEqual(request.get_header('User-agent'), 'Akmusi-AI-Daily-Checkin/1.0')
        finally:
            app.PROFILE, app.ORIGIN, app.SITE_NAME, app.TOKEN_ENV, app.USER_AGENT = original

    def test_profile_rejects_unlisted_origins_and_profile_names(self):
        original = (app.PROFILE, app.ORIGIN, app.SITE_NAME, app.TOKEN_ENV, app.USER_AGENT)
        try:
            with self.assertRaises(app.CheckinError):
                app.configure_profile('unlisted')
            app.configure_profile('akmusi')
            with patch.object(app, 'ORIGIN', 'https://ai.akmusi.online.attacker.example'), \
                    patch.object(app.urllib.request, 'build_opener') as factory:
                with self.assertRaises(app.CheckinError):
                    app.request_json('/api/user/self', TOKEN)
                factory.assert_not_called()
        finally:
            app.PROFILE, app.ORIGIN, app.SITE_NAME, app.TOKEN_ENV, app.USER_AGENT = original

    def test_akmusi_profile_rejects_another_allowlisted_sites_origin_before_network(self):
        original = (app.PROFILE, app.ORIGIN, app.SITE_NAME, app.TOKEN_ENV, app.USER_AGENT)
        try:
            app.configure_profile('akmusi')
            with patch.object(app, 'ORIGIN', 'https://new.orbitix.site'), \
                    patch.object(app.urllib.request, 'build_opener') as factory:
                with self.assertRaises(app.CheckinError):
                    app.request_json('/api/user/self', TOKEN)
                factory.assert_not_called()
        finally:
            app.PROFILE, app.ORIGIN, app.SITE_NAME, app.TOKEN_ENV, app.USER_AGENT = original

    def test_missing_checkin_posts_once_and_verifies_on_akmusi_origin(self):
        from akmusi_checkin import main as akmusi_main

        absent = {'success': True, 'data': {'stats': {
            'checked_in_today': False, 'records': [],
        }}}
        calls = []
        responses = iter([IDENTITY, absent, {'success': True}, CHECKED])

        def route(path, token, *, method='GET', user_id=None):
            calls.append((app.ORIGIN, path, method, token, user_id))
            return next(responses)

        out = io.StringIO()
        with patch.object(app, 'datetime') as clock, \
                patch.object(app, 'request_json', side_effect=route), \
                patch.dict(os.environ, {'AKMUSI_ACCESS_TOKEN': TOKEN}, clear=True), \
                redirect_stdout(out):
            clock.now.return_value = NOW
            code = akmusi_main()

        expected = [
            ('https://ai.akmusi.online', '/api/user/self', 'GET', TOKEN, None),
            ('https://ai.akmusi.online', '/api/user/checkin?month=2026-10', 'GET', TOKEN, 42),
            ('https://ai.akmusi.online', '/api/user/checkin', 'POST', TOKEN, 42),
            ('https://ai.akmusi.online', '/api/user/checkin?month=2026-10', 'GET', TOKEN, 42),
        ]
        self.assertEqual(code, 0)
        self.assertEqual(calls, expected)
        self.assertEqual(json.loads(out.getvalue()), {
            'site': 'Akmusi AI', 'date': DAY, 'record_date': DAY,
            'result': 'checked_in', 'quota_awarded': 1.25,
        })

    def test_akmusi_step_summary_failure_names_the_active_site(self):
        original = (app.PROFILE, app.ORIGIN, app.SITE_NAME, app.TOKEN_ENV, app.USER_AGENT)
        try:
            app.configure_profile('akmusi')
            with tempfile.TemporaryDirectory() as directory:
                summary = pathlib.Path(directory) / 'missing' / 'summary.md'
                err = io.StringIO()
                with patch.dict(os.environ, {'GITHUB_STEP_SUMMARY': str(summary)}, clear=True), \
                        redirect_stderr(err):
                    app.write_summary(error_code='internal')
            self.assertEqual(err.getvalue(), 'Akmusi AI step summary unavailable\n')
        finally:
            app.PROFILE, app.ORIGIN, app.SITE_NAME, app.TOKEN_ENV, app.USER_AGENT = original

    def test_workflow_has_daily_and_manual_runs_and_injects_only_its_token_after_tests(self):
        adapter = ROOT / 'akmusi_checkin.py'
        workflow_path = ROOT / '.github/workflows/akmusi.yml'
        self.assertTrue(adapter.is_file(), 'Akmusi runner is missing')
        self.assertTrue(workflow_path.is_file(), 'Akmusi GitHub Actions workflow is missing')
        workflow = workflow_path.read_text(encoding='utf-8')
        for setting in ("cron: '07 2 * * *'", 'workflow_dispatch:', 'runs-on: ubuntu-latest',
                        'actions/checkout@v7', 'persist-credentials: false', 'actions/setup-python@v7',
                        "python-version: '3.11'", 'contents: read', 'timeout-minutes: 15',
                        'group: akmusi-checkin', 'AKMUSI_ACCESS_TOKEN: ${{ secrets.AKMUSI_ACCESS_TOKEN }}',
                        'python -u -B akmusi_checkin.py'):
            self.assertIn(setting, workflow)
        self.assertEqual(__import__('re').findall(r'secrets\.([A-Z_]+)', workflow), ['AKMUSI_ACCESS_TOKEN'])
        self.assertLess(workflow.index('python -B tests/test_akmusi_api.py'),
                        workflow.index('secrets.AKMUSI_ACCESS_TOKEN'))


if __name__ == '__main__':
    unittest.main()
