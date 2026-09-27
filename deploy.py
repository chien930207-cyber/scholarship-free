"""One supported deployment. Idempotent D1 schema/seed; never resets live data."""
from __future__ import annotations
import hashlib, json, os, re, subprocess, sys, time
from pathlib import Path
from cloud import ROOT, cf, runner_token, request_json

def required():
    for k in ['CLOUDFLARE_ACCOUNT_ID','CLOUDFLARE_API_TOKEN','TAVILY_API_KEY','GH_DISPATCH_TOKEN','GITHUB_REPOSITORY']:
        if not os.getenv(k):raise RuntimeError('Missing GitHub Actions secret: '+k)
    if not re.fullmatch(r'[a-fA-F0-9]{32}',os.environ['CLOUDFLARE_ACCOUNT_ID']):raise RuntimeError('Account ID must be 32 hex characters, not email or Zone ID')
    if not os.environ['TAVILY_API_KEY'].startswith('tvly-'):raise RuntimeError('TAVILY_API_KEY should begin tvly-')
    if os.environ.get('GITHUB_REPOSITORY_VISIBILITY','public')!='public':raise RuntimeError('This zero-cost workflow requires a PUBLIC repository')

def deploy():
    required()
    try:sub=cf('/workers/subdomain').get('subdomain')
    except RuntimeError as e:
        if '404' not in str(e):raise
        sub=None
    if not sub:
        proposed='scholar-'+hashlib.sha256(os.environ['CLOUDFLARE_ACCOUNT_ID'].encode()).hexdigest()[:12]
        sub=cf('/workers/subdomain','PUT',{'subdomain':proposed}).get('subdomain')
    if not sub:raise RuntimeError('Workers subdomain not ready; open Workers & Pages and set Your subdomain, then retry.')
    dbs=cf('/d1/database?per_page=100');db=next((d for d in dbs if d['name']=='scholarship-free-data'),None)
    if not db:db=cf('/d1/database','POST',{'name':'scholarship-free-data'})
    did=db.get('uuid') or db.get('id')
    if not did:raise RuntimeError('D1 database ID missing')
    schema=(ROOT/'worker/schema.sql').read_text()
    cf('/d1/database/'+did+'/query','POST',{'sql':schema})
    # Preserve live rows on each redeploy; seed only absent datasets.
    for p in (ROOT/'web/data').glob('*.json'):
        if p.name not in {'catalog.json','library.json','sources.json','schools.json','crawl-status.json','daily-status.json','queue.json','discovered.json','school-cache.json','base-status.json'}:continue
        text=p.read_text();json.loads(text)
        cf('/d1/database/'+did+'/query','POST',{'sql':'INSERT OR IGNORE INTO datasets(name,body,updated) VALUES(?,?,?)','params':[p.name,text,int(time.time())]})
    config=json.loads((ROOT/'worker/config.template.json').read_text());config['d1_databases'][0]['database_id']=did
    config['vars']['GITHUB_REPO']=os.environ['GITHUB_REPOSITORY'];config['vars']['GITHUB_BRANCH']='main'
    (ROOT/'wrangler.json').write_text(json.dumps(config,indent=2))
    subprocess.run(['npx','wrangler','deploy','--config','wrangler.json'],cwd=ROOT,check=True)
    # Stream secrets via stdin; no file, argv or logs containing secrets.
    secrets={'RUNNER_TOKEN':runner_token(),'GH_DISPATCH_TOKEN':os.environ['GH_DISPATCH_TOKEN']}
    subprocess.run(['npx','wrangler','secret','bulk','--config','wrangler.json'],input=json.dumps(secrets),text=True,cwd=ROOT,check=True)
    url='https://scholarship-free.'+sub+'.workers.dev'
    for i in range(10):
        try:
            x=request_json(url+'/api/status')
            if x.get('appVersion')==14:break
        except Exception:pass
        time.sleep(3)
    else:raise RuntimeError('Deployment published but health check not ready; retry in dashboard')
    msg=f'## Website deployed\n\n**Open your website:** {url}\n\n**Setup check:** {url}/check.html\n\nNext: Actions -> Collect and verify -> Run workflow -> mode=verify.\nThis deploy did not prove that live search/AI works.\n'
    if os.getenv('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'],'a') as f:f.write(msg)
    print('DEPLOYED:',url)
if __name__=='__main__':
    try:deploy()
    except Exception as e:print('SETUP FAILED:',str(e));sys.exit(1)
