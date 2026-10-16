"""Daily HappyCoding check-in with private plain-token or JSON-account credentials."""

import json
import math
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

ORIGIN = 'https://happycoding.xyz'
BEIJING = timezone(timedelta(hours=8))


def request_json(path, token, *, method='GET', user_id=None):
    headers = {
        'Authorization': 'Bearer ' + token,
        'Accept': 'application/json',
        'User-Agent': 'HappyCoding-Daily-Checkin/1.0',
    }
    if user_id is not None:
        headers['New-Api-User'] = str(user_id)
    payload = None
    if method == 'POST':
        payload = b'{}'
        headers['Content-Type'] = 'application/json'
    try:
        request = urllib.request.Request(ORIGIN + path, data=payload, headers=headers, method=method)
        with urllib.request.urlopen(request, timeout=30) as response:
            result = json.load(response)
    except urllib.error.HTTPError as error:
        raise RuntimeError('HappyCoding HTTP ' + str(error.code)) from None
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        raise RuntimeError('HappyCoding network or response error') from None
    if not isinstance(result, dict):
        raise RuntimeError('HappyCoding returned invalid JSON')
    return result


def load_accounts(secret):
    secret = secret.strip()
    if not secret:
        raise ValueError('HappyCoding credentials are required')
    try:
        accounts = json.loads(secret)
    except ValueError:
        if secret[0] in '[{"':
            raise ValueError('HappyCoding account configuration is invalid') from None
        return [{'user_id': None, 'access_token': secret}]
    if not isinstance(accounts, list) or not accounts:
        raise ValueError('HappyCoding account configuration is invalid')
    seen = set()
    validated = []
    for account in accounts:
        if not isinstance(account, dict):
            raise ValueError('HappyCoding account configuration is invalid')
        uid, token = account.get('user_id'), account.get('access_token')
        if (type(uid) is not int or uid <= 0 or uid in seen
                or not isinstance(token, str) or not token.strip()):
            raise ValueError('HappyCoding account configuration is invalid')
        seen.add(uid)
        validated.append({'user_id': uid, 'access_token': token.strip()})
    return validated


def today_record(result, day):
    if not isinstance(result, dict) or result.get('success') is not True:
        raise RuntimeError('HappyCoding status request was unsuccessful')
    data = result.get('data')
    stats = data.get('stats') if isinstance(data, dict) else None
    if not isinstance(stats, dict) or type(stats.get('checked_in_today')) is not bool:
        raise RuntimeError('HappyCoding status is invalid')
    records = stats.get('records')
    if not isinstance(records, list):
        raise RuntimeError('HappyCoding records are invalid')
    record = None
    for item in records:
        if not isinstance(item, dict) or not isinstance(item.get('checkin_date'), str):
            raise RuntimeError('HappyCoding records are invalid')
        if item['checkin_date'] == day:
            record = item
    if stats['checked_in_today'] and record is None:
        raise RuntimeError('HappyCoding lacks a record for today')
    return record


def checked_today(result, day=None):
    day = day or datetime.now(BEIJING).strftime('%Y-%m-%d')
    return today_record(result, day) is not None


def require_same_day(day):
    if datetime.now(BEIJING).strftime('%Y-%m-%d') != day:
        raise RuntimeError('HappyCoding date changed; retry required')


def quota_award(data):
    value = data.get('quota_awarded') if isinstance(data, dict) else None
    if type(value) in (int, float) and math.isfinite(value):
        return value
    return None


def checkin(token, expected_user_id=None):
    if not isinstance(token, str) or not token.strip():
        raise ValueError('HappyCoding credentials are required')
    token = token.strip()
    now = datetime.now(BEIJING)
    day = now.strftime('%Y-%m-%d')
    user = request_json('/api/user/self', token)
    data = user.get('data') if isinstance(user, dict) else None
    user_id = data.get('id') if isinstance(data, dict) else None
    if (not isinstance(user, dict) or user.get('success') is not True
            or type(user_id) is not int or user_id <= 0):
        raise RuntimeError('HappyCoding access token is invalid')
    if expected_user_id is not None and (type(expected_user_id) is not int or user_id != expected_user_id):
        raise ValueError('HappyCoding account identity mismatch')
    require_same_day(day)
    path = '/api/user/checkin?month=' + now.strftime('%Y-%m')
    record = today_record(request_json(path, token, user_id=user_id), day)
    require_same_day(day)
    outcome = None
    state = 'already_checked_in'
    if record is None:
        outcome = request_json('/api/user/checkin', token, method='POST', user_id=user_id)
        require_same_day(day)
        # A concurrent check-in is accepted only after exact-date record verification.
        record = today_record(request_json(path, token, user_id=user_id), day)
        require_same_day(day)
        if record is None:
            raise RuntimeError('HappyCoding check-in did not pass verification')
        state = 'checked_in'
    result = {'site': 'HappyCoding', 'user_id': user_id, 'date': day,
              'record_date': record['checkin_date'], 'result': state}
    award = quota_award(record)
    if award is None and isinstance(outcome, dict):
        award = quota_award(outcome.get('data'))
    if award is not None:
        result['quota_awarded'] = award
    return result


def main():
    try:
        accounts = load_accounts(os.environ.get('HAPPYCODING_ACCESS_TOKEN', ''))
    except ValueError:
        print('HappyCoding account configuration is invalid', file=sys.stderr)
        return 1
    if os.environ.get('GITHUB_ACTIONS') == 'true':
        for account in accounts:
            masked = account['access_token'].replace('%', '%25').replace('\r', '%0D').replace('\n', '%0A')
            print('::add-mask::' + masked, flush=True)
    results = []
    failed = False
    for account in accounts:
        for attempt in range(3):
            try:
                result = checkin(account['access_token'], account['user_id'])
                results.append({key: result.get(key) for key in
                                ('user_id', 'date', 'record_date', 'result', 'quota_awarded')})
                break
            except ValueError:
                print('HappyCoding account validation failed', file=sys.stderr)
                failed = True
                break
            except Exception:
                # No raw exceptions, response bodies, configuration or profiles in logs.
                print('HappyCoding account check-in failed', file=sys.stderr)
                if attempt < 2:
                    time.sleep(10 * (attempt + 1))
                else:
                    failed = True
    day = datetime.now(BEIJING).strftime('%Y-%m-%d')
    if failed or any(result['date'] != day or result['record_date'] != day for result in results):
        print('HappyCoding all-account verification failed', file=sys.stderr)
        return 1
    summary = {'site': 'HappyCoding', 'date': day,
               'result': 'checked_in' if any(r['result'] == 'checked_in' for r in results)
               else 'already_checked_in', 'accounts': results}
    if len(results) == 1 and results[0]['quota_awarded'] is not None:
        summary['quota_awarded'] = results[0]['quota_awarded']
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
