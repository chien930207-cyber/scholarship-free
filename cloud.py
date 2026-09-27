"""Cloud API helpers. Never print a token, applicant context or source body."""
from __future__ import annotations
import hashlib, json, os, time, urllib.error, urllib.request
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*a,**k):raise ValueError('API redirect denied')

def request_json(url,method='GET',payload=None,token='',timeout=90):
    data=None if payload is None else json.dumps(payload,ensure_ascii=False).encode()
    headers={'Accept':'application/json','User-Agent':'ScholarshipFree/14'}
    if data is not None:headers['Content-Type']='application/json'
    if token:headers['Authorization']='Bearer '+token
    req=urllib.request.Request(url,data=data,headers=headers,method=method)
    try:
        with urllib.request.build_opener(NoRedirect()).open(req,timeout=timeout) as r:
            raw=r.read(6_000_001)
            if len(raw)>6_000_000:raise RuntimeError('API response too large')
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        # No bodies in public Actions logs: might contain a query or authorization detail.
        raise RuntimeError(f'API_HTTP_{e.code}') from None

def runner_token():
    key=os.environ['CLOUDFLARE_API_TOKEN'];repo=os.environ['GITHUB_REPOSITORY']
    return hashlib.sha256(('scholarship-free-runner-v14\0'+repo+'\0'+key).encode()).hexdigest()

def cf(path,method='GET',payload=None):
    a=os.environ['CLOUDFLARE_ACCOUNT_ID']
    x=request_json('https://api.cloudflare.com/client/v4/accounts/'+a+path,method,payload,os.environ['CLOUDFLARE_API_TOKEN'])
    if not x.get('success'):raise RuntimeError('CLOUDFLARE_API_FAILED: check account/token permissions')
    return x.get('result')

def site_url():
    if os.getenv('SITE_URL'):return os.environ['SITE_URL'].rstrip('/')
    x=cf('/workers/subdomain');sub=x.get('subdomain')
    if not sub:raise RuntimeError('WORKERS_DEV_SUBDOMAIN_MISSING: set a workers.dev subdomain in Cloudflare first')
    return 'https://scholarship-free.'+sub+'.workers.dev'

class API:
    def __init__(self,url=None,token=None):self.url=url or site_url();self.token=token or runner_token()
    def call(self,path,payload=None,method=None):return request_json(self.url+path,method or ('POST' if payload is not None else 'GET'),payload,self.token)
    def read(self,name):return self.call('/internal/dataset/'+name)
    def write(self,name,data):return self.call('/internal/dataset/'+name,data,'PUT')
    def ai(self,messages):return self.call('/internal/ai',{'messages':messages})
