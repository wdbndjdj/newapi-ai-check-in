"""Conservative GitHub-native watchdog for exactly two check-in workflows.

No site API or site credentials are used. A green run alone is not a receipt.
All probe failures are fail-closed; an uncertain dispatch is never retried here.
"""
import argparse
import io
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

UTC = timezone.utc
BEIJING = timezone(timedelta(hours=8))
SITES = {
    'HappyCoding': (373080586, 'happycoding.yml', 9, 57, 'happycoding_checkin.py'),
    'Mofas': (373126098, 'mofas.yml', 10, 7, 'mofas_checkin.py'),
}
ACTIVE = ('queued', 'in_progress', 'waiting', 'pending', 'requested')
CONCLUSIONS = {'success', 'failure', 'cancelled', 'timed_out', 'action_required',
               'neutral', 'skipped', 'stale', 'startup_failure'}
BOT = 'github-actions[bot]'
MAX_BYTES = 16 * 1024 * 1024


class ProbeError(Exception):
    """Only fixed, non-sensitive error codes may cross the summary boundary."""


def timestamp(value):
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if result.tzinfo is None:
            raise ValueError
        return result.astimezone(UTC)
    except (ValueError, TypeError, AttributeError):
        raise ProbeError('invalid_timestamp') from None


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class GitHubAPI:
    """Bounded stdlib HTTP adapter. Injection replaces ALL network operations."""
    def __init__(self, repository, token, *, opener=None):
        if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repository or '') or not token:
            raise ProbeError('missing_github_configuration')
        self.base = 'https://api.github.com/repos/' + repository
        self.token = token
        self.opener = opener or urllib.request.build_opener(NoRedirect()).open
        self.server_time = None
        self.deadline = None
        self.clock = time.monotonic

    def set_deadline(self, deadline, clock):
        self.deadline, self.clock = deadline, clock

    def _date(self, headers):
        try:
            date = parsedate_to_datetime(headers.get('Date', ''))
            self.server_time = date.astimezone(UTC) if date.tzinfo is not None else None
        except (ValueError, TypeError, AttributeError):
            self.server_time = None

    def _open(self, request):
        timeout = 20
        if self.deadline is not None:
            timeout = min(timeout, self.deadline - self.clock())
            if timeout <= 0:
                raise ProbeError('wait_deadline')
        return self.opener(request, timeout=timeout)

    def _request(self, path, *, method='GET', payload=None, raw=False):
        headers = {'Authorization': 'Bearer ' + self.token, 'Accept': 'application/vnd.github+json',
                   'X-GitHub-Api-Version': '2022-11-28', 'User-Agent': 'Two-Site-Checkin-Watchdog'}
        data = None if payload is None else json.dumps(payload).encode('utf-8')
        if data is not None:
            headers['Content-Type'] = 'application/json'
        request = urllib.request.Request(self.base + path, data=data, headers=headers, method=method)
        try:
            with self._open(request) as response:
                self._date(response.headers)
                body = response.read(MAX_BYTES + 1)
                if len(body) > MAX_BYTES:
                    raise ProbeError('response_too_large')
                if method == 'POST':
                    if response.status != 204:
                        raise ProbeError('dispatch_not_accepted')
                    return None
                return body if raw else json.loads(body)
        except urllib.error.HTTPError as error:
            self._date(error.headers)
            if raw and error.code in (301, 302, 303, 307, 308):
                return self._download(error.headers.get('Location', ''))
            raise ProbeError('github_http_' + str(error.code)) from None
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            raise ProbeError('github_response_failed') from None

    def _download(self, location):
        parsed = urllib.parse.urlsplit(location)
        host = parsed.hostname or ''
        trusted = host.endswith('.blob.core.windows.net') or host.endswith('.actions.githubusercontent.com')
        if parsed.scheme != 'https' or not trusted or parsed.username or parsed.password or parsed.port not in (None, 443):
            raise ProbeError('untrusted_log_redirect')
        # Never forward Authorization (or other API headers) to the signed URL.
        request = urllib.request.Request(location, headers={'User-Agent': 'Two-Site-Checkin-Watchdog'})
        try:
            with self._open(request) as response:
                body = response.read(MAX_BYTES + 1)
                if len(body) > MAX_BYTES:
                    raise ProbeError('logs_too_large')
                return body
        except (urllib.error.URLError, TimeoutError, OSError):
            raise ProbeError('log_download_failed') from None

    @staticmethod
    def _allow(workflow_id):
        if type(workflow_id) is not int or workflow_id not in (373080586, 373126098):
            raise ProbeError('workflow_not_whitelisted')

    def workflow(self, workflow_id):
        self._allow(workflow_id)
        return self._request('/actions/workflows/' + str(workflow_id))

    def list_runs(self, workflow_id, *, since=None, status=None):
        self._allow(workflow_id)
        rows, total = [], None
        for page in range(1, 11):
            query = {'per_page': 100, 'page': page, 'exclude_pull_requests': 'true'}
            if since is not None:
                query['created'] = '>=' + since
            if status is not None:
                query['status'] = status
            body = self._request('/actions/workflows/' + str(workflow_id) + '/runs?' + urllib.parse.urlencode(query))
            if not isinstance(body, dict) or type(body.get('total_count')) is not int:
                raise ProbeError('incomplete_run_list')
            count, chunk = body['total_count'], body.get('workflow_runs')
            if count < 0 or count > 1000 or not isinstance(chunk, list) or (total is not None and total != count):
                raise ProbeError('incomplete_run_list')
            total = count
            if len(chunk) != min(100, total - len(rows)):
                raise ProbeError('incomplete_run_list')
            rows.extend(chunk)
            if len(rows) == total:
                ids = [r.get('id') for r in rows if isinstance(r, dict)]
                if len(ids) != len(rows) or any(type(i) is not int for i in ids) or len(set(ids)) != len(ids):
                    raise ProbeError('incomplete_run_list')
                return rows
        raise ProbeError('pagination_exhausted')

    def logs(self, run_id):
        return self._request('/actions/runs/' + str(run_id) + '/logs', raw=True)

    def get_run(self, run_id):
        return self._request('/actions/runs/' + str(run_id))

    def main_sha(self):
        body = self._request('/commits/main')
        sha = body.get('sha') if isinstance(body, dict) else None
        if not isinstance(sha, str) or not re.fullmatch('[0-9a-f]{40}', sha):
            raise ProbeError('invalid_main_sha')
        return sha

    def dispatch(self, workflow_id, ref='main'):
        self._allow(workflow_id)
        if ref != 'main':
            raise ProbeError('ref_not_main')
        self._request('/actions/workflows/' + str(workflow_id) + '/dispatches', method='POST', payload={'ref': 'main'})


