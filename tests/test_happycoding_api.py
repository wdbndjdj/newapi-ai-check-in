import importlib.util
import io
import json
import pathlib
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from unittest.mock import patch


ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('happycoding_checkin', ROOT / 'happycoding_checkin.py')
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)
DAY = '2026-10-16'
NOW = datetime(2026, 10, 16, 12, tzinfo=timezone(timedelta(hours=8)))


def status(checked=False, records=None):
    return {'success': True, 'data': {'stats': {
        'checked_in_today': checked, 'records': [] if records is None else records}}}


def dated_status(checked=True, day=DAY):
    return status(checked, [{'checkin_date': day, 'quota_awarded': 2500000}])


class FakeAPI:
    """In-memory API boundary: never opens the network or uses real credentials."""

    def __init__(self, identities=None, failures=(), prechecked=(), bad_records=(), null_award=False):
        self.identities = identities or {'fixture-primary': 3370, 'fixture-secondary': 2806}
        self.failures = set(failures)
        self.checked = set(prechecked)
        self.bad_records = set(bad_records)
        self.null_award = null_award
        self.calls = []

    def __call__(self, path, token, *, method='GET', user_id=None):
        self.calls.append((path, token, method, user_id))
        uid = self.identities[token]
        if path == '/api/user/self':
            if uid in self.failures:
                raise RuntimeError('fixture-private-server-body')
            return {'success': True, 'data': {'id': uid, 'profile': 'fixture-private-profile'}}
        if user_id != uid:
            raise AssertionError('wrong account header')
        if method == 'POST':
            self.checked.add(uid)
            return {'success': True, 'data': None if self.null_award else {'quota_awarded': 2500000}}
        if uid in self.checked:
            return dated_status(day='2026-10-15' if uid in self.bad_records else DAY)
        return status()

    def posts(self):
        return [call[3] for call in self.calls if call[2] == 'POST']

    def self_count(self, uid):
        return sum(path == '/api/user/self' and self.identities[token] == uid
                   for path, token, _, _ in self.calls)


