"""Isolated Orbitix check-in. Standard library only; never logs API bodies.

Only ORBITIX_ACCESS_TOKEN supplies credentials. Dates use Beijing time, as does
the daily workflow. Midnight crossings fail closed: rerun to query the new day.
No redirects, challenge solving, cookies, automatic POST retries, or other sites.
"""

import json
import math
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone

ORIGIN = 'https://new.orbitix.site'
BEIJING = timezone(timedelta(hours=8))
MAX_RESPONSE_BYTES = 1024 * 1024
ERROR_MESSAGES = {
    'token': 'Orbitix access token is missing or invalid',
    'origin': 'Orbitix origin or endpoint is invalid',
    'redirect': 'Orbitix redirect rejected',
    'auth': 'Orbitix authentication failed',
    'challenge': 'Orbitix challenge detected; check-in stopped',
    'http': 'Orbitix HTTP request failed',
    'conflict': 'Orbitix check-in conflict requires verification',
    'network': 'Orbitix network request failed',
    'schema': 'Orbitix response schema is invalid',
    'date_changed': 'Orbitix date changed; rerun required',
    'verification': 'Orbitix exact-date check-in verification failed',
    'internal': 'Orbitix check-in failed',
}


class CheckinError(RuntimeError):
    """Only static error codes/messages may reach logs or step summaries."""

    def __init__(self, code):
        self.code = code if code in ERROR_MESSAGES else 'internal'
        super().__init__(ERROR_MESSAGES[self.code])


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        fp.close()
        raise CheckinError('redirect')


def validated_token(token):
    if not isinstance(token, str):
        raise CheckinError('token')
    token = token.strip()
    if not token or any(ord(char) < 33 or ord(char) > 126 for char in token):
        raise CheckinError('token')
    return token


def validated_endpoint(path, method):
    # Exact allowlisting also excludes userinfo, ports, fragments and URL joins.
    if ORIGIN != 'https://new.orbitix.site' or method not in ('GET', 'POST'):
        raise CheckinError('origin')
    if path in ('/api/user/self', '/api/user/checkin'):
        if method == 'POST' and path != '/api/user/checkin':
            raise CheckinError('origin')
        return
    if method == 'GET' and isinstance(path, str) and re.fullmatch(
            r'/api/user/checkin\?month=[0-9]{4}-(0[1-9]|1[0-2])', path):
        return
    raise CheckinError('origin')


def validated_envelope(result):
    if not isinstance(result, dict) or type(result.get('success')) is not bool:
        raise CheckinError('schema')
    # Treat explicit anti-bot indicators as failures, never as duplicate results.
    message = result.get('message')
    if isinstance(message, str) and any(word in message.lower() for word in (
            'captcha', 'turnstile', 'cloudflare', 'challenge', '\u4eba\u673a', '\u9a8c\u8bc1\u7801')):
        raise CheckinError('challenge')
    if any(result.get(key) for key in ('captcha', 'turnstile', 'challenge', 'requires_captcha')):
        raise CheckinError('challenge')
    return result


def request_json(path, token, *, method='GET', user_id=None):
    validated_endpoint(path, method)
    token = validated_token(token)
    if user_id is not None and (type(user_id) is not int or user_id <= 0):
        raise CheckinError('schema')
    headers = {'Authorization': 'Bearer ' + token, 'Accept': 'application/json',
               'User-Agent': 'Orbitix-Daily-Checkin/1.0'}
    if user_id is not None:
        headers['New-Api-User'] = str(user_id)
    payload = None
    if method == 'POST':
        payload = b'{}'
        headers['Content-Type'] = 'application/json'
    url = ORIGIN + path
    try:
        request = urllib.request.Request(url, data=payload, headers=headers, method=method)
        opener = urllib.request.build_opener(NoRedirect())
        with opener.open(request, timeout=30) as response:
            if response.geturl() != url or 300 <= response.getcode() < 400:
                raise CheckinError('redirect')
            if response.headers.get('cf-mitigated', '').lower() == 'challenge':
                raise CheckinError('challenge')
            if not 200 <= response.getcode() < 300:
                raise CheckinError('http')
            media_type = response.headers.get('Content-Type', '').split(';', 1)[0].strip().lower()
            if media_type != 'application/json':
                raise CheckinError('challenge' if media_type == 'text/html' else 'schema')
            raw = response.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES:
                raise CheckinError('schema')
            result = json.loads(raw.decode('utf-8'))
    except urllib.error.HTTPError as error:
        code = error.code
        challenged = error.headers and error.headers.get('cf-mitigated', '').lower() == 'challenge'
        error.close()
        if challenged:
            raise CheckinError('challenge') from None
        if 300 <= code < 400:
            raise CheckinError('redirect') from None
        if code in (401, 403):
            raise CheckinError('auth') from None
        if code == 409:
            raise CheckinError('conflict') from None
        raise CheckinError('http') from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise CheckinError('network') from None
    except (ValueError, UnicodeError, RecursionError):
        raise CheckinError('schema') from None
    return validated_envelope(result)


