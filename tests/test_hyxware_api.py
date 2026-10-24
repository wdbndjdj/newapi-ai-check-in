"""Offline Hyxware regression tests; all credentials and responses are fixtures."""

import http.client
import importlib
import io
import json
import pathlib
import re
import sys
import unittest
import urllib.error
import urllib.request
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from email.message import Message
from unittest.mock import patch
from urllib.response import addinfourl


ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
app = importlib.import_module('hyxware_checkin')
DAY = '2026-10-24'
NOW = datetime(2026, 10, 24, 10, 17, tzinfo=timezone(timedelta(hours=8)))
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
        patch('socket.getaddrinfo', side_effect=AssertionError('real DNS forbidden')).start()
        patch.object(urllib.request, 'urlopen', side_effect=AssertionError('urlopen forbidden')).start()


class HyxwareLogicTest(OfflineTest):
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
        self.assertEqual(result, {'site': 'Hyxware', 'date': DAY, 'record_date': DAY,
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

    def test_uncertain_post_schema_and_http_conflict_are_read_only_reconciled(self):
        for code in ('network', 'conflict', 'schema'):
            with self.subTest(code=code):
                result, calls = self.invoke([IDENTITY, status(), app.CheckinError(code), checked()])
                self.assertEqual(result['result'], 'checked_in')
                self.assertEqual(sum(c.kwargs.get('method') == 'POST' for c in calls), 1)

    def test_post_auth_challenge_redirect_or_http_failure_stops(self):
        for code in ('auth', 'challenge', 'redirect', 'http'):
            with self.subTest(code=code), patch.object(app, 'request_json', side_effect=[
                    IDENTITY, status(), app.CheckinError(code)]) as api:
                with self.assertRaises(app.CheckinError):
                    app.checkin(TOKEN)
                self.assertEqual(api.call_count, 3)

    def test_malformed_post_envelope_is_read_only_reconciled(self):
        for response in ({}, {'success': 1}, None, []):
            for final, verifies in ((checked(), True), (status(), False)):
                with self.subTest(response=response, verifies=verifies), \
                        patch.object(app, 'request_json', side_effect=[
                            IDENTITY, status(), response, final]) as api:
                    if verifies:
                        self.assertEqual(app.checkin(TOKEN)['result'], 'checked_in')
                    else:
                        with self.assertRaises(app.CheckinError) as raised:
                            app.checkin(TOKEN)
                        self.assertEqual(raised.exception.code, 'verification')
                    self.assertEqual([c.kwargs.get('method', 'GET') for c in api.call_args_list],
                                     ['GET', 'GET', 'POST', 'GET'])

    def test_malformed_post_challenge_envelope_stops_before_verification(self):
        for response in ({'message': 'CAPTCHA required'},
                         {'success': 1, 'requires_captcha': True},
                         {'success': None, 'challenge': True}):
            with self.subTest(response=response), patch.object(app, 'request_json', side_effect=[
                    IDENTITY, status(), response]) as api:
                with self.assertRaises(app.CheckinError) as raised:
                    app.checkin(TOKEN)
                self.assertEqual(raised.exception.code, 'challenge')
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


    def test_network_or_conflict_without_exact_record_never_reposts(self):
        for code in ('network', 'conflict', 'schema'):
            with self.subTest(code=code), patch.object(app, 'request_json', side_effect=[
                    IDENTITY, status(), app.CheckinError(code), status()]) as api:
                with self.assertRaises(app.CheckinError) as raised:
                    app.checkin(TOKEN)
                self.assertEqual(raised.exception.code, 'verification')
                self.assertEqual([c.kwargs.get('method', 'GET') for c in api.call_args_list],
                                 ['GET', 'GET', 'POST', 'GET'])

    def test_initial_query_failure_never_posts_or_retries(self):
        for code in ('network', 'conflict', 'auth', 'challenge', 'redirect', 'schema', 'http'):
            with self.subTest(code=code), patch.object(app, 'request_json', side_effect=[
                    IDENTITY, app.CheckinError(code)]) as api:
                with self.assertRaises(app.CheckinError):
                    app.checkin(TOKEN)
                self.assertEqual(api.call_count, 2)
                self.assertTrue(all(c.kwargs.get('method', 'GET') == 'GET' for c in api.call_args_list))

    def test_future_date_does_not_suppress_post_or_verify_today(self):
        for final in (status(False, [record('2026-10-25')]), checked('2026-10-25')):
            with self.subTest(final=final), patch.object(app, 'request_json', side_effect=[
                    IDENTITY, status(False, [record('2026-10-25')]), {'success': True}, final]) as api:
                with self.assertRaises(app.CheckinError):
                    app.checkin(TOKEN)
                self.assertEqual([c.kwargs.get('method', 'GET') for c in api.call_args_list],
                                 ['GET', 'GET', 'POST', 'GET'])

    def test_year_rollover_never_posts_or_verifies_previous_month(self):
        before = datetime(2026, 12, 31, 23, 59, 59, tzinfo=app.BEIJING)
        after = datetime(2027, 1, 1, 0, 0, tzinfo=app.BEIJING)
        for change_at in (2, 3, 4):
            with self.subTest(change_at=change_at):
                self.clock.now.side_effect = [before] * change_at + [after] * 10
                with patch.object(app, 'request_json', side_effect=[
                        IDENTITY, status(), {'success': True}, checked('2026-12-31')]) as api:
                    with self.assertRaises(app.CheckinError) as raised:
                        app.checkin(TOKEN)
                self.assertEqual(raised.exception.code, 'date_changed')
                self.assertEqual(api.call_count, change_at)
                self.assertEqual(api.call_args_list[1].args[0], '/api/user/checkin?month=2026-12')
                self.assertLessEqual(sum(c.kwargs.get('method') == 'POST' for c in api.call_args_list), 1)


class Response:
    def __init__(self, body=None, *, raw=None, url='https://hyxware.com/api/user/self',
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


class HyxwareTransportTest(OfflineTest):
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
        self.assertEqual(request.full_url, 'https://hyxware.com/api/user/self')
        self.assertEqual(request.get_header('Authorization'), 'Bearer ' + TOKEN)
        self.assertEqual(request.get_header('New-api-user'), '42')
        self.assertEqual(call.kwargs['timeout'], 30)

    def test_identity_request_has_no_user_header(self):
        _, call = self.request()
        self.assertIsNone(call.args[0].get_header('New-api-user'))

    def test_post_has_json_body_and_identity(self):
        with patch.object(app.urllib.request, 'build_opener') as factory:
            factory.return_value.open.return_value = Response(
                {'success': True}, url='https://hyxware.com/api/user/checkin')
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
        for origin in ('https://www.hyxware.com', 'https://new.orbitix.site', 'http://hyxware.com', 'https://hyxware.com.evil.example',
                       'https://user@hyxware.com', 'https://hyxware.com:444',
                       'https://hyxware.com/'):
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
            for target in ('https://evil.example/collect', 'http://hyxware.com/insecure', '/same-origin'):
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
        for error in (urllib.error.URLError(TOKEN), TimeoutError(TOKEN), OSError(TOKEN),
                      http.client.IncompleteRead(b'fixture-private-body', 100)):
            with self.subTest(error=type(error)), self.assertRaises(app.CheckinError) as raised:
                self.request(error)
            self.assertEqual(raised.exception.code, 'network')
            self.assertNotIn(TOKEN, str(raised.exception))


class HyxwareMainTest(OfflineTest):
    def setUp(self):
        super().setUp()
        self.clock = patch.object(app, 'datetime').start()
        self.clock.now.return_value = NOW

    def run_main(self, replies, *, token=TOKEN, actions=False, summary=None):
        out, err = io.StringIO(), io.StringIO()
        env = {'HYXWARE_ACCESS_TOKEN': token, 'GITHUB_ACTIONS': 'true' if actions else 'false'}
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
        self.assertEqual(json.loads(out), {'site': 'Hyxware', 'date': DAY, 'record_date': DAY,
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
        with patch.dict(app.os.environ, {'HYXWARE_ACCESS_TOKEN': TOKEN, 'GITHUB_ACTIONS': 'true'}, clear=True), \
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
        self.assertIn('Hyxware', err)
        api.assert_not_called()

    def test_old_day_receipt_is_not_emitted_after_midnight(self):
        result = {'site': 'Hyxware', 'date': DAY, 'record_date': DAY,
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
                self.assertIn('Hyxware', text)
                self.assertIn(expected, text)
                self.assertNotIn('fixture', text)
                self.assertNotIn('profile', text)

    def test_unwritable_optional_summary_does_not_change_checkin_result(self):
        with patch('builtins.open', side_effect=OSError(TOKEN)):
            code, out, err, _ = self.run_main([IDENTITY, checked()], summary='fixture-summary')
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)['record_date'], DAY)
        self.assertNotIn('fixture', err)


    def test_repository_secret_names_and_other_provider_env_are_not_credential_sources(self):
        out, err = io.StringIO(), io.StringIO()
        env = {'HYXWARE_ACCESS_TOKEN_1': TOKEN, 'HYXWARE_ACCESS_TOKEN_2': TOKEN,
               'ORBITIX_ACCESS_TOKEN': TOKEN, 'ACCESS_TOKEN': TOKEN}
        with patch.dict(app.os.environ, env, clear=True), patch.object(app, 'request_json') as api, \
                redirect_stdout(out), redirect_stderr(err):
            self.assertEqual(app.main(), 1)
        api.assert_not_called()
        self.assertEqual(out.getvalue(), '')
        self.assertNotIn(TOKEN, err.getvalue())

    def test_hostile_record_extras_never_reach_receipt_or_summary(self):
        private = 'fixture-private-body::error::' + TOKEN
        identity = {'success': True, 'data': {'id': 42, 'profile': private, 'access_token': TOKEN}}
        response = checked()
        response['message'] = private
        response['data']['stats']['records'][0].update({'token': TOKEN, 'profile': private, 'message': private})
        with patch('builtins.open', unittest.mock.mock_open()) as opened:
            code, out, err, _ = self.run_main([identity, response], summary='fixture-summary')
        self.assertEqual(code, 0)
        summary = ''.join(c.args[0] for c in opened().write.call_args_list)
        for rendered in (out, err, summary):
            self.assertNotIn(TOKEN, rendered)
            self.assertNotIn('fixture', rendered)
            self.assertNotIn('profile', rendered)
            self.assertNotIn('::error::', rendered)
        self.assertEqual(set(json.loads(out)),
                         {'site', 'date', 'record_date', 'result', 'quota_awarded'})

    def test_real_transport_failure_is_sanitized_by_main_and_summary(self):
        for response in (Response(raw=b'<html>fixture-private-body</html>', content_type='text/html'),
                         Response({'success': False, 'message': 'fixture-private CAPTCHA ' + TOKEN}),
                         urllib.error.URLError('fixture-private-body ' + TOKEN)):
            with self.subTest(response=type(response)), \
                    patch.dict(app.os.environ, {'HYXWARE_ACCESS_TOKEN': TOKEN, 'GITHUB_STEP_SUMMARY': 'fixture-summary'}, clear=True), \
                    patch.object(app.urllib.request, 'build_opener') as factory, \
                    patch('builtins.open', unittest.mock.mock_open()) as opened, \
                    redirect_stdout(io.StringIO()) as out, redirect_stderr(io.StringIO()) as err:
                if isinstance(response, Exception):
                    factory.return_value.open.side_effect = response
                else:
                    factory.return_value.open.return_value = response
                self.assertEqual(app.main(), 1)
                self.assertEqual(out.getvalue(), '')
                summary = ''.join(c.args[0] for c in opened().write.call_args_list)
                for rendered in (err.getvalue(), summary):
                    self.assertNotIn(TOKEN, rendered)
                    self.assertNotIn('fixture', rendered)
                    self.assertNotIn('Traceback', rendered)
                self.assertEqual(factory.return_value.open.call_count, 1)


    def run_truncated_post_response(self, final_status):
        class SocketFixture:
            def makefile(self, *args, **kwargs):
                return io.BytesIO(
                    b'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n'
                    b'Content-Length: 200\r\n\r\n{"success":tr')

        # Real HTTPResponse.read(size) returns short JSON without IncompleteRead.
        truncated = http.client.HTTPResponse(SocketFixture())
        truncated.begin()
        truncated.url = app.ORIGIN + '/api/user/checkin'
        with patch.dict(app.os.environ, {'HYXWARE_ACCESS_TOKEN': TOKEN}, clear=True), \
                patch.object(app.urllib.request, 'build_opener') as factory, \
                redirect_stdout(io.StringIO()) as out, redirect_stderr(io.StringIO()) as err:
            factory.return_value.open.side_effect = [
                Response(IDENTITY), Response(status(), url=app.ORIGIN + STATUS_PATH),
                truncated, Response(final_status, url=app.ORIGIN + STATUS_PATH)]
            code = app.main()
        methods = [c.args[0].get_method() for c in factory.return_value.open.call_args_list]
        return code, out.getvalue(), err.getvalue(), methods

    def test_truncated_json_post_is_verified_once_without_reposting(self):
        code, out, err, methods = self.run_truncated_post_response(checked())
        self.assertEqual(code, 0)
        self.assertEqual(methods, ['GET', 'GET', 'POST', 'GET'])
        receipt = json.loads(out)
        self.assertEqual(receipt['result'], 'checked_in')
        self.assertEqual(receipt['record_date'], DAY)
        self.assertEqual(receipt['quota_awarded'], 2500000)
        self.assertEqual(err, '')

    def test_truncated_json_post_without_record_fails_without_reposting(self):
        code, out, err, methods = self.run_truncated_post_response(status())
        self.assertEqual(code, 1)
        self.assertEqual(out, '')
        self.assertEqual(err, app.ERROR_MESSAGES['verification'] + '\n')
        self.assertEqual(methods, ['GET', 'GET', 'POST', 'GET'])

    def test_incomplete_post_response_is_requeried_once_without_reposting(self):
        class BrokenResponse(Response):
            def read(self, size):
                raise http.client.IncompleteRead(b'fixture-private-body', 100)

        with patch.dict(app.os.environ, {'HYXWARE_ACCESS_TOKEN': TOKEN}, clear=True), \
                patch.object(app.urllib.request, 'build_opener') as factory, \
                redirect_stdout(io.StringIO()) as out, redirect_stderr(io.StringIO()) as err:
            factory.return_value.open.side_effect = [
                Response(IDENTITY), Response(status(), url=app.ORIGIN + STATUS_PATH),
                BrokenResponse(url=app.ORIGIN + '/api/user/checkin'),
                Response(checked(), url=app.ORIGIN + STATUS_PATH)]
            self.assertEqual(app.main(), 0)
        calls = factory.return_value.open.call_args_list
        self.assertEqual([c.args[0].get_method() for c in calls], ['GET', 'GET', 'POST', 'GET'])
        self.assertEqual(json.loads(out.getvalue())['record_date'], DAY)
        self.assertEqual(err.getvalue(), '')

    def test_midnight_during_summary_never_emits_old_day_receipt(self):
        result = {'site': 'Hyxware', 'date': DAY, 'record_date': DAY,
                  'result': 'already_checked_in', 'quota_awarded': 0}
        def summary(*args, **kwargs):
            self.clock.now.return_value = TOMORROW
        with patch.object(app, 'checkin', return_value=result), \
                patch.object(app, 'write_summary', side_effect=summary):
            code, out, err, api = self.run_main([])
        self.assertEqual(code, 1)
        self.assertEqual(out, '')
        self.assertIn('date changed', err)
        api.assert_not_called()


class HyxwareWorkflowTest(unittest.TestCase):
    def setUp(self):
        self.workflow = (ROOT / '.github/workflows/hyxware.yml').read_text(encoding='utf-8')

    def test_workflow_is_isolated_and_tests_before_secret_bearing_step(self):
        workflow = self.workflow
        for setting in ("cron: '17 2 * * *'", 'workflow_dispatch:', 'runs-on: ubuntu-latest',
                        "python-version: '3.11'", 'contents: read', 'timeout-minutes: 15',
                        'cancel-in-progress: false', 'group: hyxware-checkin',
                        'HYXWARE_ACCESS_TOKEN: ${{ secrets[matrix.secret] }}'):
            self.assertIn(setting, workflow)
        self.assertIsNone(re.search(r'^\s*environment\s*:', workflow, re.MULTILINE))
        self.assertLess(workflow.index('python -B tests/test_hyxware_api.py'),
                        workflow.index('secrets[matrix.secret]'))
        self.assertIn('python -u -B hyxware_checkin.py', workflow)
        self.assertNotIn('upload-artifact', workflow)
        self.assertNotIn('pip install', workflow)

    def test_matrix_maps_exactly_fourteen_independent_accounts_to_repository_secret_names(self):
        workflow = self.workflow
        self.assertRegex(workflow, r'(?m)^    strategy:\n      fail-fast: false\n      matrix:\n        include:')
        pairs = re.findall(r'(?m)^          - account: (account[0-9]+)\n            secret: (HYXWARE_ACCESS_TOKEN_[0-9]+)$', workflow)
        self.assertEqual(pairs, [(f'account{i}', f'HYXWARE_ACCESS_TOKEN_{i}')
                                 for i in range(1, 15)])
        self.assertEqual(len(re.findall(r'(?m)^          - ', workflow)), 14)
        self.assertRegex(workflow, r'(?m)^    name: .*\$\{\{ matrix.account \}\}')
        self.assertEqual(re.findall(r'(?m)^  ([a-zA-Z_][\w-]*):\n(?=    runs-on:)', workflow), ['checkin'])
        self.assertNotIn('max-parallel: 1', workflow)
        self.assertNotIn('needs:', workflow)
        self.assertEqual(re.findall(r'HYXWARE_ACCESS_TOKEN_[0-9]+', workflow),
                         [f'HYXWARE_ACCESS_TOKEN_{i}' for i in range(1, 15)])

    def test_only_checkin_step_receives_selected_secret_and_repository_is_read_only(self):
        workflow = self.workflow
        self.assertEqual(len(re.findall(r'\bsecrets\b', workflow)), 1)
        self.assertEqual(len(re.findall(r'(?m)^\s*env:', workflow)), 1)
        self.assertRegex(workflow, r'(?m)^        env:\n          HYXWARE_ACCESS_TOKEN: \$\{\{ secrets\[matrix.secret\] \}\}\n        run: python -u -B hyxware_checkin.py$')
        self.assertEqual(re.findall(r'(?m)^permissions:\n((?:  .+\n)+)', workflow), ['  contents: read\n'])
        self.assertNotRegex(workflow, r'(?mi)^\s*(?:environment|token|GITHUB_TOKEN|id-token|write-all):')
        self.assertNotIn('pull_request', workflow)
        self.assertNotIn('continue-on-error', workflow)
        self.assertNotRegex(workflow, r'(?m)^\s*uses: .*\./')

    def test_current_actions_are_used_without_persisting_checkout_credentials(self):
        workflow = self.workflow
        self.assertEqual(re.findall(r'uses: ([^\s]+)', workflow),
                         ['actions/checkout@v5', 'actions/setup-python@v6'])
        self.assertRegex(workflow, r'checkout@v5\n        with:\n          persist-credentials: false')
        self.assertEqual(len(re.findall(r'(?m)^\s*- cron:', workflow)), 1)
        self.assertNotIn('orbitix', workflow.lower())


class HyxwareBootstrapTest(unittest.TestCase):
    def test_isolated_helper_exists(self):
        self.assertTrue((ROOT / 'hyxware_checkin.py').is_file(), 'isolated Hyxware helper is missing')


if __name__ == '__main__':
    unittest.main()