class HappyCodingTest(unittest.TestCase):
    def setUp(self):
        self.clock = patch.object(app, 'datetime')
        self.datetime = self.clock.start()
        self.datetime.now.return_value = NOW
        self.addCleanup(self.clock.stop)
        # Any accidental unmocked network access is a test failure.
        self.network = patch.object(app.urllib.request, 'urlopen', side_effect=AssertionError('network forbidden'))
        self.network.start()
        self.addCleanup(self.network.stop)

    def run_main(self, secret, api, actions=False):
        out, err = io.StringIO(), io.StringIO()
        with patch.dict(app.os.environ, {'HAPPYCODING_ACCESS_TOKEN': secret,
                                       'GITHUB_ACTIONS': 'true' if actions else 'false'}, clear=True), \
                patch.object(app, 'request_json', side_effect=api), \
                patch.object(app.time, 'sleep') as sleep, redirect_stdout(out), redirect_stderr(err):
            code = app.main()
        return code, out.getvalue(), err.getvalue(), sleep.call_count

    @staticmethod
    def accounts(*ids):
        return json.dumps([{'user_id': uid, 'access_token': 'fixture-primary' if uid == 3370
                            else 'fixture-secondary'} for uid in ids])

    def test_workflow_budget_allows_both_accounts_to_exhaust_retries(self):
        import re
        workflow = (ROOT / '.github/workflows/happycoding.yml').read_text(encoding='utf-8')
        minutes = int(re.search(r'timeout-minutes: (\d+)', workflow).group(1))
        self.assertEqual(minutes, 15)
        self.assertGreater(minutes * 60, 2 * (3 * 4 * 30 + 10 + 20))

    def test_already_checked_in_does_not_post(self):
        api = FakeAPI(prechecked=(2806,))
        with patch.object(app, 'request_json', side_effect=api):
            result = app.checkin('fixture-secondary')
        self.assertEqual(result['result'], 'already_checked_in')
        self.assertEqual(result['user_id'], 2806)
        self.assertEqual(result['record_date'], DAY)
        self.assertEqual(api.posts(), [])

    def test_new_checkin_is_verified(self):
        api = FakeAPI()
        with patch.object(app, 'request_json', side_effect=api):
            result = app.checkin('fixture-secondary')
        self.assertEqual(result['result'], 'checked_in')
        self.assertEqual(result['quota_awarded'], 2500000)
        self.assertEqual(result['record_date'], DAY)
        self.assertEqual(api.posts(), [2806])

    def test_failed_verification_is_not_reported_success(self):
        replies = [{'success': True, 'data': {'id': 2806}}, status(),
                   {'success': True}, status()]
        with patch.object(app, 'request_json', side_effect=replies):
            with self.assertRaises(RuntimeError):
                app.checkin('fixture-secondary')

    def test_invalid_status_is_not_treated_as_unchecked(self):
        with patch.object(app, 'request_json', side_effect=[
                {'success': True, 'data': {'id': 2806}}, {'success': False}]) as api:
            with self.assertRaises(RuntimeError):
                app.checkin('fixture-secondary')
        self.assertEqual(api.call_count, 2)

    def test_missing_token_fails_before_network(self):
        with patch.object(app, 'request_json') as api:
            with self.assertRaises(ValueError):
                app.checkin('')
        api.assert_not_called()

    def test_json_primary_and_2806_both_run_and_emit_one_safe_receipt(self):
        api = FakeAPI()
        code, out, err, _ = self.run_main(self.accounts(3370, 2806), api)
        self.assertEqual(code, 0)
        self.assertEqual(api.posts(), [3370, 2806])
        self.assertEqual(len(out.splitlines()), 1)
        receipt = json.loads(out)
        self.assertEqual((receipt['site'], receipt['date'], receipt['result']), ('HappyCoding', DAY, 'checked_in'))
        self.assertEqual([a['user_id'] for a in receipt['accounts']], [3370, 2806])
        for account in receipt['accounts']:
            self.assertEqual(set(account), {'user_id', 'date', 'record_date', 'result', 'quota_awarded'})
            self.assertEqual(account['date'], DAY)
            self.assertEqual(account['record_date'], DAY)
            self.assertEqual(account['quota_awarded'], 2500000)
        self.assertNotIn('quota_awarded', receipt)
        self.assertEqual(err, '')
        self.assertNotIn('fixture-', out)
        self.assertNotIn('profile', out)

    def test_actions_masks_each_token_before_network_and_escapes_commands(self):
        tokens = ('fixture-primary%value', 'fixture-secondary\r\nvalue')
        secret = json.dumps([{'user_id': uid, 'access_token': token}
                             for uid, token in zip((3370, 2806), tokens)])
        api = FakeAPI(identities=dict(zip(tokens, (3370, 2806))))
        out = io.StringIO()
        masks = '::add-mask::fixture-primary%25value\n::add-mask::fixture-secondary%0D%0Avalue\n'
        def masked_api(path, token, **kwargs):
            self.assertEqual(out.getvalue(), masks)
            return api(path, token, **kwargs)
        with patch.dict(app.os.environ, {'HAPPYCODING_ACCESS_TOKEN': secret, 'GITHUB_ACTIONS': 'true'}, clear=True), \
                patch.object(app, 'request_json', side_effect=masked_api), redirect_stdout(out):
            code = app.main()
        self.assertEqual(code, 0)
        lines = out.getvalue().splitlines()
        self.assertEqual(len(lines), 3)
        self.assertEqual(len(json.loads(lines[-1])['accounts']), 2)

    def test_actions_invalid_json_has_no_mask_or_network(self):
        api = FakeAPI()
        code, out, err, _ = self.run_main('[{"access_token":"fixture-private-token"}]', api, actions=True)
        self.assertEqual(code, 1)
        self.assertEqual(api.calls, [])
        self.assertEqual(out, '')
        self.assertNotIn('fixture-', err)

    def test_plain_token_preserves_single_account_and_top_level_quota(self):
        code, out, err, _ = self.run_main('  fixture-primary  ', FakeAPI())
        self.assertEqual(code, 0)
        receipt = json.loads(out)
        self.assertEqual(receipt['quota_awarded'], 2500000)
        self.assertEqual(receipt['accounts'][0]['user_id'], 3370)
        self.assertEqual(receipt['accounts'][0]['record_date'], DAY)
        self.assertEqual(err, '')

    def test_expected_identity_mismatch_fails_without_post(self):
        api = FakeAPI(identities={'fixture-secondary': 3370})
        code, out, _, _ = self.run_main(self.accounts(2806), api)
        self.assertEqual(code, 1)
        self.assertEqual(api.posts(), [])
        self.assertEqual(out, '')

    def test_all_failures_exit_one_after_three_attempts_each(self):
        api = FakeAPI(failures=(3370, 2806))
        code, out, _, sleeps = self.run_main(self.accounts(3370, 2806), api)
        self.assertEqual(code, 1)
        self.assertEqual(out, '')
        self.assertEqual(api.self_count(3370), 3)
        self.assertEqual(api.self_count(2806), 3)
        self.assertEqual(sleeps, 4)

    def test_partial_success_never_masks_failure_or_emits_receipt(self):
        for failure in (3370, 2806):
            with self.subTest(failure=failure):
                api = FakeAPI(failures=(failure,))
                code, out, _, _ = self.run_main(self.accounts(3370, 2806), api)
                self.assertEqual(code, 1)
                self.assertEqual(out, '')
                self.assertEqual(api.self_count(failure), 3)
                self.assertEqual(api.posts(), [2806 if failure == 3370 else 3370])

    def test_each_account_retries_independently_then_succeeds(self):
        api = FakeAPI()
        attempts = {}
        def flaky(path, token, **kwargs):
            if path == '/api/user/self':
                attempts[token] = attempts.get(token, 0) + 1
                if attempts[token] < 3:
                    raise RuntimeError('fixture-private-error')
            return api(path, token, **kwargs)
        code, out, _, sleeps = self.run_main(self.accounts(3370, 2806), flaky)
        self.assertEqual(code, 0)
        self.assertEqual(attempts, {'fixture-primary': 3, 'fixture-secondary': 3})
        self.assertEqual(api.posts(), [3370, 2806])
        self.assertEqual(len(json.loads(out)['accounts']), 2)
        self.assertEqual(sleeps, 4)

    def test_duplicate_expected_ids_rejected_before_any_post(self):
        api = FakeAPI()
        code, out, _, _ = self.run_main(self.accounts(3370, 3370), api)
        self.assertEqual(code, 1)
        self.assertEqual(out, '')
        self.assertEqual(api.calls, [])

    def test_two_expected_ids_cannot_share_actual_identity(self):
        api = FakeAPI(identities={'fixture-primary': 3370, 'fixture-secondary': 3370})
        code, out, _, _ = self.run_main(self.accounts(3370, 2806), api)
        self.assertEqual(code, 1)
        self.assertEqual(out, '')
        self.assertEqual(api.posts(), [3370])

    def test_invalid_json_configuration_does_not_fall_back_to_plain(self):
        malformed = ('[]', 'null', 'true', '123', '{}', '"fixture-private-value"',
                     '[', '{broken-fixture-private-json', '[null]',
                     '[{"access_token":"fixture-secondary"}]',
                     '[{"user_id":2806}]', '[{"user_id":2806,"access_token":null}]',
                     '[{"user_id":2806,"access_token":"  "}]',
                     '[{"user_id":true,"access_token":"fixture-secondary"}]',
                     '[{"user_id":"2806","access_token":"fixture-secondary"}]',
                     '[{"user_id":0,"access_token":"fixture-secondary"}]',
                     '[{"user_id":2806,"access_token":42}]')
        for secret in malformed:
            with self.subTest(secret=secret):
                api = FakeAPI()
                code, out, err, _ = self.run_main(secret, api)
                self.assertEqual(code, 1)
                self.assertEqual(api.calls, [])
                self.assertEqual(out, '')
                self.assertNotIn('fixture-', err)

    def test_missing_or_wrong_date_record_cannot_claim_success(self):
        for verification in (status(True), dated_status(day='2026-10-15'),
                             dated_status(day='2026-10-17')):
            with self.subTest(verification=verification):
                replies = [{'success': True, 'data': {'id': 2806}}, status(),
                           {'success': True, 'data': {'quota_awarded': 5}}, verification]
                with patch.object(app, 'request_json', side_effect=replies):
                    with self.assertRaises(RuntimeError):
                        app.checkin('fixture-secondary')

    def test_inconsistent_checked_flag_does_not_cause_duplicate_post(self):
        api = FakeAPI()
        def records_only(path, token, **kwargs):
            return api(path, token, **kwargs) if path == '/api/user/self' else dated_status(False)
        with patch.object(app, 'request_json', side_effect=records_only) as requests:
            result = app.checkin('fixture-secondary')
        self.assertEqual(result['result'], 'already_checked_in')
        self.assertFalse(any(c.kwargs.get('method') == 'POST' for c in requests.call_args_list))

    def test_null_or_invalid_self_and_stats_fail_cleanly_without_post(self):
        invalid = [None, [], {'success': True, 'data': None},
                   {'success': True, 'data': []}, {'success': True, 'data': {'id': True}},
                   {'success': True, 'data': {'id': 0}}]
        for user in invalid:
            with self.subTest(user=user), patch.object(app, 'request_json', return_value=user) as api:
                with self.assertRaises(RuntimeError):
                    app.checkin('fixture-secondary')
                self.assertEqual(api.call_count, 1)
        bad_statuses = [None, [], {'success': True, 'data': None},
                        {'success': True, 'data': {'stats': None}},
                        {'success': True, 'data': {'stats': []}},
                        {'success': True, 'data': {'stats': {'checked_in_today': True, 'records': None}}},
                        status(True, [None]), status(True, [{'checkin_date': None}])]
        for bad in bad_statuses:
            with self.subTest(status=bad), patch.object(app, 'request_json', side_effect=[
                    {'success': True, 'data': {'id': 2806}}, bad]) as api:
                with self.assertRaises(RuntimeError):
                    app.checkin('fixture-secondary')
                self.assertEqual(api.call_count, 2)

    def test_null_post_data_does_not_crash_after_verified_record(self):
        code, out, _, _ = self.run_main('fixture-primary', FakeAPI(null_award=True))
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)['accounts'][0]['record_date'], DAY)

    def test_sensitive_exception_details_and_profiles_never_reach_logs(self):
        api = FakeAPI(failures=(3370, 2806))
        code, out, err, _ = self.run_main(self.accounts(3370, 2806), api)
        self.assertEqual(code, 1)
        self.assertEqual(out, '')
        self.assertNotIn('fixture-', out + err)
        self.assertNotIn('access_token', out + err)
        self.assertNotIn('profile', out + err)

    def test_sensitive_malformed_header_is_safely_reported(self):
        code, out, err, _ = self.run_main('fixture-private\nheader',
                                         ValueError('fixture-private-header-error'))
        self.assertEqual(code, 1)
        self.assertEqual(out, '')
        self.assertNotIn('fixture-', err)

    def test_midnight_during_status_or_post_verification_fails(self):
        tomorrow = NOW + timedelta(days=1)
        for prechecked in (False, True):
            api = FakeAPI(prechecked=(3370,) if prechecked else ())
            def cross(path, token, **kwargs):
                result = api(path, token, **kwargs)
                if path.startswith('/api/user/checkin?'):
                    self.datetime.now.return_value = tomorrow
                return result
            self.datetime.now.return_value = NOW
            with self.subTest(prechecked=prechecked), patch.object(app, 'request_json', side_effect=cross):
                with self.assertRaises(RuntimeError):
                    app.checkin('fixture-primary')
            self.assertEqual(api.posts(), [])
        api = FakeAPI()
        def cross_post(path, token, **kwargs):
            result = api(path, token, **kwargs)
            if kwargs.get('method') == 'POST':
                self.datetime.now.return_value = tomorrow
            return result
        self.datetime.now.return_value = NOW
        with patch.object(app, 'request_json', side_effect=cross_post):
            with self.assertRaises(RuntimeError):
                app.checkin('fixture-primary')

    def test_midnight_is_retried_on_new_day(self):
        api = FakeAPI()
        attempts = 0
        tomorrow = NOW + timedelta(days=1)
        def cross_once(path, token, **kwargs):
            nonlocal attempts
            if path == '/api/user/self':
                attempts += 1
                if attempts > 1:
                    self.datetime.now.return_value = tomorrow
            result = api(path, token, **kwargs)
            if path.startswith('/api/user/checkin?'):
                if attempts == 1:
                    self.datetime.now.return_value = tomorrow
                elif 3370 in api.checked:
                    return dated_status(day='2026-10-17')
            return result
        code, out, _, sleeps = self.run_main('fixture-primary', cross_once)
        self.assertEqual(code, 0)
        self.assertEqual(sleeps, 1)
        self.assertEqual(json.loads(out)['date'], '2026-10-17')
        self.assertEqual(json.loads(out)['accounts'][0]['record_date'], '2026-10-17')
        self.assertEqual(api.posts(), [3370])

    def test_midnight_after_first_account_never_emits_stale_summary(self):
        api = FakeAPI()
        def cross_between_accounts(path, token, **kwargs):
            if path == '/api/user/self' and token == 'fixture-secondary':
                self.datetime.now.return_value = NOW + timedelta(days=1)
            result = api(path, token, **kwargs)
            if token == 'fixture-secondary' and token and path.startswith('/api/user/checkin?') and 2806 in api.checked:
                return dated_status(day='2026-10-17')
            return result
        code, out, _, _ = self.run_main(self.accounts(3370, 2806), cross_between_accounts)
        self.assertEqual(code, 1)
        self.assertEqual(out, '')


if __name__ == '__main__':
    unittest.main()