def has_receipt(data, site, day):
    """Accept only actual check-in step output or its bounded full-job region."""
    script = SITES[site][4]
    found_source, source_output, found_receipt = False, False, False
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            members = archive.infolist()
            if not members or len(members) > 200 or sum(m.file_size for m in members) > MAX_BYTES:
                raise ProbeError('incomplete_logs')
            for member in members:
                if member.is_dir():
                    continue
                if member.file_size > 4 * 1024 * 1024 or member.flag_bits & 1:
                    raise ProbeError('incomplete_logs')
                # Reading every member checks CRC, including files not used as evidence.
                content = archive.read(member).decode('utf-8-sig')
                name = member.filename.rsplit('/', 1)[-1]
                step = bool(re.fullmatch(r'\d+_签到并确认今日状态\.txt', name))
                job = bool(re.fullmatch(r'\d+_checkin\.txt', name))
                if not (step or job):
                    continue
                in_output = step
                group = False
                if step:
                    found_source = True
                for line in content.splitlines():
                    line = re.sub(r'^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?Z\s+', '', line).strip()
                    if line.startswith('##[group]'):
                        in_output = False
                        group = line == '##[group]Run python -u ' + script
                        if group:
                            found_source = True
                        continue
                    if line == '##[endgroup]':
                        in_output, group = group, False
                        continue
                    if line.startswith('Post job cleanup'):
                        in_output = False
                    if not in_output or not line:
                        continue
                    if not line.startswith('##['):
                        source_output = True
                    if not line.startswith('{'):
                        continue
                    try:
                        value = json.loads(line)
                    except ValueError:
                        raise ProbeError('malformed_receipt') from None
                    if isinstance(value, dict) and value.get('site') == site:
                        receipt_day = value.get('date')
                        if not isinstance(receipt_day, str) or not re.fullmatch(r'\d{4}-\d\d-\d\d', receipt_day) or value.get('result') not in ('checked_in', 'already_checked_in'):
                            raise ProbeError('malformed_receipt')
                        try:
                            datetime.fromisoformat(receipt_day)
                        except ValueError:
                            raise ProbeError('malformed_receipt') from None
                        if receipt_day == day:
                            found_receipt = True
    except (zipfile.BadZipFile, UnicodeError, RuntimeError, OSError, ValueError):
        raise ProbeError('incomplete_logs') from None
    if not found_source or not source_output:
        raise ProbeError('checkin_log_unavailable')
    return found_receipt


