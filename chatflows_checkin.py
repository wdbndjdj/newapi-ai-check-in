"""ChatFlowsAI daily check-in. Credentials are read only from environment."""
import json
import os
import sys
import urllib.error
import urllib.request

ORIGIN = 'https://ai.chatflowsai.com'
ALLOWED = {('POST', '/api/v1/auth/login'), ('GET', '/api/checkin/status'), ('POST', '/api/checkin')}

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

class Client:
    def __init__(self, email, password):
        self.email, self.password = email, password
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    def request(self, method, path, token=None):
        if (method, path) not in ALLOWED:
            raise RuntimeError('unexpected endpoint')
        headers = {'Content-Type':'application/json', 'Accept':'application/json', 'User-Agent':'ChatFlowsAI-Checkin/1.0'}
        if token: headers['Authorization'] = 'Bearer ' + token
        data = None
        if method == 'POST':
            body = {'email':self.email, 'password':self.password} if path.endswith('/auth/login') else {}
            data = json.dumps(body).encode('utf8')
        req = urllib.request.Request(ORIGIN+path, data=data, headers=headers, method=method)
        try:
            with self.opener.open(req, timeout=30) as resp:
                raw = resp.read(1048577)
                if len(raw)>1048576: raise RuntimeError('response too large')
                result = json.loads(raw)
        except urllib.error.HTTPError as e:
            if path == '/api/checkin' and e.code == 409: return {'code':409}
            raise RuntimeError('request failed: HTTP '+str(e.code)) from None
        except (urllib.error.URLError, TimeoutError, ValueError):
            raise RuntimeError('request failed or returned invalid JSON') from None
        if not isinstance(result,dict): raise RuntimeError('invalid response')
        return result

def get_status(client, token):
    result = client.request('GET','/api/checkin/status',token)
    data = result.get('data')
    if result.get('code') != 0 or not isinstance(data,dict) or not isinstance(data.get('can_checkin'),bool) or not isinstance(data.get('period_key'),str):
        raise RuntimeError('invalid check-in status')
    return data

def run_checkin(client):
    login = client.request('POST','/api/v1/auth/login')
    data = login.get('data')
    if login.get('code') != 0 or not isinstance(data,dict) or not isinstance(data.get('access_token'),str) or not data['access_token']:
        raise RuntimeError('login failed or additional verification required')
    token = data['access_token']
    before = get_status(client,token)
    if not before['can_checkin']:
        return {'result':'already_checked_in','period':before['period_key']}
    result = client.request('POST','/api/checkin',token)
    if result.get('code') not in (0,409): raise RuntimeError('check-in failed')
    after = get_status(client,token)
    if after['can_checkin'] or after['period_key'] != before['period_key']:
        raise RuntimeError('check-in verification failed or period changed')
    return {'result':'checked_in' if result.get('code') == 0 else 'already_checked_in','period':after['period_key']}

def main():
    email, password = os.environ.get('CHATFLOWS_EMAIL',''), os.environ.get('CHATFLOWS_PASSWORD','')
    if not email or not password:
        print('Missing ChatFlowsAI credentials',file=sys.stderr); return 1
    try:
        print(json.dumps(run_checkin(Client(email,password)),ensure_ascii=False))
        return 0
    except (RuntimeError, OSError):
        print('ChatFlowsAI login/check-in/verification failed; inspect service availability or credentials.',file=sys.stderr)
        return 1

if __name__ == '__main__': raise SystemExit(main())
