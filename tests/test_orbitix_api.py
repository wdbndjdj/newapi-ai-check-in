"""Offline Orbitix regression tests; all credentials and responses are fixtures."""

import importlib.util
import io
import json
import pathlib
import re
import unittest
import urllib.error
import urllib.request
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from email.message import Message
from unittest.mock import patch
from urllib.response import addinfourl


ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('orbitix_checkin', ROOT / 'orbitix_checkin.py')
app = importlib.util.module_from_spec(spec)
if (ROOT / 'orbitix_checkin.py').is_file():
    spec.loader.exec_module(app)
DAY = '2026-10-24'
NOW = datetime(2026, 10, 24, 10, 7, tzinfo=timezone(timedelta(hours=8)))
TOMORROW = NOW + timedelta(days=1)
TOKEN = 'fixture-private-token%value'
IDENTITY = {'success': True, 'data': {'id': 42, 'profile': 'fixture-private-profile'}}
STATUS_PATH = '/api/user/checkin?month=2026-10'


def status(checked=False, records=None):
    return {'success': True, 'data': {'stats': {
        'checked_in_today': checked, 'records': [] if records is None else records}}}


def record(day=DAY, award=2500000):
    return {'checkin_date': day, 'quota_awarded': award}


def checked(day=DAY):
    return status(True, [record(day)])


class OfflineTest(unittest.TestCase):
    def setUp(self):
        self.addCleanup(patch.stopall)
        patch('socket.socket', side_effect=AssertionError('real network forbidden')).start()
        patch.object(urllib.request, 'urlopen', side_effect=AssertionError('urlopen forbidden')).start()


