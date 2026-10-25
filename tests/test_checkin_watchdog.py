"""Offline watchdog tests: fake GitHub API/HTTP only, never site traffic."""
import importlib.util
import io
import json
import pathlib
import unittest
import urllib.error
import zipfile
from datetime import datetime, timezone
from email.utils import format_datetime

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('checkin_watchdog', ROOT / 'scripts/checkin_watchdog.py')
app = None
if pathlib.Path(spec.origin).exists():
    app = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(app)

UTC = timezone.utc
DATE = datetime(2026, 10, 12, 2, 30, tzinfo=UTC)
SHA = 'a' * 40
HYXWARE_NOW = datetime(2026, 10, 12, 3, 30, tzinfo=UTC)
HYXWARE_STEP = "5_Check in and verify today's exact-date record.txt"


def archive(receipt=None, source='checkin/3_签到并确认今日状态.txt', text=None):
    content = text if text is not None else json.dumps(receipt or {'message': 'no receipt'})
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w') as output:
        output.writestr(source, '2026-10-12T02:30:00.1234567Z ' + content + '\n')
    return stream.getvalue()


def receipt(site='HappyCoding', date='2026-10-12', result='checked_in'):
    return {'site': site, 'date': date, 'result': result}


def hyxware_members():
    # The receipt has no account field: identity comes from the matrix job name.
    value = {**receipt('Hyxware'), 'record_date': '2026-10-12', 'quota_awarded': 1000}
    return {f'Hyxware account{i}/{HYXWARE_STEP}': json.dumps(value) for i in range(1, 15)}


def member_archive(members):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w') as output:
        for source, content in members.items():
            output.writestr(source, '2026-10-12T02:30:00.1234567Z ' + content + '\n')
    return stream.getvalue()


def run(run_id=1, **changes):
    value = dict(id=run_id, workflow_id=373080586, status='completed', conclusion='success',
                 event='schedule', actor={'login': 'github-actions[bot]'}, head_branch='main',
                 head_sha=SHA, created_at='2026-10-12T01:57:00Z',
                 updated_at='2026-10-12T01:58:00Z', run_attempt=1)
    value.update(changes)
    return value


class Clock:
    def __init__(self):
        self.value = 0
    def now(self):
        return self.value
    def sleep(self, seconds):
        self.value += seconds


class FakeAPI:
    def __init__(self, runs=None, logs=None, server_time=DATE):
        self.server_time = server_time
        self.runs = list(runs or [])
        self.log_data = logs or {}
        self.posts = []
        self.calls = []
        self.dispatched = False
        self.new_runs = []
        self.error = None
        self.refresh_runs = None
    def workflow(self, workflow_id):
        self.calls.append(('workflow', workflow_id))
        name = {373080586: 'happycoding', 373126098: 'mofas', 395129293: 'hyxware'}[workflow_id]
        return {'id': workflow_id, 'path': '.github/workflows/' + name + '.yml', 'state': 'active'}
    def list_runs(self, workflow_id, *, since=None, status=None):
        self.calls.append(('list', workflow_id, since, status))
        if self.error:
            raise self.error
        items = self.runs + (self.new_runs if self.dispatched else [])
        if self.refresh_runs is not None and sum(c[0] == 'list' and c[2] is not None for c in self.calls) >= 2:
            items = self.refresh_runs
        return [r for r in items if r['workflow_id'] == workflow_id and
                (status is None or r['status'] == status) and
                (since is None or r['created_at'] >= since)]
    def logs(self, run_id):
        value = self.log_data.get(run_id, archive())
        if isinstance(value, Exception):
            raise value
        return value
    def main_sha(self):
        return SHA
    def dispatch(self, workflow_id, ref='main'):
        self.posts.append((workflow_id, ref))
        self.dispatched = True
    def get_run(self, run_id):
        return next(r for r in self.new_runs + self.runs if r['id'] == run_id)


class WatchdogTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(app, 'watchdog implementation is missing (expected RED)')
        self.clock = Clock()
    def check(self, api, site='HappyCoding', wait=0):
        return app.Watchdog(api, now=self.clock.now, sleep=self.clock.sleep, wait_seconds=wait).check_site(site)
    def test_today_receipt_skips_even_when_workflow_failed(self):
        for value in ('checked_in', 'already_checked_in'):
            api = FakeAPI([run(conclusion='failure')], {1: archive(receipt(result=value))})
            self.assertEqual(self.check(api)['status'], 'healthy')
            self.assertEqual(api.posts, [])
    def test_active_older_run_skips(self):
        for status in ('queued', 'in_progress', 'waiting', 'pending', 'requested'):
            api = FakeAPI([run(status=status, conclusion=None, created_at='2026-10-01T01:00:00Z')])
            self.assertEqual(self.check(api)['status'], 'active')
            self.assertEqual(api.posts, [])
    def test_due_missing_dispatches_only_main(self):
        api = FakeAPI()
        self.assertEqual(self.check(api)['status'], 'pending')
        self.assertEqual(api.posts, [(373080586, 'main')])
    def test_grace_boundaries_for_both_sites(self):
        for site, hour, minute, workflow in [('HappyCoding', 2, 12, 373080586), ('Mofas', 2, 22, 373126098)]:
            before = FakeAPI(server_time=datetime(2026, 10, 12, hour, minute - 1, 59, tzinfo=UTC))
            self.assertEqual(self.check(before, site)['status'], 'not_due')
            self.assertEqual(before.posts, [])
            due = FakeAPI(server_time=datetime(2026, 10, 12, hour, minute, tzinfo=UTC))
            self.assertEqual(self.check(due, site)['status'], 'pending')
            self.assertEqual(due.posts, [(workflow, 'main')])
    def test_wrong_date_and_wrong_site_are_not_today_receipts(self):
        for value in (receipt(date='2026-10-11'), receipt(site='Mofas')):
            api = FakeAPI([run()], {1: archive(value)})
            self.assertEqual(self.check(api)['status'], 'pending')
            self.assertEqual(len(api.posts), 1)
    def test_test_log_receipt_does_not_count(self):
        api = FakeAPI([run()], {1: archive(receipt(), source='checkin/2_验证签到逻辑.txt')})
        self.assertEqual(self.check(api)['status'], 'unknown')
        self.assertEqual(api.posts, [])
    def test_echoed_json_in_actual_step_does_not_count(self):
        api = FakeAPI([run()], {1: archive(text='echo ' + json.dumps(receipt()))})
        self.assertEqual(self.check(api)['status'], 'pending')
    def test_whole_job_log_only_accepts_actual_checkin_output(self):
        text = '\n'.join(['##[group]Run python tests/test_happycoding_api.py', '##[endgroup]',
                          json.dumps(receipt()), '##[group]Run python -u happycoding_checkin.py',
                          'python -u happycoding_checkin.py', '##[endgroup]', json.dumps(receipt(date='2026-10-11'))])
        api = FakeAPI([run()], {1: archive(source='0_checkin.txt', text=text)})
        self.assertEqual(self.check(api)['status'], 'pending')
        api = FakeAPI([run()], {1: archive(source='0_checkin.txt', text=text + '\n' + json.dumps(receipt()))})
        self.assertEqual(self.check(api)['status'], 'healthy')
    def test_incomplete_or_failed_logs_fail_closed(self):
        for value in (b'', b'PK\x03\x04truncated', archive(source='tests.txt'), OSError('SECRET must not appear')):
            api = FakeAPI([run()], {1: value})
            result = self.check(api)
            self.assertEqual(result['status'], 'unknown')
            self.assertNotIn('SECRET', json.dumps(result))
            self.assertEqual(api.posts, [])
    def test_empty_checkin_step_is_unknown_not_missing(self):
        api = FakeAPI([run()], {1: archive(text='')})
        self.assertEqual(self.check(api)['status'], 'unknown')
        self.assertEqual(api.posts, [])
    def test_malformed_site_receipt_is_unknown_not_missing(self):
        for value in (receipt(result='success'), {'site': 'HappyCoding', 'date': '2026-10-12'},
                      {'site': 'HappyCoding', 'result': 'checked_in'}):
            api = FakeAPI([run()], {1: archive(value)})
            self.assertEqual(self.check(api)['status'], 'unknown')
            self.assertEqual(api.posts, [])
    def test_uncertain_dispatch_is_not_retried(self):
        api = FakeAPI()
        def uncertain(workflow_id, ref='main'):
            api.posts.append((workflow_id, ref))
            raise TimeoutError('SECRET')
        api.dispatch = uncertain
        result = self.check(api)
        self.assertEqual(result['status'], 'unknown')
        self.assertTrue(result['dispatch_requested'])
        self.assertEqual(len(api.posts), 1)
        self.assertNotIn('SECRET', json.dumps(result))
    def test_unknown_status_fails_closed(self):
        api = FakeAPI([run(status='future_status')])
        self.assertEqual(self.check(api)['status'], 'unknown')
        self.assertEqual(api.posts, [])
    def test_probe_error_is_isolated_per_site(self):
        api = FakeAPI([run()], {1: OSError('private')})
        summary = app.Watchdog(api, now=self.clock.now, sleep=self.clock.sleep, wait_seconds=0).run()
        self.assertEqual([s['status'] for s in summary['sites']], ['unknown', 'pending', 'not_due'])
        self.assertEqual(api.posts, [(373126098, 'main')])
    def test_failure_cooldown_exact_boundary(self):
        for updated, expected in [('2026-10-12T01:30:01Z', 'cooldown'), ('2026-10-12T01:30:00Z', 'pending')]:
            api = FakeAPI([run(conclusion='failure', updated_at=updated)])
            self.assertEqual(self.check(api)['status'], expected)
    def test_dispatch_limit_counts_only_bot_dispatches_on_beijing_day(self):
        cases = [(run(1, event='workflow_dispatch'), run(2, event='workflow_dispatch'), 'limit'),
                 (run(1, event='workflow_dispatch', actor={'login': 'human'}), run(2, event='schedule'), 'pending'),
                 (run(1, event='workflow_dispatch', created_at='2026-10-11T15:59:59Z'),
                  run(2, event='workflow_dispatch', created_at='2026-10-11T16:00:00Z'), 'pending')]
        for first, second, want in cases:
            api = FakeAPI([first, second])
            self.assertEqual(self.check(api)['status'], want)
    def test_rerun_id_is_one_dispatch_not_two(self):
        api = FakeAPI([run(event='workflow_dispatch', run_attempt=3)])
        self.assertEqual(self.check(api)['status'], 'pending')
    def test_whitelist_rejects_other_site_before_api(self):
        api = FakeAPI()
        self.assertEqual(self.check(api, 'NOFX')['status'], 'unknown')
        self.assertEqual(api.calls, [])
        self.assertEqual(api.posts, [])
    def test_wrong_workflow_identity_blocks_dispatch(self):
        api = FakeAPI()
        api.workflow = lambda _: {'id': 373080586, 'path': '.github/workflows/nofx.yml', 'state': 'active'}
        self.assertEqual(self.check(api)['status'], 'unknown')
        self.assertEqual(api.posts, [])
    def test_refresh_catches_concurrent_active_run(self):
        api = FakeAPI()
        api.refresh_runs = [run(status='queued', conclusion=None)]
        self.assertEqual(self.check(api)['status'], 'active')
        self.assertEqual(api.posts, [])
    def test_wait_binds_server_date_and_prior_ids_not_local_wall_clock(self):
        api = FakeAPI([run(event='workflow_dispatch')])
        api.new_runs = [run(2, event='workflow_dispatch', created_at='2026-10-12T02:30:00Z')]
        api.log_data[2] = archive(receipt())
        result = self.check(api, wait=240)
        self.assertEqual(result['status'], 'verified')
        self.assertEqual(result['run_id'], 2)
    def test_binding_requires_main_bot_event_workflow_sha_and_server_time(self):
        for changes in ({'head_branch': 'other'}, {'actor': {'login': 'human'}}, {'event': 'schedule'},
                        {'head_sha': 'b' * 40}, {'created_at': '2026-10-12T02:29:33Z'}):
            api = FakeAPI()
            api.new_runs = [run(2, event='workflow_dispatch', **{k:v for k,v in changes.items() if k != 'event'})]
            if 'event' in changes:
                api.new_runs[0]['event'] = changes['event']
            result = self.check(api, wait=240)
            self.assertEqual(result['status'], 'pending')
            self.assertLessEqual(self.clock.value, 240 * (1 + len(api.posts)))
            self.clock.value = 0
    def test_ambiguous_new_runs_do_not_claim_success(self):
        api = FakeAPI()
        api.new_runs = [run(i, event='workflow_dispatch', created_at='2026-10-12T02:30:00Z') for i in (2,3)]
        self.assertEqual(self.check(api, wait=240)['status'], 'unknown')
    def test_pending_at_240_seconds_is_not_failure(self):
        api = FakeAPI()
        api.new_runs = [run(2, event='workflow_dispatch', created_at='2026-10-12T02:30:00Z', status='queued', conclusion=None)]
        result = self.check(api, wait=240)
        self.assertEqual(result['status'], 'pending')
        self.assertEqual(self.clock.value, 240)
        self.assertEqual(len(api.posts), 1)
    def test_completed_without_receipt_is_failed_not_verified(self):
        api = FakeAPI()
        api.new_runs = [run(2, event='workflow_dispatch', created_at='2026-10-12T02:30:00Z')]
        self.assertEqual(self.check(api, wait=240)['status'], 'failed')
    def test_missing_server_date_does_not_use_local_time(self):
        api = FakeAPI(server_time=None)
        self.assertEqual(self.check(api)['status'], 'unknown')
        self.assertEqual(api.posts, [])


class HyxwareWatchdogTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()

    def check(self, api, wait=0):
        return app.Watchdog(api, now=self.clock.now, sleep=self.clock.sleep,
                            wait_seconds=wait).check_site('Hyxware')

    def api(self, members=None, **changes):
        return FakeAPI([run(workflow_id=395129293, **changes)],
                       {1: member_archive(hyxware_members() if members is None else members)},
                       server_time=HYXWARE_NOW)

    def test_hyxware_due_only_at_1032_beijing(self):
        before = FakeAPI(server_time=datetime(2026, 10, 12, 2, 31, 59, tzinfo=UTC))
        self.assertEqual(self.check(before)['status'], 'not_due')
        self.assertEqual(before.posts, [])
        due = FakeAPI(server_time=datetime(2026, 10, 12, 2, 32, tzinfo=UTC))
        result = self.check(due)
        self.assertEqual(result['status'], 'pending')
        self.assertEqual(result['eligible_at'], '2026-10-12T10:32:00+08:00')
        self.assertEqual(due.posts, [(395129293, 'main')])

    def test_all_14_today_receipts_are_healthy_even_if_run_failed(self):
        for state in ('checked_in', 'already_checked_in'):
            with self.subTest(state=state):
                members = {name: text.replace('"checked_in"', json.dumps(state))
                           for name, text in hyxware_members().items()}
                api = self.api(members, conclusion='failure')
                self.assertEqual(self.check(api)['status'], 'healthy')
                self.assertEqual(api.posts, [])

    def test_one_or_13_receipts_cannot_cover_14_accounts(self):
        for count in (1, 13):
            with self.subTest(count=count):
                members = dict(list(hyxware_members().items())[:count])
                api = self.api(members)
                self.assertEqual(self.check(api)['status'], 'pending')
                self.assertEqual(api.posts, [(395129293, 'main')])

    def test_repeated_receipts_for_one_account_do_not_count_as_14(self):
        name, text = next(iter(hyxware_members().items()))
        api = self.api({name: '\n'.join([text] * 14)})
        self.assertEqual(self.check(api)['status'], 'pending')
        self.assertEqual(api.posts, [(395129293, 'main')])

    def test_full_job_and_step_duplicate_cannot_replace_missing_account(self):
        members = hyxware_members()
        members.pop(f'Hyxware account14/{HYXWARE_STEP}')
        text = members[f'Hyxware account1/{HYXWARE_STEP}']
        members['0_Hyxware account1.txt'] = '\n'.join([
            '##[group]Run python -u -B hyxware_checkin.py', '##[endgroup]', text])
        api = self.api(members)
        self.assertEqual(self.check(api)['status'], 'pending')
        self.assertEqual(api.posts, [(395129293, 'main')])

    def test_wrong_date_or_site_receipt_cannot_complete_matrix(self):
        for changes in ({'date': '2026-10-11', 'record_date': '2026-10-11'},
                        {'record_date': '2026-10-11'}, {'date': '2026-10-11'},
                        {'site': 'HappyCoding'}):
            with self.subTest(changes=changes):
                members = hyxware_members()
                name = f'Hyxware account14/{HYXWARE_STEP}'
                members[name] = json.dumps({**json.loads(members[name]), **changes})
                api = self.api(members)
                self.assertEqual(self.check(api)['status'], 'pending')
                self.assertEqual(api.posts, [(395129293, 'main')])

    def test_wrong_account_log_cannot_complete_matrix(self):
        for account in ('account15', 'account014', 'account1', 'other'):
            with self.subTest(account=account):
                members = hyxware_members()
                text = members.pop(f'Hyxware account14/{HYXWARE_STEP}')
                members[f'Hyxware {account}/6_Check in and verify today\'s exact-date record.txt'] = text
                api = self.api(members)
                self.assertEqual(self.check(api)['status'], 'pending')
                self.assertEqual(api.posts, [(395129293, 'main')])

    def test_test_step_or_echoed_json_cannot_complete_matrix(self):
        for source, prefix in [('4_Test isolated Hyxware check-in offline.txt', ''),
                               (HYXWARE_STEP, 'echo ')]:
            with self.subTest(source=source, prefix=prefix):
                members = hyxware_members()
                text = members.pop(f'Hyxware account14/{HYXWARE_STEP}')
                members[f'Hyxware account14/{source}'] = prefix + text
                api = self.api(members)
                self.assertEqual(self.check(api)['status'], 'pending')
                self.assertEqual(api.posts, [(395129293, 'main')])

    def test_malformed_hyxware_receipt_fails_closed(self):
        for changes in ({'record_date': None}, {'record_date': '2026-02-30'},
                        {'quota_awarded': None}, {'quota_awarded': -1},
                        {'quota_awarded': True}, {'quota_awarded': float('nan')},
                        {'quota_awarded': float('inf')}, {'result': 'success'}):
            with self.subTest(changes=changes):
                members = hyxware_members()
                name = f'Hyxware account14/{HYXWARE_STEP}'
                members[name] = json.dumps({**json.loads(members[name]), **changes})
                api = self.api(members)
                self.assertEqual(self.check(api)['status'], 'unknown')
                self.assertEqual(api.posts, [])

    def test_whole_jobs_accept_only_hyxware_command_output(self):
        members = {}
        for index, text in enumerate(hyxware_members().values(), 1):
            members[f'{index}_Hyxware account{index}.txt'] = '\n'.join([
                '##[group]Run python -B tests/test_hyxware_api.py', '##[endgroup]', text,
                '##[group]Run python -u -B hyxware_checkin.py',
                'python -u -B hyxware_checkin.py', '##[endgroup]',
                text if index != 14 else text.replace('2026-10-12', '2026-10-11'),
                '##[group]Post job cleanup', '##[endgroup]', text])
        api = self.api(members)
        self.assertEqual(self.check(api)['status'], 'pending')
        members['14_Hyxware account14.txt'] = members['14_Hyxware account14.txt'].replace(
            '2026-10-11', '2026-10-12')
        api = self.api(members)
        self.assertEqual(self.check(api)['status'], 'healthy')
        self.assertEqual(api.posts, [])

    def test_receipts_are_not_pooled_across_different_runs(self):
        members = list(hyxware_members().items())
        api = FakeAPI([run(i, workflow_id=395129293) for i in (1, 2)],
                      {1: member_archive(dict(members[:7])), 2: member_archive(dict(members[7:]))},
                      server_time=HYXWARE_NOW)
        self.assertEqual(self.check(api)['status'], 'pending')
        self.assertEqual(api.posts, [(395129293, 'main')])

    def test_hyxware_unavailable_logs_fail_closed(self):
        for data in (b'', b'PK\x03\x04truncated', archive(source='tests.txt'), OSError('SECRET')):
            with self.subTest(data=type(data)):
                api = self.api()
                api.log_data[1] = data
                result = self.check(api)
                self.assertEqual(result['status'], 'unknown')
                self.assertNotIn('SECRET', json.dumps(result))
                self.assertEqual(api.posts, [])

    def test_empty_account_step_fails_closed_even_with_13_valid_receipts(self):
        members = hyxware_members()
        members[f'Hyxware account14/{HYXWARE_STEP}'] = ''
        api = self.api(members)
        self.assertEqual(self.check(api)['status'], 'unknown')
        self.assertEqual(api.posts, [])

    def test_dispatched_run_verification_also_requires_all_14_receipts(self):
        for count, expected in ((13, 'failed'), (14, 'verified')):
            with self.subTest(count=count):
                api = FakeAPI(server_time=HYXWARE_NOW)
                api.new_runs = [run(2, workflow_id=395129293, event='workflow_dispatch',
                                    created_at='2026-10-12T03:30:00Z')]
                api.log_data[2] = member_archive(dict(list(hyxware_members().items())[:count]))
                result = self.check(api, wait=240)
                self.assertEqual(result['status'], expected)
                self.assertEqual(result['receipt_verified'], count == 14)
                self.assertEqual(api.posts, [(395129293, 'main')])

    def test_hyxware_active_cooldown_and_two_retry_limit_are_preserved(self):
        cases = [([run(workflow_id=395129293, status='queued', conclusion=None)], 'active'),
                 ([run(workflow_id=395129293, conclusion='failure',
                       updated_at='2026-10-12T03:00:00Z')], 'cooldown'),
                 ([run(i, workflow_id=395129293, event='workflow_dispatch') for i in (1, 2)], 'limit')]
        members = dict(list(hyxware_members().items())[:13])
        for runs, expected in cases:
            with self.subTest(expected=expected):
                api = FakeAPI(runs, {r['id']: member_archive(members) for r in runs},
                              server_time=HYXWARE_NOW)
                self.assertEqual(self.check(api)['status'], expected)
                self.assertEqual(api.posts, [])

    def test_all_three_sites_can_be_healthy_without_dispatch(self):
        api = FakeAPI([run(1), run(2, workflow_id=373126098), run(3, workflow_id=395129293)],
                      {1: archive(receipt()), 2: archive(receipt('Mofas')),
                       3: member_archive(hyxware_members())}, server_time=HYXWARE_NOW)
        summary = app.Watchdog(api, wait_seconds=0).run()
        self.assertEqual([(s['site'], s['status']) for s in summary['sites']],
                         [('HappyCoding', 'healthy'), ('Mofas', 'healthy'), ('Hyxware', 'healthy')])
        self.assertEqual(api.posts, [])


