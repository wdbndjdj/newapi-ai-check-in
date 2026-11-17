"""APIKey Casa check-in. Never log credentials, tokens, or response bodies."""
import http.client, json, math, os, sys, urllib.request, urllib.error
from datetime import datetime, timedelta, timezone
ORIGIN='https://apikey.casa'
PATHS={('POST','/api/v1/auth/login'),('GET','/api/v1/redeem/checkin'),('POST','/api/v1/redeem/checkin')}
class Failure(Exception): pass
class Transient(Failure): pass
class NoRedirect(urllib.request.HTTPRedirectHandler):
 def redirect_request(self,req,fp,code,msg,headers,newurl): return None
class Client:
 def __init__(self,email,password):
  self.email=email;self.password=password;self.opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
 def request(self,method,path,token=None):
  if (method,path) not in PATHS:raise Failure('endpoint refused')
  headers={'Content-Type':'application/json','Accept':'application/json','User-Agent':'Casa-Daily-Checkin/1.0'}
  if token:
   if not isinstance(token,str) or not token or any(ord(c)<33 or ord(c)>126 for c in token):raise Failure('invalid token')
   headers['Authorization']='Bearer '+token
  data=None
  if method=='POST':data=json.dumps({'email':self.email,'password':self.password} if path.endswith('/login') else {}).encode('utf8')
  try:
   req=urllib.request.Request(ORIGIN+path,data=data,headers=headers,method=method)
   with self.opener.open(req,timeout=30) as resp:
    if resp.geturl()!=ORIGIN+path or resp.headers.get('cf-mitigated','').lower()=='challenge':raise Failure('verification required')
    if resp.headers.get_content_type()!='application/json':
     if method=='POST' and path.endswith('/checkin'):raise Transient('uncertain submit')
     raise Failure('invalid response')
    raw=resp.read(1048577)
    if len(raw)>1048576:
     if method=='POST' and path.endswith('/checkin'):raise Transient('uncertain submit')
     raise Failure('response too large')
    result=json.loads(raw)
  except urllib.error.HTTPError as e:
   code=e.code;challenge=e.headers.get('cf-mitigated','').lower()=='challenge';e.close()
   if challenge or code in (401,403):raise Failure('authentication or verification rejected') from None
   if method=='POST' and path.endswith('/checkin') and code in (409,500,502,503,504):raise Transient('uncertain submit') from None
   raise Failure('HTTP request failed') from None
  except (urllib.error.URLError,TimeoutError,OSError,http.client.HTTPException):raise Transient('network failure') from None
  except (ValueError,UnicodeError,RecursionError):
   if method=='POST' and path.endswith('/checkin'):raise Transient('uncertain submit') from None
   raise Failure('invalid response') from None
  if not isinstance(result,dict):raise Failure('invalid response')
  return result
def today():return datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%d')
def get_status(client,token,day):
 x=client.request('GET','/api/v1/redeem/checkin',token);data=x.get('data')
 if type(x.get('code')) is not int or x.get('code')!=0 or not isinstance(data,dict) or type(data.get('checked_in')) is not bool or data.get('date')!=day:raise Failure('invalid status or date')
 reward=data.get('reward')
 if type(reward) not in (int,float) or reward<0 or reward>1000000000 or (type(reward) is float and not math.isfinite(reward)):raise Failure('invalid reward')
 return data
def checkin(client,day,clock=None):
 now=clock or (lambda:day)
 def same():
  if now()!=day:raise Failure('date changed')
 login=client.request('POST','/api/v1/auth/login');data=login.get('data');token=data.get('access_token') if isinstance(data,dict) else None
 if type(login.get('code')) is not int or login.get('code')!=0 or not isinstance(token,str) or not token:raise Failure('login failed or verification required')
 same(); before=get_status(client,token,day);same()
 if before['checked_in']:return {'site':'APIKey Casa','date':day,'result':'already_checked_in'}
 if before['reward']<=0:raise Failure('check-in disabled')
 try:
  post=client.request('POST','/api/v1/redeem/checkin',token)
  if type(post.get('code')) is not int or post.get('code')!=0:raise Failure('check-in rejected')
 except Transient:pass
 same();after=get_status(client,token,day);same()
 if not after['checked_in']:raise Failure('check-in not verified')
 return {'site':'APIKey Casa','date':day,'result':'checked_in'}
def main():
 email=os.environ.get('APICASA_EMAIL','').strip();password=os.environ.get('APICASA_PASSWORD','')
 if not email or not password or any(ord(c)<32 for c in email+password):print('APIKey Casa credentials missing or invalid',file=sys.stderr);return 1
 if os.environ.get('GITHUB_ACTIONS')=='true':
  for value in (email,password):print('::add-mask::'+value.replace('%','%25'))
 try:print(json.dumps(checkin(Client(email,password),today(),clock=today),ensure_ascii=False));return 0
 except (Failure,ValueError):print('APIKey Casa check-in failed; check credentials, verification or network',file=sys.stderr);return 1
if __name__=='__main__':raise SystemExit(main())