class Watchdog:
    """Decision layer; API, elapsed clock and sleep are independently injectable."""
    def __init__(self, api, *, now=time.monotonic, sleep=time.sleep, wait_seconds=240):
        if not 0 <= wait_seconds <= 240:
            raise ValueError('wait_seconds must be between 0 and 240')
        self.api, self.now, self.sleep, self.wait_seconds = api, now, sleep, wait_seconds
        self.receipts = {}

    def _server_now(self):
        value = self.api.server_time
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise ProbeError('server_date_unavailable')
        return value.astimezone(BEIJING)

    @staticmethod
    def _validate(run, workflow_id):
        if not isinstance(run, dict) or type(run.get('id')) is not int or run.get('workflow_id') != workflow_id:
            raise ProbeError('invalid_run_identity')
        status = run.get('status')
        if status not in ACTIVE + ('completed',):
            raise ProbeError('unknown_run_status')
        if status == 'completed' and run.get('conclusion') not in CONCLUSIONS:
            raise ProbeError('unknown_run_conclusion')
        if not isinstance(run.get('actor'), dict) or not isinstance(run['actor'].get('login'), str) or not isinstance(run.get('event'), str):
            raise ProbeError('invalid_run_actor')
        timestamp(run.get('created_at'))
        timestamp(run.get('updated_at'))
        return run

    def _receipt(self, run, site, day):
        key = (run['id'], run.get('run_attempt'), day)
        if key not in self.receipts:
            self.receipts[key] = has_receipt(self.api.logs(run['id']), site, day)
        return self.receipts[key]

    def _probe(self, site, day):
        workflow_id = SITES[site][0]
        start = datetime.fromisoformat(day).replace(tzinfo=BEIJING)
        since = (start - timedelta(days=1)).astimezone(UTC).strftime('%Y-%m-%dT%H:%M:%SZ')
        runs = self.api.list_runs(workflow_id, since=since)
        for status in ACTIVE:
            # No date filter: an older queued/environment-waiting run also blocks.
            older = self.api.list_runs(workflow_id, status=status)
            for run in older:
                self._validate(run, workflow_id)
                if run['status'] != status:
                    raise ProbeError('incomplete_active_list')
            runs += older
        unique = {r['id']: self._validate(r, workflow_id) for r in runs}
        runs = list(unique.values())
        if self._server_now().date().isoformat() != day:
            raise ProbeError('beijing_day_changed')
        active = [r for r in runs if r['status'] in ACTIVE]
        if active:
            return {'status': 'active', 'run_ids': sorted(r['id'] for r in active)}, runs
        completed = [r for r in runs if r['head_branch'] == 'main']
        if len(completed) > 20:
            raise ProbeError('log_probe_limit')
        for run in completed:
            if self._receipt(run, site, day):
                return {'status': 'healthy', 'run_id': run['id'], 'conclusion': run['conclusion']}, runs
        attempts = sum(r['event'] == 'workflow_dispatch' and r['actor']['login'] == BOT and
                       timestamp(r['created_at']).astimezone(BEIJING).date().isoformat() == day for r in runs)
        if attempts >= 2:
            return {'status': 'limit', 'automatic_dispatches_today': attempts}, runs
        latest_fail = max((timestamp(r['updated_at']) for r in runs if r['conclusion'] != 'success'), default=None)
        if latest_fail is not None and self._server_now() - latest_fail < timedelta(minutes=60):
            return {'status': 'cooldown', 'retry_after': (latest_fail + timedelta(minutes=60)).isoformat()}, runs
        return None, runs

    def _wait(self, site, day, prior_ids, dispatched_at, sha):
        deadline = self.now() + self.wait_seconds
        if hasattr(self.api, 'set_deadline'):
            self.api.set_deadline(deadline, self.now)
        selected = None
        try:
            while self.now() < deadline:
                if selected is None:
                    since = dispatched_at.astimezone(UTC).strftime('%Y-%m-%dT%H:%M:%SZ')
                    runs = self.api.list_runs(SITES[site][0], since=since)
                    matches = []
                    for run in runs:
                        self._validate(run, SITES[site][0])
                        if (run['id'] not in prior_ids and run['event'] == 'workflow_dispatch' and
                            run['actor']['login'] == BOT and run.get('head_branch') == 'main' and
                            run.get('head_sha') == sha and timestamp(run['created_at']) >= dispatched_at):
                            matches.append(run)
                    if len(matches) > 1:
                        raise ProbeError('ambiguous_dispatched_run')
                    if matches:
                        selected = matches[0]['id']
                if selected is not None:
                    run = self._validate(self.api.get_run(selected), SITES[site][0])
                    if self._server_now().date().isoformat() != day:
                        raise ProbeError('beijing_day_changed')
                    if run['status'] == 'completed':
                        verified = self._receipt(run, site, day)
                        return {'status': 'verified' if verified else 'failed', 'run_id': selected,
                                'conclusion': run['conclusion'], 'receipt_verified': verified}
                self.sleep(min(10, max(0, deadline - self.now())))
            return {'status': 'pending', 'run_id': selected, 'reason': 'verification_deadline', 'wait_seconds': self.wait_seconds}
        except ProbeError as error:
            if str(error) == 'wait_deadline':
                return {'status': 'pending', 'run_id': selected, 'reason': 'verification_deadline'}
            raise
        finally:
            if hasattr(self.api, 'set_deadline'):
                self.api.set_deadline(None, self.now)

    def check_site(self, site):
        result = {'site': site, 'status': 'unknown'}
        if site not in SITES:
            return {**result, 'reason': 'site_not_whitelisted'}
        result['workflow'] = SITES[site][1]
        result['workflow_id'] = SITES[site][0]
        try:
            workflow = self.api.workflow(SITES[site][0])
            if not isinstance(workflow, dict) or workflow.get('id') != SITES[site][0] or workflow.get('path') != '.github/workflows/' + SITES[site][1] or workflow.get('state') != 'active':
                raise ProbeError('workflow_identity_or_state')
            server_now = self._server_now()
            day = server_now.date().isoformat()
            result['beijing_date'] = day
            result['server_time'] = server_now.isoformat()
            due = server_now.replace(hour=SITES[site][2], minute=SITES[site][3], second=0, microsecond=0) + timedelta(minutes=15)
            result['eligible_at'] = due.isoformat()
            if server_now < due:
                return {**result, 'status': 'not_due'}
            decision, prior = self._probe(site, day)
            if decision is not None:
                return {**result, **decision}
            # Refresh after log reads; never dispatch from a stale decision.
            sha = self.api.main_sha()
            decision, prior = self._probe(site, day)
            if decision is not None:
                return {**result, **decision}
            dispatched_at = self._server_now().astimezone(UTC)
            result['dispatch_requested'] = True
            self.api.dispatch(SITES[site][0], ref='main')
            result['dispatch_accepted'] = True
            result['dispatched_at'] = dispatched_at.isoformat()
            if self.wait_seconds == 0:
                return {**result, 'status': 'pending', 'reason': 'dispatch_accepted_not_verified'}
            return {**result, **self._wait(site, day, {r['id'] for r in prior}, dispatched_at, sha)}
        except ProbeError as error:
            return {**result, 'status': 'unknown', 'reason': str(error)}
        except Exception:
            # No exception body, raw logs, signed URL or token reaches stdout.
            return {**result, 'status': 'unknown', 'reason': 'probe_failed'}

    def run(self):
        return {'schema_version': 1, 'sites': [self.check_site(site) for site in SITES]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--wait-seconds', type=int, default=240, choices=range(241))
    args = parser.parse_args(argv)
    try:
        api = GitHubAPI(os.environ.get('GITHUB_REPOSITORY', ''), os.environ.get('GITHUB_TOKEN', ''))
        summary = Watchdog(api, wait_seconds=args.wait_seconds).run()
    except ProbeError as error:
        summary = {'schema_version': 1, 'status': 'unknown', 'reason': str(error), 'sites': []}
    encoded = json.dumps(summary, ensure_ascii=False, sort_keys=True)
    print(encoded)
    step_summary = os.environ.get('GITHUB_STEP_SUMMARY')
    if step_summary:
        with open(step_summary, 'a', encoding='utf-8') as output:
            output.write('## Check-in watchdog\n\n```json\n' + encoded + '\n```\n')
    return int(summary.get('status') == 'unknown' or any(s['status'] in ('unknown', 'failed') for s in summary['sites']))


if __name__ == '__main__':
    raise SystemExit(main())