class Response(io.BytesIO):
    def __init__(self, data, headers=None, status=200):
        super().__init__(data)
        self.headers = {'Date': format_datetime(DATE, usegmt=True), **(headers or {})}
        self.status = status


class APITests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(app, 'watchdog implementation is missing (expected RED)')
    def client(self, replies):
        self.requests = []
        def opener(request, timeout):
            self.requests.append(request)
            item = replies.pop(0)
            if isinstance(item, Exception):
                raise item
            return item
        return app.GitHubAPI('wdbndjdj/newapi-ai-check-in', 'fixture-token', opener=opener)
    def test_api_paginates_bounded_lists(self):
        replies = [Response(json.dumps({'total_count': 101, 'workflow_runs': [run(i) for i in range(100)]}).encode()),
                   Response(json.dumps({'total_count': 101, 'workflow_runs': [run(100)]}).encode())]
        api = self.client(replies)
        self.assertEqual(len(api.list_runs(373080586)), 101)
        self.assertIn('page=2', self.requests[-1].full_url)
    def test_pagination_exhaustion_and_incomplete_fail_closed(self):
        for value in ({'total_count': 1001, 'workflow_runs': []}, {'total_count': 2, 'workflow_runs': [run()]},
                      {'workflow_runs': []}):
            api = self.client([Response(json.dumps(value).encode())])
            with self.assertRaises(app.ProbeError):
                api.list_runs(373080586)
    def test_redirect_log_download_strips_authorization(self):
        location = 'https://example.blob.core.windows.net/logs?signature=fixture'
        redirect = urllib.error.HTTPError('https://api.github.com/logs', 302, '', {'Location': location, 'Date': format_datetime(DATE, usegmt=True)}, None)
        api = self.client([redirect, Response(archive(receipt()))])
        self.assertTrue(api.logs(1).startswith(b'PK'))
        self.assertEqual(self.requests[0].get_header('Authorization'), 'Bearer fixture-token')
        self.assertIsNone(self.requests[1].get_header('Authorization'))
    def test_untrusted_redirect_is_never_opened(self):
        redirect = urllib.error.HTTPError('https://api.github.com/logs', 302, '', {'Location': 'https://happycoding.xyz/'}, None)
        api = self.client([redirect])
        with self.assertRaises(app.ProbeError):
            api.logs(1)
        self.assertEqual(len(self.requests), 1)
    def test_client_rejects_nonwhitelisted_dispatch(self):
        api = self.client([])
        for workflow_id in (999, True, '395129293', 395129293.0):
            with self.subTest(workflow_id=workflow_id), self.assertRaises(app.ProbeError):
                api.dispatch(workflow_id)
        self.assertEqual(self.requests, [])
    def test_client_dispatches_only_the_three_checkin_workflows(self):
        for workflow_id in (373080586, 373126098, 395129293):
            with self.subTest(workflow_id=workflow_id):
                api = self.client([Response(b'', status=204)])
                try:
                    api.dispatch(workflow_id)
                except app.ProbeError as error:
                    self.fail(f'check-in workflow rejected: {error}')
                self.assertEqual(self.requests[0].full_url,
                                 api.base + f'/actions/workflows/{workflow_id}/dispatches')
                self.assertEqual(json.loads(self.requests[0].data), {'ref': 'main'})
    def test_api_error_never_leaks_body_or_token(self):
        error = urllib.error.HTTPError('https://api.github.com/', 403, 'SECRET', {}, io.BytesIO(b'SECRET'))
        api = self.client([error])
        with self.assertRaises(app.ProbeError) as caught:
            api.workflow(373080586)
        self.assertNotIn('SECRET', str(caught.exception))


if __name__ == '__main__':
    unittest.main()
