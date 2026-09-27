"""Single writer background workflow. Free quotas are reserved in D1 atomically.
Every job is durably queued. Source text lives only in runner memory/temp files;
only normalized public facts, short evidence, links and fingerprints persist.
"""
from __future__ import annotations
import copy, datetime as dt, hashlib, json, os, re, sys, time
from urllib.parse import urlsplit
from cloud import API, ROOT, request_json
from net import Reader, canonical, WORDS, SKIP, ATTACH
from model import PROMPT, VERIFY, plain_json, compile_analysis, all_expired

STAGES=['plan','school','city','district','inspect','reconcile']
LABELS=['\u6392\u7a0b\u8207\u8b80\u53d6\u65e2\u6709\u8cc7\u6599','\u641c\u5c0b\u5b78\u6821\u8207\u65b0\u4f86\u6e90','\u641c\u5c0b\u6236\u7c4d\u5730\u734e\u52a9','\u5f59\u6574\u516c\u544a\u9023\u7d50','\u8b80\u53d6\u5167\u6587\u9644\u4ef6\u8207\u81ea\u52d5\u6574\u7406','\u6838\u5c0d\u689d\u4ef6\u3001\u5165\u5eab\u8207\u5230\u671f\u4e0b\u67b6']

def stamp():return dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')
def norm(s):return re.sub(r'\s+','',str(s)).replace('\u53f0','\u81fa').lower()
def parse_time(s):
    try:
        d=dt.datetime.fromisoformat(s)
        if d.tzinfo is None:d=d.replace(hour=23,minute=59,second=59,tzinfo=dt.timezone(dt.timedelta(hours=8)))
        return d
    except (ValueError,TypeError):return None

def search(api,q):
    if not api.call('/internal/consume',{'type':'search'}).get('allowed'):raise RuntimeError('SEARCH_FREE_QUOTA_REACHED')
    data=request_json('https://api.tavily.com/search','POST',{
      'query':q,'search_depth':'basic','auto_parameters':False,'topic':'general','max_results':6,
      'include_answer':False,'include_raw_content':False
    },os.environ['TAVILY_API_KEY'],timeout=40)
    # Search snippets are not used as qualification or deadline evidence.
    return [{'url':x.get('url',''),'title':x.get('title','')[:240]} for x in data.get('results',[])[:6]]

def school_for(name,schools):
    return next((s for s in schools.get('schools',[]) if norm(name) in [norm(s['name']),*[norm(a) for a in s.get('aliases',[])]]),None)

def plan(context,daily,schools,sources,meta,library):
    term=context.get('term','115-1');year=int(term.split('-')[0]);queries=[];seeds=[]
    if daily:
        topics=['\u57fa\u91d1\u6703 \u734e\u5b78\u91d1 \u7533\u8acb','\u5bae\u5edf \u52a9\u5b78\u91d1','\u6236\u7c4d \u734e\u5b78\u91d1 \u5340\u516c\u6240','\u5927\u5b78 \u6821\u5167 \u734e\u5b78\u91d1','\u79d1\u6280 \u734e\u5b78\u91d1','\u6e05\u5bd2 \u734e\u52a9\u5b78\u91d1','\u9ad8\u4e2d\u8077 \u734e\u5b78\u91d1']
        pos=dt.date.today().toordinal()%len(topics)
        queries=[{'stage':'school','q':f'{year} {year-1} \u734e\u5b78\u91d1 \u7533\u8acb \u8fa6\u6cd5'}]+[{'stage':'city','q':topics[(pos+i)%len(topics)]+f' {year} {year-1}'} for i in range(3)]
        # Registered sources rotate; active known deadlines are checked first.
        available=[s for s in sources['sources'] if s.get('enabled',True) and not s.get('requiresLogin')]
        start=int(meta.get('sourceCursor',0));chosen=[available[(start+i)%len(available)] for i in range(min(12,len(available)))] if available else []
        meta['sourceCursor']=(start+len(chosen))%max(1,len(available))
        seeds=[{'url':s['url'],'title':s['name'],'kind':s.get('sourceType') or ('index' if s.get('discovery') else 'notice'),'depth':0} for s in chosen]
        now=dt.datetime.now(dt.timezone.utc)
        urgent=[]
        for p in library['programs']:
            d=parse_time(p.get('deadline'))
            if d and dt.timedelta(0)<=d-now<=dt.timedelta(days=3):urgent.append({'url':p['sourceUrl'],'title':p['name'],'depth':0})
        seeds=urgent[:4]+seeds
    else:
        school=context.get('schoolName','');city=context.get('city','');district=context.get('district','');resolved=school_for(school,schools)
        if school:
            school=resolved['name'] if resolved else school
            queries.extend([{'stage':'school','q':f'"{school}" \u734e\u52a9\u5b78\u91d1 {year} {year-1}'},{'stage':'school','q':f'"{school}" \u751f\u8f14\u7d44 \u734e\u5b78\u91d1 \u7533\u8acb'}])
        if city:queries.append({'stage':'city','q':f'{city} {district} \u6236\u7c4d \u734e\u5b78\u91d1 {year} {year-1}'})
        if district:queries.append({'stage':'district','q':f'{city} {district} \u57fa\u91d1\u6703 \u5bae\u5edf \u734e\u52a9\u5b78\u91d1'})
        if context.get('noticeName'):queries.insert(0,{'stage':'school','q':context['noticeName']+' \u734e\u5b78\u91d1 \u7533\u8acb'})
        if resolved:
            selected=[s for s in sources['sources'] if s['id'] in resolved.get('sourceIds',[]) and s.get('enabled',True)]
            selected.sort(key=lambda s:s.get('priority',50))
            seeds=[{'url':s['url'],'title':s['name'],'kind':s.get('sourceType') or ('index' if s.get('discovery') else 'notice'),'depth':0} for s in selected[:3]]
        for k in ('noticeUrl','schoolUrl'):
            if context.get(k):seeds.insert(0,{'url':context[k],'title':context.get('noticeName') or school,'depth':0})
    return queries[:4],seeds