class OrbitixLogicTest(OfflineTest):
    def setUp(self):
        super().setUp()
        self.clock = patch.object(app, 'datetime').start()
        self.clock.now.return_value = NOW

    def invoke(self, replies):
        with patch.object(app, 'request_json', side_effect=replies) as api:
            result = app.checkin(TOKEN)
        return result, api.call_args_list

    def test_already_checked_never_posts(self):
        result, calls = self.invoke([IDENTITY, checked()])
        self.assertEqual(result, {'site': 'Orbitix', 'date': DAY, 'record_date': DAY,
                                  'result': 'already_checked_in', 'quota_awarded': 2500000})
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0].args, ('/api/user/self', TOKEN))
        self.assertEqual(calls[0].kwargs, {})
        self.assertEqual(calls[1].args, (STATUS_PATH, TOKEN))
        self.assertEqual(calls[1].kwargs, {'user_id': 42})

    def test_missing_checkin_posts_once_then_gets_exact_record(self):
        result, calls = self.invoke([IDENTITY, status(), {'success': True}, checked()])
        self.assertEqual(result['result'], 'checked_in')
        self.assertEqual(result['record_date'], DAY)
        self.assertEqual(calls[2].args, ('/api/user/checkin', TOKEN))
        self.assertEqual(calls[2].kwargs, {'method': 'POST', 'user_id': 42})
        self.assertEqual(calls[3].args, (STATUS_PATH, TOKEN))
        self.assertEqual(calls[3].kwargs, {'user_id': 42})

    def test_post_success_and_award_are_not_verification(self):
        with patch.object(app, 'request_json', side_effect=[
                IDENTITY, status(), {'success': True, 'data': record()}, status()]) as api:
            with self.assertRaises(app.CheckinError):
                app.checkin(TOKEN)
        self.assertEqual(api.call_count, 4)

    def test_wrong_date_never_verifies_even_when_flag_true(self):
        with patch.object(app, 'request_json', side_effect=[
                IDENTITY, status(), {'success': True}, checked('2026-10-23')]):
            with self.assertRaises(app.CheckinError):
                app.checkin(TOKEN)

    def test_post_award_cannot_override_verified_award(self):
        result, _ = self.invoke([IDENTITY, status(), {'success': True, 'data': record(award=999)}, checked()])
        self.assertEqual(result['quota_awarded'], 2500000)

    def test_exact_record_prevents_post_even_when_flag_lags(self):
        result, calls = self.invoke([IDENTITY, status(False, [record()])])
        self.assertEqual(result['result'], 'already_checked_in')
        self.assertEqual(len(calls), 2)

    def test_concurrent_business_rejection_requires_get_verification(self):
        result, calls = self.invoke([IDENTITY, status(),
                                    {'success': False, 'message': 'fixture-private-message'}, checked()])
        self.assertEqual(result['result'], 'checked_in')
        self.assertEqual(len(calls), 4)

    def test_concurrent_rejection_without_record_fails(self):
        with patch.object(app, 'request_json', side_effect=[
                IDENTITY, status(), {'success': False}, status()]) as api:
            with self.assertRaises(app.CheckinError):
                app.checkin(TOKEN)
        self.assertEqual(api.call_count, 4)

    def test_uncertain_post_and_http_conflict_are_read_only_reconciled(self):
        for code in ('network', 'conflict'):
            with self.subTest(code=code):
                result, calls = self.invoke([IDENTITY, status(), app.CheckinError(code), checked()])
                self.assertEqual(result['result'], 'checked_in')
                self.assertEqual(sum(c.kwargs.get('method') == 'POST' for c in calls), 1)

    def test_post_auth_challenge_redirect_or_schema_failure_stops(self):
        for code in ('auth', 'challenge', 'redirect', 'schema'):
            with self.subTest(code=code), patch.object(app, 'request_json', side_effect=[
                    IDENTITY, status(), app.CheckinError(code)]) as api:
                with self.assertRaises(app.CheckinError):
                    app.checkin(TOKEN)
                self.assertEqual(api.call_count, 3)

    def test_malformed_post_envelope_fails(self):
        for response in ({}, {'success': 1}, None, []):
            with self.subTest(response=response), patch.object(app, 'request_json', side_effect=[
                    IDENTITY, status(), response]) as api:
                with self.assertRaises(app.CheckinError):
                    app.checkin(TOKEN)
                self.assertEqual(api.call_count, 3)

    def test_invalid_identity_stops_before_status_or_post(self):
        for response in ({'success': False}, {'success': True},
                         {'success': True, 'data': {'id': True}},
                         {'success': True, 'data': {'id': '42'}},
                         {'success': True, 'data': {'id': 0}},
                         {'success': True, 'data': {'id': -1}}):
            with self.subTest(response=response), patch.object(app, 'request_json', return_value=response) as api:
                with self.assertRaises(app.CheckinError):
                    app.checkin(TOKEN)
                self.assertEqual(api.call_count, 1)

    def test_invalid_status_never_becomes_permission_to_post(self):
        invalid = [None, [], {}, {'success': False}, {'success': 1, 'data': {}},
                   {'success': True, 'data': []}, {'success': True, 'data': {'stats': {}}},
                   status('false'), status(1), status(None), status(True),
                   status(False, {}), status(False, [None]),
                   status(False, [{'checkin_date': DAY}]),
                   status(False, [record('2026-02-30')]),
                   status(False, [record('2026-1-1')]),
                   status(False, [record(), record()])]
        for response in invalid:
            with self.subTest(response=response), patch.object(app, 'request_json', side_effect=[IDENTITY, response]) as api:
                with self.assertRaises(app.CheckinError):
                    app.checkin(TOKEN)
                self.assertEqual(api.call_count, 2)

    def test_invalid_quota_is_not_a_verified_record(self):
        for award in (None, True, '2500000', -1, float('nan'), float('inf')):
            with self.subTest(award=award), patch.object(app, 'request_json', side_effect=[
                    IDENTITY, status(True, [record(award=award)])]):
                with self.assertRaises(app.CheckinError):
                    app.checkin(TOKEN)

    def test_zero_quota_is_valid(self):
        result, _ = self.invoke([IDENTITY, status(True, [record(award=0)])])
        self.assertEqual(result['quota_awarded'], 0)

    def test_previous_dates_do_not_suppress_today_post(self):
        result, calls = self.invoke([IDENTITY, status(False, [record('2026-10-23')]),
                                    {'success': True}, checked()])
        self.assertEqual(result['result'], 'checked_in')
        self.assertEqual(len(calls), 4)

    def test_midnight_during_identity_or_status_never_posts(self):
        for change_at, replies, expected_calls in (
                (1, [IDENTITY], 1), (2, [IDENTITY, status()], 2)):
            with self.subTest(change_at=change_at):
                self.clock.now.side_effect = [NOW] * change_at + [TOMORROW] * 10
                with patch.object(app, 'request_json', side_effect=replies) as api:
                    with self.assertRaises(app.CheckinError):
                        app.checkin(TOKEN)
                self.assertEqual(api.call_count, expected_calls)

    def test_midnight_during_post_or_verification_never_reports_success(self):
        for change_at in (3, 4):
            with self.subTest(change_at=change_at):
                self.clock.now.side_effect = [NOW] * change_at + [TOMORROW] * 10
                with patch.object(app, 'request_json', side_effect=[IDENTITY, status(), {'success': True}, checked()]) as api:
                    with self.assertRaises(app.CheckinError):
                        app.checkin(TOKEN)
                self.assertEqual(sum(c.kwargs.get('method') == 'POST' for c in api.call_args_list), 1)

    def test_month_is_derived_from_beijing_not_host_clock(self):
        self.clock.now.return_value = datetime(2027, 1, 1, 0, 1, tzinfo=app.BEIJING)
        with patch.object(app, 'request_json', side_effect=[IDENTITY, checked('2027-01-01')]) as api:
            app.checkin(TOKEN)
        self.assertEqual(api.call_args_list[1].args[0], '/api/user/checkin?month=2027-01')
        self.clock.now.assert_called_with(app.BEIJING)

    def test_missing_or_header_injecting_token_fails_before_api(self):
        for token in ('', ' ', None, 'fixture\r\nInjected: value', 'fixture token', 'fixture\x00token', '\u4ee4\u724c'):
            with self.subTest(token=token), patch.object(app, 'request_json') as api:
                with self.assertRaises(app.CheckinError):
                    app.checkin(token)
                api.assert_not_called()