def today_record(result, day):
    validated_envelope(result)
    if result['success'] is not True:
        raise CheckinError('schema')
    data = result.get('data')
    stats = data.get('stats') if isinstance(data, dict) else None
    if not isinstance(stats, dict) or type(stats.get('checked_in_today')) is not bool:
        raise CheckinError('schema')
    records = stats.get('records')
    if not isinstance(records, list):
        raise CheckinError('schema')
    found = None
    for item in records:
        if not isinstance(item, dict):
            raise CheckinError('schema')
        record_day, award = item.get('checkin_date'), item.get('quota_awarded')
        if not isinstance(record_day, str):
            raise CheckinError('schema')
        try:
            if date.fromisoformat(record_day).isoformat() != record_day:
                raise ValueError
        except ValueError:
            raise CheckinError('schema') from None
        if (type(award) not in (int, float) or award < 0
                or (type(award) is float and not math.isfinite(award))):
            raise CheckinError('schema')
        if record_day == day:
            if found is not None:
                raise CheckinError('schema')
            # Carry forward only validated scalar fields, never other record data.
            found = {'checkin_date': record_day, 'quota_awarded': award}
    if stats['checked_in_today'] and found is None:
        raise CheckinError('verification')
    return found


def require_same_day(day):
    if datetime.now(BEIJING).strftime('%Y-%m-%d') != day:
        raise CheckinError('date_changed')


def checkin(token):
    token = validated_token(token)
    now = datetime.now(BEIJING)
    day = now.strftime('%Y-%m-%d')
    identity = validated_envelope(request_json('/api/user/self', token))
    data = identity.get('data')
    user_id = data.get('id') if isinstance(data, dict) else None
    if identity['success'] is not True or type(user_id) is not int or user_id <= 0:
        raise CheckinError('auth')
    require_same_day(day)
    path = '/api/user/checkin?month=' + now.strftime('%Y-%m')
    record = today_record(request_json(path, token, user_id=user_id), day)
    require_same_day(day)
    state = 'already_checked_in'
    if record is None:
        try:
            validated_envelope(request_json('/api/user/checkin', token, method='POST', user_id=user_id))
        except CheckinError as error:
            # A lost POST response or concurrent 409 is ambiguous, not a reason
            # to repost. Other failures (especially auth/challenges) stop here.
            if error.code not in ('network', 'conflict'):
                raise
        require_same_day(day)
        record = today_record(request_json(path, token, user_id=user_id), day)
        require_same_day(day)
        if record is None:
            raise CheckinError('verification')
        state = 'checked_in'
    return {'site': 'Orbitix', 'date': day, 'record_date': record['checkin_date'],
            'result': state, 'quota_awarded': record['quota_awarded']}


def write_summary(result=None, error_code='internal'):
    path = os.environ.get('GITHUB_STEP_SUMMARY')
    if not path:
        return
    if result is None:
        message = ERROR_MESSAGES.get(error_code, ERROR_MESSAGES['internal'])
        text = '## Orbitix daily check-in\n\nResult: **failed**\n\n' + message + '\n'
    else:
        text = ('## Orbitix daily check-in\n\n'
                f"Result: **{result['result']}**\n\n"
                f"Beijing date: {result['date']}\n\n"
                f"Verified record date: {result['record_date']}\n\n"
                f"Verified quota awarded: {result['quota_awarded']}\n")
    try:
        with open(path, 'a', encoding='utf-8') as summary:
            summary.write(text)
    except OSError:
        print('Orbitix step summary unavailable', file=sys.stderr)


def main():
    try:
        token = validated_token(os.environ.get('ORBITIX_ACCESS_TOKEN', ''))
        if os.environ.get('GITHUB_ACTIONS') == 'true':
            masked = token.replace('%', '%25').replace('\r', '%0D').replace('\n', '%0A')
            print('::add-mask::' + masked, flush=True)
        result = checkin(token)
        require_same_day(result['date'])
        if result['record_date'] != result['date']:
            raise CheckinError('verification')
    except Exception as error:
        code = error.code if isinstance(error, CheckinError) else 'internal'
        print(ERROR_MESSAGES[code], file=sys.stderr)
        write_summary(error_code=code)
        return 1
    write_summary(result)
    print(json.dumps(result, ensure_ascii=True, allow_nan=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