def archive(library,now=None):
    now=now or dt.datetime.now(dt.timezone.utc);n=0
    for p in library['programs']:
        if p.get('routes'):expired=all_expired(p['routes'],now)
        else:
            d=parse_time(p.get('deadline'));expired=bool(d and d<now)
        if expired and not p.get('archived'):p['archived']=True;p['archivedAt']=stamp();n+=1
    return n

def replace_entry(library,catalog,program,model):
    url=canonical(program['sourceUrl']);old=next((p for p in library['programs'] if canonical(p['sourceUrl'])==url),None)
    if old:
        program['id']=old['id'];program['programKey']=old.get('programKey',old['id'])
        # Keep source trails, not stale eligibility or deadlines.
        extras=[x for x in old.get('sourceLinks',[]) if x.get('url') not in {z['url'] for z in program['sourceLinks']}]
        program['sourceLinks']=(program['sourceLinks']+extras)[:16]
        old_ids=old.get('modelIds',[])
        other_ids={i for p in library['programs'] if p is not old for i in p.get('modelIds',[])}
        catalog['scholarships']=[s for s in catalog['scholarships'] if s['id'] not in old_ids or s['id'] in other_ids]
        library['programs'][library['programs'].index(old)]=program
    else:library['programs'].append(program)
    if model:
        catalog['scholarships']=[s for s in catalog['scholarships'] if s['id']!=model['id']]+[model]
    return 'updated' if old else 'added'

def pending_entry(page,term,issues):
    aid='auto-'+hashlib.sha256(page.url.encode()).hexdigest()[:20]
    title=page.title or '\u65b0\u734e\u52a9\u516c\u544a'
    return {'id':aid,'programKey':aid,'name':title[:200],'organizer':urlsplit(page.url).hostname,'sourceUrl':page.url,'sourceLabel':urlsplit(page.url).hostname,
      'summary':'\u5df2\u53d6\u5f97\u516c\u544a\uff1b\u8cc7\u683c\u8207\u6587\u4ef6\u6574\u7406\u5c1a\u5f85\u5b8c\u6210\u3002','conditions':issues,'documentsSummary':['\u5b8c\u6574\u6587\u4ef6\u5f85\u6574\u7406'],
      'deadline':None,'start':None,'amountText':'\u5f85\u78ba\u8a8d','scope':'base','schoolIds':[],'levels':[],'regions':[],'period':'\u4f9d\u539f\u516c\u544a','applicationTerms':[],
      'deadlineKind':'official','deadlineNote':'\u671f\u9650\u5f85\u78ba\u8a8d','reviewedAt':stamp(),'verification':'\u5167\u6587\u5df2\u53d6\u5f97\uff0c\u898f\u5b9a\u5f85\u6574\u7406','evidence':'page','reviewState':'partial','modelIds':[],'archived':False,
      'sourceLinks':[{'url':page.url,'label':'\u539f\u516c\u544a','role':'announcement'}],'automation':{'ready':False,'warnings':issues}}