class Response:
    def __init__(self, body=None, *, raw=None, url='https://new.orbitix.site/api/user/self',
                 content_type='application/json', code=200, extra_headers=None):
        self.payload = io.BytesIO(json.dumps(IDENTITY if body is None else body).encode() if raw is None else raw)
        self.url, self.code = url, code
        self.headers = Message()
        self.headers['Content-Type'] = content_type
        for name, value in (extra_headers or {}).items():
            self.headers[name] = value

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.payload.close()

    def read(self, size):
        return self.payload.read(size)

    def geturl(self):
        return self.url

    def getcode(self):
        return self.code


class OrbitixTransportTest(OfflineTest):
    def request(self, response=None, **kwargs):
        with patch.object(app.urllib.request, 'build_opener') as factory:
            opener = factory.return_value
            if isinstance(response, Exception):
                opener.open.side_effect = response
            else:
                opener.open.return_value = response or Response()
            result = app.request_json('/api/user/self', TOKEN, **kwargs)
            return result, opener.open.call_args

    def test_bearer_and_identity_headers_and_timeout(self):
        result, call = self.request(user_id=42)
        request = call.args[0]
        self.assertEqual(result, IDENTITY)
        self.assertEqual(request.full_url, 'https://new.orbitix.site/api/user/self')
        self.assertEqual(request.get_header('Authorization'), 'Bearer ' + TOKEN)
        self.assertEqual(request.get_header('New-api-user'), '42')
        self.assertEqual(call.kwargs['timeout'], 30)

    def test_identity_request_has_no_user_header(self):
        _, call = self.request()
        self.assertIsNone(call.args[0].get_header('New-api-user'))

    def test_post_has_json_body_and_identity(self):
        with patch.object(app.urllib.request, 'build_opener') as factory:
            factory.return_value.open.return_value = Response(
                {'success': True}, url='https://new.orbitix.site/api/user/checkin')
            app.request_json('/api/user/checkin', TOKEN, method='POST', user_id=42)
        request = factory.return_value.open.call_args.args[0]
        self.assertEqual(request.get_method(), 'POST')
        self.assertEqual(request.data, b'{}')
        self.assertEqual(request.get_header('New-api-user'), '42')
        self.assertEqual(request.get_header('Content-type'), 'application/json')

    def test_origin_and_endpoint_validation_rejects_before_open(self):
        for path in ('https://evil.example/api/user/self', '//evil.example/', '/api/user/self#x',
                     '/api/user/self?token=fixture', '/api/user/checkin?month=2026-13',
                     '/api/user/../self', '/api/user/checkin?month=2026-10&next=https://evil.example'):
            with self.subTest(path=path), patch.object(app.urllib.request, 'build_opener') as factory:
                with self.assertRaises(app.CheckinError):
                    app.request_json(path, TOKEN)
                factory.assert_not_called()
        for origin in ('http://new.orbitix.site', 'https://new.orbitix.site.evil.example',
                       'https://user@new.orbitix.site', 'https://new.orbitix.site:444',
                       'https://new.orbitix.site/'):
            with self.subTest(origin=origin), patch.object(app, 'ORIGIN', origin), \
                    patch.object(app.urllib.request, 'build_opener') as factory:
                with self.assertRaises(app.CheckinError):
                    app.request_json('/api/user/self', TOKEN)
                factory.assert_not_called()

    def test_unsupported_methods_fail_without_opening(self):
        for method in ('DELETE', 'PUT', 'HEAD', 'post'):
            with self.subTest(method=method), patch.object(app.urllib.request, 'build_opener') as factory:
                with self.assertRaises(app.CheckinError):
                    app.request_json('/api/user/checkin', TOKEN, method=method)
                factory.assert_not_called()

    def test_all_redirect_statuses_are_rejected_without_following(self):
        class FixtureHTTPS(urllib.request.HTTPSHandler):
            def __init__(self, code, target):
                super().__init__()
                self.code, self.target, self.calls = code, target, []

            def https_open(self, req):
                self.calls.append(req)
                headers = Message()
                headers['Location'] = self.target
                response = addinfourl(io.BytesIO(b'fixture-private-server-body'), headers, req.full_url, self.code)
                response.msg = 'Redirect'
                return response

        real_build = urllib.request.build_opener
        for code in (301, 302, 303, 307, 308):
            for target in ('https://evil.example/collect', 'http://new.orbitix.site/insecure', '/same-origin'):
                with self.subTest(code=code, target=target):
                    fixture = FixtureHTTPS(code, target)
                    def build(*handlers):
                        return real_build(fixture, *handlers)
                    with patch.object(app.urllib.request, 'build_opener', side_effect=build):
                        with self.assertRaises(app.CheckinError) as raised:
                            app.request_json('/api/user/self', TOKEN)
                    self.assertEqual(raised.exception.code, 'redirect')
                    self.assertEqual(len(fixture.calls), 1)

    def test_effective_response_url_cannot_change(self):
        with self.assertRaises(app.CheckinError):
            self.request(Response(url='https://evil.example/collect'))

    def test_challenges_html_and_invalid_json_fail_closed(self):
        responses = [Response(raw=b'<html>fixture-private-challenge</html>', content_type='text/html'),
                     Response(extra_headers={'cf-mitigated': 'challenge'}),
                     Response({'success': False, 'message': 'Please solve the CAPTCHA challenge'}),
                     Response(raw=b'not-json-fixture-private-body'), Response(raw=b'\xff'),
                     Response([]), Response({'success': 1}),
                     Response(raw=b' ' * (1024 * 1024 + 1))]
        for response in responses:
            with self.subTest(response=response), self.assertRaises(app.CheckinError):
                self.request(response)

    def test_http_and_network_errors_have_only_static_messages(self):
        for error in (urllib.error.HTTPError('https://fixture-private-url', code, TOKEN, {}, io.BytesIO(b'private'))
                      for code in (301, 401, 403, 409, 429, 500)):
            with self.subTest(code=error.code), self.assertRaises(app.CheckinError) as raised:
                self.request(error)
            self.assertNotIn('fixture', str(raised.exception))
            self.assertNotIn('private', str(raised.exception))
        for error in (urllib.error.URLError(TOKEN), TimeoutError(TOKEN), OSError(TOKEN)):
            with self.subTest(error=type(error)), self.assertRaises(app.CheckinError) as raised:
                self.request(error)
            self.assertEqual(raised.exception.code, 'network')
            self.assertNotIn(TOKEN, str(raised.exception))


