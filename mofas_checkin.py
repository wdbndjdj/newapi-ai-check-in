"""Daily Mofas check-in with a private environment access token."""

import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

ORIGIN = 'https://www.mofas.one'
BEIJING = timezone(timedelta(hours=8))


def request_json(path, token, *, method='GET', user_id=None):
    headers = {
        'Authorization': 'Bearer ' + token,
        'Accept': 'application/json',
        'User-Agent': 'Mofas-Daily-Checkin/1.0',
    }
    if user_id is not None:
        headers['New-Api-User'] = str(user_id)
    payload = None
    if method == 'POST':
        payload = b'{}'
        headers['Content-Type'] = 'application/json'
    request = urllib.request.Request(ORIGIN + path, data=payload, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            result = json.load(response)
    except urllib.error.HTTPError as error:
        raise RuntimeError('Mofas HTTP ' + str(error.code)) from None
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        raise RuntimeError('Mofas network or response error') from None
    if not isinstance(result, dict):
        raise RuntimeError('Mofas returned invalid JSON')
    return result


def checked_today(result):
    if result.get('success') is not True:
        raise RuntimeError('Mofas status request was unsuccessful')
    data = result.get('data')
    stats = data.get('stats') if isinstance(data, dict) else None
    value = stats.get('checked_in_today') if isinstance(stats, dict) else None
    if not isinstance(value, bool):
        raise RuntimeError('Mofas status lacks checked_in_today')
    return value


def checkin(token, user_id):
    token = token.strip()
    if not token:
        raise ValueError('MOFAS_ACCESS_TOKEN is required')
    if type(user_id) is not int or user_id <= 0:
        raise ValueError('MOFAS_USER_ID must be a positive integer')
    user = request_json('/api/user/self', token, user_id=user_id)
    profile = user.get('data')
    if user.get('success') is not True or not isinstance(profile, dict) or type(profile.get('id')) is not int or profile['id'] != user_id:
        raise RuntimeError('Mofas access token is invalid')
    now = datetime.now(BEIJING)
    path = '/api/user/checkin?month=' + now.strftime('%Y-%m')
    if checked_today(request_json(path, token, user_id=user_id)):
        return {'site': 'Mofas', 'date': now.strftime('%Y-%m-%d'), 'result': 'already_checked_in'}
    outcome = request_json('/api/user/checkin', token, method='POST', user_id=user_id)
    # Recheck even after a concurrent check-in; never infer success from HTTP 200 alone.
    if not checked_today(request_json(path, token, user_id=user_id)):
        raise RuntimeError('Mofas check-in did not pass verification')
    result = {'site': 'Mofas', 'date': now.strftime('%Y-%m-%d'), 'result': 'checked_in'}
    data = outcome.get('data')
    award = data.get('quota_awarded') if isinstance(data, dict) else None
    if isinstance(award, (int, float)):
        result['quota_awarded'] = award
    return result


def main():
    token = os.environ.get('MOFAS_ACCESS_TOKEN', '')
    for attempt in range(3):
        try:
            user_id = int(os.environ.get('MOFAS_USER_ID', '0'))
            print(json.dumps(checkin(token, user_id), ensure_ascii=False))
            return 0
        except ValueError as error:
            print(str(error), file=sys.stderr)
            return 1
        except RuntimeError as error:
            print(str(error), file=sys.stderr)
            if attempt < 2:
                time.sleep(10 * (attempt + 1))
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
