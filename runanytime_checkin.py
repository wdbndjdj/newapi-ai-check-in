"""RunAnytime daily check-in; credentials stay in environment, fail closed."""
import os, sys, json, hashlib, time, re, urllib.request, urllib.error, urllib.parse
from datetime import datetime, timezone, timedelta
import orbitix_checkin as core
ORIGIN='https://runanytime.hxi.me'
core.SITE_PROFILES['runanytime']={'name':'RunAnytime','origin':ORIGIN,'token_env':'RUNANYTIME_ACCESS_TOKEN','user_agent':'RunAnytime-Daily-Checkin/1.0'}
class Failure(Exception): pass
class NoRedirect(urllib.request.HTTPRedirectHandler):
 def redirect_request(self,req,fp,code,msg,headers,newurl): return None
def day_string(): return datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%d')
def solve_pow(prefix,difficulty,timeout=30):
 if not isinstance(prefix,str) or not 1<=len(prefix)<=1024 or type(difficulty) is not int or not 0<=difficulty<=26: raise ValueError('invalid proof challenge')
 end=time.monotonic()+timeout
 for i in range(2**32):
  if i%4096==0 and time.monotonic()>end: raise Failure('proof timed out')
  nonce=format(i,'08x'); digest=hashlib.sha256((prefix+nonce).encode()).digest()
  if int.from_bytes(digest,'big')>>(256-difficulty)==0:return nonce
 raise Failure('proof exhausted')
def request(path,token,uid=None,method='GET'):
 valid=(method=='GET' and (path in ('/api/status','/api/user/self','/api/user/pow/challenge?action=checkin') or bool(re.fullmatch(r'/api/user/checkin\?month=\d{4}-(0[1-9]|1[0-2])',path)))) or (method=='POST' and bool(re.fullmatch(r'/api/user/checkin\?pow_challenge=[A-Za-z0-9_-]{1,128}&pow_nonce=[0-9a-f]{8}',path)))
 if not valid:raise Failure('endpoint rejected')
 headers={'Accept':'application/json','User-Agent':'RunAnytime-Daily-Checkin/1.0'}
 if path!='/api/status':headers['Authorization']='Bearer '+core.validated_token(token)
 if uid is not None:headers['New-Api-User']=str(uid)
 body=None if method=='GET' else b'{}'
 if body:headers['Content-Type']='application/json'
 try:
  op=urllib.request.build_opener(NoRedirect()); req=urllib.request.Request(ORIGIN+path,data=body,headers=headers,method=method)
  with op.open(req,timeout=30) as r:
   if r.geturl()!=ORIGIN+path or r.headers.get('cf-mitigated','').lower()=='challenge' or r.headers.get_content_type()!='application/json':raise Failure('challenge or invalid response')
   raw=r.read(1048577)
   if len(raw)>1048576:raise Failure('response too large')
   data=json.loads(raw)
  if not isinstance(data,dict):raise Failure('invalid response')
  return data
 except (urllib.error.URLError,OSError,ValueError,UnicodeError,RecursionError):raise Failure('network or response failure') from None
def checkin(token):
 day=day_string(); ident=request('/api/user/self',token); data=ident.get('data'); uid=data.get('id') if isinstance(data,dict) else None
 if ident.get('success') is not True or type(uid) is not int or uid<=0:raise Failure('authentication failed')
 path='/api/user/checkin?month='+day[:7]
 def record():
  if day_string()!=day:raise Failure('date changed')
  try:return core.today_record(request(path,token,uid),day)
  except core.CheckinError:raise Failure('invalid check-in status') from None
 entry=record(); result='already_checked_in'
 if entry is None:
  state=request('/api/status',token); state=state.get('data') if state.get('success') is True else None
  if not isinstance(state,dict) or state.get('checkin_enabled') is not True or state.get('pow_enabled') is not True or state.get('pow_mode') != 'replace':raise Failure('unsupported verification policy')
  ch=request('/api/user/pow/challenge?action=checkin',token,uid); c=ch.get('data') if ch.get('success') is True else None
  if not isinstance(c,dict) or not isinstance(c.get('challenge_id'),str) or not re.fullmatch('[A-Za-z0-9_-]{1,128}',c['challenge_id']):raise Failure('invalid proof challenge')
  nonce=solve_pow(c.get('prefix'),c.get('difficulty')); submit='/api/user/checkin?pow_challenge='+c['challenge_id']+'&pow_nonce='+nonce
  if day_string()!=day:raise Failure('date changed')
  try:
   post=request(submit,token,uid,method='POST')
  except Failure as exc:
   if str(exc)!='network or response failure':raise
   post=None
  if post is not None and post.get('success') is not True:raise Failure('check-in rejected or interactive verification required')
  entry=record()
  if entry is None:raise Failure('check-in not confirmed')
  result='checked_in'
 if day_string()!=day:raise Failure('date changed')
 return {'site':'RunAnytime','date':day,'record_date':entry['checkin_date'],'result':result,'quota_awarded':entry['quota_awarded']}
def main():
 token=os.environ.get('RUNANYTIME_ACCESS_TOKEN','').strip()
 if not token:print('RunAnytime access token missing',file=sys.stderr); return 1
 try:token=core.validated_token(token)
 except core.CheckinError:print('RunAnytime token invalid',file=sys.stderr);return 1
 if os.environ.get('GITHUB_ACTIONS')=='true':print('::add-mask::'+token.replace('%','%25'))
 try:print(json.dumps(checkin(token),ensure_ascii=False));return 0
 except (Failure,ValueError,core.CheckinError):print('RunAnytime check-in failed or needs verification',file=sys.stderr);return 1
if __name__=='__main__':raise SystemExit(main())