class OrbitixMainTest(OfflineTest):
    def setUp(self):
        super().setUp()
        self.clock = patch.object(app, 'datetime').start()
        self.clock.now.return_value = NOW

    def run_main(self, replies, *, token=TOKEN, actions=False, summary=None):
        out, err = io.StringIO(), io.StringIO()
        env = {'ORBITIX_ACCESS_TOKEN': token, 'GITHUB_ACTIONS': 'true' if actions else 'false'}
        if summary is not None:
            env['GITHUB_STEP_SUMMARY'] = summary
        with patch.dict(app.os.environ, env, clear=True), \
                patch.object(app, 'request_json', side_effect=replies) as api, \
                redirect_stdout(out), redirect_stderr(err):
            code = app.main()
        return code, out.getvalue(), err.getvalue(), api

    def test_safe_receipt_has_no_identity_profile_or_token(self):
        code, out, err, _ = self.run_main([IDENTITY, checked()])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out), {'site': 'Orbitix', 'date': DAY, 'record_date': DAY,
                                         'result': 'already_checked_in', 'quota_awarded': 2500000})
        self.assertEqual(err, '')
        self.assertNotIn('fixture', out)
        self.assertNotIn('profile', out)
        self.assertNotIn('user_id', out)

    def test_actions_mask_is_flushed_before_network(self):
        out, err = io.StringIO(), io.StringIO()
        replies = iter([IDENTITY, checked()])
        def api(*args, **kwargs):
            self.assertEqual(out.getvalue(), '::add-mask::fixture-private-token%25value\n')
            return next(replies)
        with patch.dict(app.os.environ, {'ORBITIX_ACCESS_TOKEN': TOKEN, 'GITHUB_ACTIONS': 'true'}, clear=True), \
                patch.object(app, 'request_json', side_effect=api), \
                redirect_stdout(out), redirect_stderr(err):
            self.assertEqual(app.main(), 0)
        self.assertEqual(len(out.getvalue().splitlines()), 2)
        self.assertNotIn(TOKEN, out.getvalue().splitlines()[-1])
        self.assertEqual(err.getvalue(), '')

    def test_all_failure_logs_are_sanitized_and_never_emit_receipt(self):
        for error in (RuntimeError('fixture-private-profile ' + TOKEN), app.CheckinError('auth'),
                      app.CheckinError('fixture-private-server-body'), ValueError(TOKEN)):
            with self.subTest(error=type(error)):
                code, out, err, api = self.run_main([error])
                self.assertEqual(code, 1)
                self.assertEqual(out, '')
                self.assertNotIn('fixture', err)
                self.assertNotIn('Traceback', err)
                self.assertEqual(api.call_count, 1)

    def test_missing_token_has_no_mask_or_network(self):
        code, out, err, api = self.run_main([], token='', actions=True)
        self.assertEqual(code, 1)
        self.assertEqual(out, '')
        self.assertIn('Orbitix', err)
        api.assert_not_called()

    def test_old_day_receipt_is_not_emitted_after_midnight(self):
        result = {'site': 'Orbitix', 'date': DAY, 'record_date': DAY,
                  'result': 'already_checked_in', 'quota_awarded': 0}
        self.clock.now.return_value = TOMORROW
        with patch.object(app, 'checkin', return_value=result):
            code, out, _, api = self.run_main([])
        self.assertEqual(code, 1)
        self.assertEqual(out, '')
        api.assert_not_called()

    def test_step_summary_is_useful_and_sanitized_for_success_and_failure(self):
        for replies, expected, exit_code in (([IDENTITY, checked()], 'already_checked_in', 0),
                                             ([RuntimeError(TOKEN)], 'failed', 1)):
            with self.subTest(expected=expected), patch('builtins.open', unittest.mock.mock_open()) as opened:
                code, _, _, _ = self.run_main(replies, summary='fixture-summary')
                self.assertEqual(code, exit_code)
                text = ''.join(c.args[0] for c in opened().write.call_args_list)
                self.assertIn('Orbitix', text)
                self.assertIn(expected, text)
                self.assertNotIn('fixture', text)
                self.assertNotIn('profile', text)

    def test_unwritable_optional_summary_does_not_change_checkin_result(self):
        with patch('builtins.open', side_effect=OSError(TOKEN)):
            code, out, err, _ = self.run_main([IDENTITY, checked()], summary='fixture-summary')
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)['record_date'], DAY)
        self.assertNotIn('fixture', err)