class Job:
    def __init__(self,api,job,max_seconds=780):self.api=api;self.job=job;self.max_seconds=max_seconds;self.stages=[{'key':k,'label':l,'status':'waiting','processed':0,'total':None} for k,l in zip(STAGES,LABELS)];self.started=time.monotonic()
    def progress(self,k,status='running',done=0,total=None,message=''):
        s=next(x for x in self.stages if x['key']==k);s.update(status=status,processed=done,total=total,message=message)
        self.api.call('/internal/job',{'id':self.job['id'],'status':'running','progress':{'stages':self.stages,'elapsedSeconds':round(time.monotonic()-self.started)}})
    def end(self,result,failed=False):
        self.api.call('/internal/job',{'id':self.job['id'],'status':'failed' if failed else 'done','progress':{'stages':self.stages},'result':result})
    def run(self):
        self.progress('plan')
        api=self.api;ctx=self.job['context'];daily=self.job['kind']=='daily';started=stamp()
        library=api.read('library.json');catalog=api.read('catalog.json');sources=api.read('sources.json');schools=api.read('schools.json')
        meta=api.read('runner-meta.json') or {};meta.setdefault('pages',{});meta.setdefault('backlog',[])
        daily_status=api.read('daily-status.json') or {}
        if daily:
            daily_status.update(status='running',lastAttemptAt=started,collectionMode='free-web-discovery');api.write('daily-status.json',daily_status)
        queries,seeds=plan(ctx,daily,schools,sources,meta,library);self.progress('plan','done',1,1)
        errors=[];seen=set();queue=[];candidates=[];readers=Reader();counts={'pagesAttempted':0,'pagesSucceeded':0,'added':0,'updated':0,'analyzed':0,'autoReady':0,'searchesSucceeded':0,'unreadPages':0}
        def enqueue(item):
            try:u=canonical(item['url'])
            except Exception:return
            if u in seen or SKIP.search(item.get('title','')):return
            seen.add(u);queue.append({**item,'url':u})
        # Explicit supplied notices and prioritized known sources stay first.
        for s in seeds:enqueue(s)
        for stage in ('school','city','district'):
            qs=[q for q in queries if q['stage']==stage]
            if not qs:self.progress(stage,'skipped',0,0);continue
            ok=0
            for i,q in enumerate(qs):
                self.progress(stage,'running',i,len(qs))
                try:
                    results=search(api,q['q']);counts['searchesSucceeded']+=1;ok+=1
                    for r in results:enqueue({**r,'depth':0})
                except Exception as e:errors.append({'stage':stage,'code':str(e)[:100]})
            self.progress(stage,'done' if ok==len(qs) else 'partial',len(qs),len(qs))
        # Backlog persists unfinished reads and quota-limited extraction, without user profiles.
        if daily:
            for b in meta['backlog'][:12]:enqueue(b)
        self.progress('inspect','running',0,len(queue));processed=set();limit=18 if daily else 12
        trusted_hosts={urlsplit(s['url']).hostname for s in sources['sources'] if s.get('trust')!='unverified'}
        model_by_id={s['id']:s for s in catalog['scholarships']}
        idx=0;new_backlog=[]
        while idx<len(queue) and counts['pagesAttempted']<limit and time.monotonic()-self.started<self.max_seconds:
            item=queue[idx];idx+=1;u=item['url'];counts['pagesAttempted']+=1;processed.add(u)
            self.progress('inspect','running',counts['pagesAttempted']-1,min(len(queue),limit),'\u8b80\u53d6\u516c\u544a\u8207\u9644\u4ef6')
            oldmeta=meta['pages'].get(u,{})
            try:page=readers.page(u)
            except Exception as e:
                errors.append({'stage':'inspect','code':type(e).__name__,'url':u});oldmeta['lastAttemptAt']=stamp();oldmeta['lastError']=type(e).__name__;meta['pages'][u]=oldmeta
                if int(item.get('retries',0))<2:new_backlog.append({**item,'retries':int(item.get('retries',0))+1})
                continue
            counts['pagesSucceeded']+=1
            # New official/organizer links discovered from directories, at most one link level.
            if item.get('depth',0)<1:
                for l in page.links:
                    if WORDS.search(l['title']) and not SKIP.search(l['title']) and not ATTACH.search(l['url']):enqueue({**l,'depth':1})
            is_index=item.get('kind') in ('index','directory','homepage') or (not WORDS.search(page.title) and len([l for l in page.links if WORDS.search(l['title'])])>=8)
            if is_index:
                meta['pages'][u]={'hash':page.digest,'lastSuccessAt':stamp(),'lastAttemptAt':stamp(),'index':True};continue
            if not WORDS.search(page.title+' '+page.text[:500]) or SKIP.search(page.title):continue
            old=next((p for p in library['programs'] if canonical(p['sourceUrl'])==canonical(page.url)),None)
            old_model=next((model_by_id[i] for i in (old or {}).get('modelIds',[]) if i in model_by_id),None)
            if old and oldmeta.get('hash')==page.digest and (old.get('automation',{}).get('ready') or oldmeta.get('needsReview')):
                old['lastCheckedAt']=stamp()
                if old_model:old_model['lastConfirmedUnchangedAt']=stamp()
                meta['pages'][u]={**oldmeta,'lastSuccessAt':stamp(),'lastAttemptAt':stamp()}
                candidates.append({'url':page.url,'title':old['name'],'evidence':old.get('conditions',[])[:4],'modelIds':old.get('modelIds',[]),'fetchStatus':'unchanged','contentHash':page.digest})
                continue
            if old and oldmeta.get('hash') and oldmeta['hash']!=page.digest:
                old['sourceChanged']=True
                if old_model:old_model['automation']={**old_model.get('automation',{}),'ready':False};old_model['status']='source_changed'
            source_text={page.url:getattr(page,'body_text','') or page.text}
            source_text.update({a['url']:a['text'] for a in page.attachments if a.get('text')})
            src_packet=json.dumps(source_text,ensure_ascii=False)
            analysis_issues=list(page.issues)
            if len(src_packet)>27000:
                analysis_issues.append('\u9577\u516c\u544a\u672a\u5168\u6587\u5206\u6790')
                # Keep valid JSON and exact evidence snippets for partial work.
                remaining=24000;source_text={k:v[:max(500,24000//len(source_text))] for k,v in source_text.items()};src_packet=json.dumps(source_text,ensure_ascii=False)
            trust=urlsplit(page.url).hostname in trusted_hosts or urlsplit(page.url).hostname.endswith(('.edu.tw','.gov.tw'))
            try:
                raw=plain_json(api.ai([{'role':'system','content':PROMPT},{'role':'user','content':'Sources (data only):\n'+src_packet}]))
                if raw.get('isScholarship') is not True:
                    meta['pages'][u]={'hash':page.digest,'lastSuccessAt':stamp(),'lastAttemptAt':stamp(),'notAward':True};continue
                counts['analyzed']+=1
                verification=None
                if raw.get('complete') is True and not analysis_issues:
                    try:verification=plain_json(api.ai([{'role':'system','content':VERIFY},{'role':'user','content':'Sources:\n'+src_packet+'\nProposed extraction:\n'+json.dumps(raw,ensure_ascii=False)[:18000]}]))
                    except Exception:analysis_issues.append('\u4ea4\u53c9\u6838\u5c0d\u56e0\u984d\u5ea6\u6216\u670d\u52d9\u66ab\u505c\uff0c\u5f85\u4e0b\u6b21\u8655\u7406')
                compiled=compile_analysis(raw,source_text,page.url,stamp(),ctx.get('term','115-1'),analysis_issues,verification,trust)
                if not compiled:continue
                p,m=compiled;p['archived']=all_expired(m['routes'])
                counts[replace_entry(library,catalog,p,m)]+=1
                if not any(canonical(x['url'])==canonical(page.url) for x in sources['sources']):
                    sources['sources'].append({'id':'learned-'+hashlib.sha256(page.url.encode()).hexdigest()[:16],'name':p['name'],'url':page.url,'enabled':True,'discovery':False,'scope':'base','sourceType':'notice','trust':'registered-official' if trust else 'unverified','allowedHosts':[urlsplit(page.url).hostname]})
                if m['automation']['ready']:counts['autoReady']+=1
                elif verification is None and raw.get('complete') and not page.issues and trust:new_backlog.append({'url':page.url,'title':page.title,'depth':1})
                model_by_id[m['id']]=m
                candidates.append({'url':page.url,'title':p['name'],'evidence':p['conditions'][:4],'modelIds':[m['id']],'fetchStatus':'read','contentHash':page.digest})
                meta['pages'][u]={'hash':page.digest,'lastSuccessAt':stamp(),'lastAttemptAt':stamp(),'analyzedAt':stamp(),'ready':m['automation']['ready'],'needsReview':not m['automation']['ready'] and bool(verification or page.issues or not raw.get('complete') or not trust)}
            except Exception as e:
                errors.append({'stage':'inspect','code':'ANALYSIS_'+str(e)[:70],'url':u})
                new_backlog.append({'url':page.url,'title':page.title,'depth':1})
                meta['pages'][u]={**oldmeta,'hash':page.digest,'lastSuccessAt':stamp(),'lastAttemptAt':stamp(),'ready':False}
                if old:
                    # Preserve prior structured facts, but never leave a changed item confirmed.
                    old['reviewNote']='\u516c\u544a\u81ea\u52d5\u5206\u6790\u5c1a\u672a\u5b8c\u6210'
                    if old_model and oldmeta.get('hash')!=page.digest:old_model['status']='source_changed';old_model['automation']={**old_model.get('automation',{}),'ready':False}
                elif WORDS.search(page.title) and re.search(r'\u7533\u8acb|\u8cc7\u683c|\u622a\u6b62',page.text):
                    p=pending_entry(page,ctx.get('term','115-1'),['\u81ea\u52d5\u6574\u7406\u7b49\u5f85\u984d\u5ea6\u6216\u9700\u6838\u5c0d\u9644\u4ef6']);counts[replace_entry(library,catalog,p,None)]+=1
        remaining=queue[idx:]
        prior=[b for b in meta['backlog'] if b['url'] not in processed]
        unique={}
        for b in new_backlog+remaining+prior:
            unique.setdefault(b['url'],{'url':b['url'],'title':b.get('title','')[:240],'depth':b.get('depth',1),'retries':b.get('retries',0)})
        meta['backlog']=list(unique.values())[:400]
        counts['unreadPages']=len(remaining);counts['awaitingAnalysis']=len(meta['backlog'])
        self.progress('inspect','done' if not errors else 'partial',counts['pagesAttempted'],counts['pagesAttempted'])
        self.progress('reconcile','running',0,1)
        counts['archived']=archive(library)
        # Public output contains facts and small evidence strings, not full source bodies.
        library['lastCollectionAt']=stamp();catalog['generatedAt']=stamp();catalog['lastCrawlAt']=stamp()
        if counts['pagesSucceeded']:library['lastPublishedAt']=stamp()
        source_history=[]
        for src in sources['sources']:
            info=meta['pages'].get(canonical(src['url']))
            if info:source_history.append({'id':src['id'],'url':src['url'],'checkedAt':info.get('lastAttemptAt'),'status':'failed' if info.get('lastError') and info.get('lastAttemptAt')!=info.get('lastSuccessAt') else 'ok','message':'Machine fetch, not manual qualification review'})
        api.write('crawl-status.json',{'schemaVersion':2,'lastRunAt':stamp(),'trigger':self.job['kind'],'ok':counts['pagesSucceeded'],'total':counts['pagesAttempted'],'sources':source_history[-300:]})
        for name,data in [('catalog.json',catalog),('library.json',library),('sources.json',sources),('runner-meta.json',meta)]:api.write(name,data)
        self.progress('reconcile','done',1,1)
        outcome='completed' if not errors and not remaining and not new_backlog else 'partial'
        result={'context':ctx,'outcome':outcome,'candidates':candidates[:24],'stats':counts,'errors':errors[:20],'progress':{'stages':self.stages},'completedAt':stamp(),
          'notice':'\u5df2\u5b8c\u6210\u672c\u6279\u6b21\u641c\u5c0b\uff0c\u4e0d\u4ee3\u8868\u5df2\u641c\u904d\u5168\u7db2\u3002'}
        if daily:
            daily_status.update(status='ok' if outcome=='completed' else 'partial',lastAttemptAt=started,lastFinishedAt=stamp(),counts=counts,errors=errors[:20],quotaPolicy='stop, never auto-upgrade',lastPublishedAt=stamp() if counts['added']+counts['updated'] else daily_status.get('lastPublishedAt'))
            if outcome=='completed':daily_status['lastSuccessfulAt']=stamp()
            api.write('daily-status.json',daily_status)
        self.end(result)
        print(json.dumps({'kind':self.job['kind'],'outcome':outcome,'counts':counts}))
        return result

def verify(api):
    checks=[]
    def record(name,fn):
        try:detail=fn();checks.append({'name':name,'ok':True,'detail':detail})
        except Exception as e:checks.append({'name':name,'ok':False,'detail':str(e)[:140]})
    record('Database read',lambda:bool(api.read('library.json').get('programs')) or (_ for _ in ()).throw(RuntimeError('Empty library')))
    def storage():
        m=api.read('runner-meta.json') or {};m['verificationNonce']=hashlib.sha256(stamp().encode()).hexdigest();api.write('runner-meta.json',m)
        if api.read('runner-meta.json')['verificationNonce']!=m['verificationNonce']:raise RuntimeError('Write/read mismatch')
        return 'Persisted'
    record('Database write',storage)
    results=[]
    def live_search():results.extend(search(api,'\u6559\u80b2\u90e8 \u5713\u5922\u52a9\u5b78\u7db2 \u734e\u5b78\u91d1'));return f'{len(results)} public links'
    record('Live Tavily search',live_search)
    def live_ai():
        r=plain_json(api.ai([{'role':'system','content':'Reply only valid JSON.'},{'role':'user','content':'Return {"ok":true}'}]))
        if r.get('ok') is not True:raise RuntimeError('AI output check failed')
        return 'Real Workers AI response'
    record('Live Workers AI',live_ai)
    def live_page():
        reader=Reader();urls=[x['url'] for x in results if urlsplit(x['url']).hostname.endswith(('.edu.tw','.gov.tw'))][:2]+['https://www.edu.tw/helpdreams/']
        for u in urls:
            try:
                p=reader.page(u,max_attachments=0)
                if len(p.text)>40:return 'At least one official page read'
            except Exception:pass
        raise RuntimeError('No official source readable: check robots/network; API may still work')
    record('Live official source',live_page)
    def trigger():
        r=api.call('/internal/dispatch-test',{})
        if not r.get('ok'):raise RuntimeError('Actions trigger failed: check token/expiry/repository')
        return 'GitHub accepted a queue workflow'
    record('GitHub job dispatch',trigger)
    report={'at':stamp(),'ok':all(c['ok'] for c in checks),'checks':checks}
    api.call('/internal/diagnostics',report)
    print(json.dumps(report,ensure_ascii=True,indent=2))
    if os.getenv('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'],'a') as f:
            f.write('## Live verification\n\n'+'\n'.join(('- PASS ' if c['ok'] else '- FAIL ')+c['name']+': '+str(c['detail']) for c in checks)+'\n')
    return report['ok']

def main():
    api=API();mode=os.getenv('JOB_MODE','queue')
    if mode=='verify':return 0 if verify(api) else 1
    if mode in ('daily','scheduled'):api.call('/internal/daily',{'force':mode=='daily'})
    start=time.monotonic();count=0
    while time.monotonic()-start<26*60 and count<8:
        claimed=api.call('/internal/claim',{})
        if not claimed.get('job'):break
        j=Job(api,claimed['job'],max_seconds=max(30,min(780,26*60-(time.monotonic()-start)-30)))
        try:j.run()
        except Exception as e:
            j.end({'message':'\u80cc\u666f\u8655\u7406\u672a\u5b8c\u6210\uff0c\u8acb\u7a0d\u5f8c\u91cd\u8a66\u3002','code':type(e).__name__},True)
            print('JOB_FAILED:',type(e).__name__)
        count+=1
    # Any remaining queued jobs survive and get another free workflow, not lost.
    api.call('/internal/continue',{})
    return 0
if __name__=='__main__':
    try:sys.exit(main())
    except Exception as e:print('RUNNER_SETUP_FAILED:',str(e));sys.exit(1)