class OrbitixWorkflowTest(unittest.TestCase):
    def test_workflow_is_isolated_and_tests_before_secret_bearing_step(self):
        workflow = (ROOT / '.github/workflows/orbitix.yml').read_text(encoding='utf-8')
        for setting in ("cron: '07 2 * * *'", 'workflow_dispatch:', 'runs-on: ubuntu-latest',
                        "python-version: '3.11'", 'contents: read', 'timeout-minutes: 15',
                        'cancel-in-progress: false', 'group: orbitix-checkin',
                        'ORBITIX_ACCESS_TOKEN: ${{ secrets.ORBITIX_ACCESS_TOKEN }}'):
            self.assertIn(setting, workflow)
        self.assertIsNone(re.search(r'^\s*environment\s*:', workflow, re.MULTILINE))
        self.assertEqual(re.findall(r'secrets\.([A-Z_]+)', workflow), ['ORBITIX_ACCESS_TOKEN'])
        self.assertLess(workflow.index('python -B tests/test_orbitix_api.py'), workflow.index('secrets.ORBITIX_ACCESS_TOKEN'))
        self.assertIn('python -u -B orbitix_checkin.py', workflow)
        self.assertNotIn('upload-artifact', workflow)
        self.assertNotIn('pip install', workflow)


class OrbitixBootstrapTest(unittest.TestCase):
    def test_isolated_helper_exists(self):
        self.assertTrue((ROOT / 'orbitix_checkin.py').is_file(), 'isolated Orbitix helper is missing')


if __name__ == '__main__':
    unittest.main()
