/** Free control plane. Applicant grades/identities are never accepted here.
 * Heavy fetching and parsing run in the private execution environment of Actions.
 * Public workflows receive only mode=queue, never a user's school/domicile.
 */
const PUBLIC_DATA = new Set(['catalog.json','library.json','sources.json','schools.json','crawl-status.json','daily-status.json','queue.json','discovered.json','school-cache.json','base-status.json']);
const PRIVATE_DATA = new Set([...PUBLIC_DATA,'runner-meta.json']);
const DAY=86400;
const stamp=()=>Math.floor(Date.now()/1000);
const iso=()=>new Date().toISOString();
const json=(x,status=200)=>new Response(JSON.stringify(x),{status,headers:{'Content-Type':'application/json; charset=utf-8','Cache-Control':'no-store','X-Content-Type-Options':'nosniff','Referrer-Policy':'no-referrer'}});
const err=(code,status=400)=>json({error:code},status);
export async function digest(s){const b=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(s));return [...new Uint8Array(b)].map(x=>x.toString(16).padStart(2,'0')).join('');}
export function taipeiDate(ms=Date.now()){return new Date(ms+8*3600e3).toISOString().slice(0,10);}
export function currentTerm(ms=Date.now()){const d=new Date(ms+8*3600e3),m=d.getUTCMonth()+1,y=d.getUTCFullYear()-1911;return `${m>=8?y:y-1}-${m>=2&&m<8?2:1}`;}
export function normalizeContext(o){
 const keys=['schoolName','term','city','district','schoolUrl','noticeName','noticeUrl','gradeTerms'];
 if(!o||Array.isArray(o)||Object.keys(o).some(k=>!keys.includes(k)))throw Error('Only school and public-notice search fields are accepted');
 const r={};for(const k of keys.filter(x=>x!=='gradeTerms')){const s=o[k]??'';if(typeof s!=='string'||s.length>(k.endsWith('Url')?500:100)||/[<>\x00-\x1f]/.test(s))throw Error('Invalid search text');r[k]=s.trim();}
 r.term=r.term||currentTerm();if(!/^\d{2,3}-[12]$/.test(r.term))throw Error('Invalid term');
 if(r.district&&(!r.city||! /^[\u3400-\u9fffA-Za-z ]{1,20}$/.test(r.district)))throw Error('Use a district, not a full address');
 if(r.city&&!/^[\u3400-\u9fff]{2,4}[\u5e02\u7e23]$/.test(r.city))throw Error('Invalid city');
 for(const k of ['schoolUrl','noticeUrl'])if(r[k]){const u=new URL(r[k]);if(!['https:','http:'].includes(u.protocol)||u.username||u.password||u.port||!u.hostname.includes('.')||/[\[\]:]/.test(u.hostname)||/^(?:\d+\.){3}\d+$/.test(u.hostname))throw Error('Use a public website URL');}
 if(!r.schoolName&&!r.city&&!r.noticeName&&!r.noticeUrl)throw Error('Enter a school or city');
 if(o.gradeTerms!==undefined){if(!Array.isArray(o.gradeTerms)||o.gradeTerms.length>8||o.gradeTerms.some(t=>typeof t!=='string'||!/^\d{2,3}-[12]$/.test(t)))throw Error('Invalid grade periods');r.gradeTerms=o.gradeTerms;}
 return r;
}
async function limitedText(request,max){
 const n=Number(request.headers.get('content-length')||0);if(n>max)throw Error('Payload too large');
 if(!request.body)return '';const reader=request.body.getReader(),chunks=[];let size=0;
 while(true){const {done,value}=await reader.read();if(done)break;size+=value.length;if(size>max){await reader.cancel();throw Error('Payload too large');}chunks.push(value);}
 const bytes=new Uint8Array(size);let offset=0;for(const v of chunks){bytes.set(v,offset);offset+=v.length;}return new TextDecoder().decode(bytes);
}
async function body(request,max=16000){return JSON.parse(await limitedText(request,max)||'{}');}
async function setting(env,key,value){if(value===undefined){return (await env.DB.prepare('SELECT value FROM settings WHERE key=?').bind(key).first())?.value;}await env.DB.prepare('INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value').bind(key,String(value)).run();}
async function dataset(env,name){const r=await env.DB.prepare('SELECT body FROM datasets WHERE name=?').bind(name).first();return r?.body;}
async function take(env,key,max,ttl=DAY){
 const r=await env.DB.prepare('INSERT INTO counters(key,n,expires) VALUES(?,1,?) ON CONFLICT(key) DO UPDATE SET n=n+1 WHERE n<? RETURNING n').bind(key,stamp()+ttl,max).first();return !!r;
}
export async function consume(env,type){
 if(type==='search'){
  const utc=new Date().toISOString();if(!await take(env,'search-month:'+utc.slice(0,7),Math.min(900,Number(env.SEARCH_MONTHLY_LIMIT)||900),40*DAY))return false;
  return take(env,'search-day:'+utc.slice(0,10),Math.min(30,Number(env.SEARCH_DAILY_LIMIT)||30),2*DAY);
 }
 if(type==='ai')return take(env,'ai:'+new Date().toISOString().slice(0,10),Math.min(16,Number(env.AI_DAILY_LIMIT)||16),2*DAY);
 return false;
}
async function authorized(request,env){if(!env.RUNNER_TOKEN||env.RUNNER_TOKEN.length<32)return false;const sent=request.headers.get('authorization')||'';return await digest(sent)===await digest('Bearer '+env.RUNNER_TOKEN);}
async function dispatch(env){
 if(!env.GH_DISPATCH_TOKEN||!env.GITHUB_REPO)return {ok:false,message:'GITHUB_DISPATCH_NOT_CONFIGURED'};
 try{const r=await fetch(`https://api.github.com/repos/${env.GITHUB_REPO}/actions/workflows/collect.yml/dispatches`,{method:'POST',redirect:'error',headers:{'Authorization':'Bearer '+env.GH_DISPATCH_TOKEN,'Accept':'application/vnd.github+json','User-Agent':'Scholarship-Free/14','X-GitHub-Api-Version':'2022-11-28','Content-Type':'application/json'},body:JSON.stringify({ref:env.GITHUB_BRANCH||'main',inputs:{mode:'queue'}}),signal:AbortSignal.timeout(8000)});
 const result={ok:r.status===204,status:r.status,at:iso()};await setting(env,'dispatch',JSON.stringify(result));return result;
 }catch{const x={ok:false,message:'DISPATCH_CONNECTION_FAILED',at:iso()};await setting(env,'dispatch',JSON.stringify(x));return x;}
}
async function cleanup(env){
 await env.DB.batch([
 env.DB.prepare('DELETE FROM counters WHERE expires<?').bind(stamp()),
 env.DB.prepare('DELETE FROM jobs WHERE created<?').bind(stamp()-DAY),
 env.DB.prepare("UPDATE jobs SET status='queued',updated=? WHERE status='running' AND updated<? AND attempts<2").bind(stamp(),stamp()-1200),
 env.DB.prepare("UPDATE jobs SET status='failed',result=?,updated=? WHERE status='running' AND updated<? AND attempts>=2").bind(JSON.stringify({message:'Runner interrupted twice; please retry later.'}),stamp(),stamp()-1200)
 ]);
}
async function daily(env,force=false){
 const date=taipeiDate(),key='daily:'+date+(force?':'+crypto.randomUUID():'');const id=(await digest(key)).slice(0,32);
 await env.DB.prepare("INSERT OR IGNORE INTO jobs(id,cache_key,kind,context,status,created,updated) VALUES(?,?,'daily',?,'queued',?,?)").bind(id,key,JSON.stringify({date,term:currentTerm()}),stamp(),stamp()).run();return id;
}
async function status(env){
 const dailyRaw=await dataset(env,'daily-status.json');let last={};try{last=JSON.parse(dailyRaw||'{}');}catch{}
 return {appVersion:14,schemaVersion:6,discovery:true,searchConfigured:env.SEARCH_CONFIGURED==='true'&&!!env.GH_DISPATCH_TOKEN,
 scheduler:{enabled:!!env.GH_DISPATCH_TOKEN,timezone:'Asia/Taipei',localTime:'12:00',execution:'GitHub Actions; may queue or delay'},
 currentTerm:currentTerm(),storage:{type:'D1',mounted:true},daily:last,dispatch:JSON.parse(await setting(env,'dispatch')||'{}'),
 diagnostics:JSON.parse(await setting(env,'diagnostics')||'{}'),lastRunnerAt:await setting(env,'runner')||null,limits:{searchDaily:30,searchMonthly:900,aiDaily:16},
 note:'Configuration is not a successful live search. Check lastRunnerAt and daily results.'};
}
async function handle(request,env){
 const u=new URL(request.url),p=u.pathname;
 if(p==='/healthz')return json({ok:true,version:14});
 if(p==='/api/status'&&request.method==='GET')return json(await status(env));
 if(p.startsWith('/data/')&&request.method==='GET'){
  const name=p.slice(6);if(!PUBLIC_DATA.has(name))return err('Not found',404);
  const raw=await dataset(env,name);return raw?new Response(raw,{headers:{'Content-Type':'application/json; charset=utf-8','Cache-Control':'no-cache','X-Content-Type-Options':'nosniff'}}):env.ASSETS.fetch(request);
 }
 if(p==='/api/discovery'&&request.method==='POST'){
  if(request.headers.get('origin')!==u.origin)return err('Same-origin requests only',403);
  if(!request.headers.get('content-type')?.startsWith('application/json'))return err('JSON required',415);
  const context=normalizeContext(await body(request));
  const normalized={...context,schoolName:context.schoolName.normalize('NFKC').replaceAll('\u53f0','\u81fa'),gradeTerms:[...(context.gradeTerms||[])].sort()};
  const key=await digest(JSON.stringify(normalized));
  const old=await env.DB.prepare("SELECT id,status,created,result FROM jobs WHERE cache_key=? AND created>? AND status IN ('queued','running','done') ORDER BY created DESC LIMIT 1").bind(key,stamp()-6*3600).first();
  // Exact context must be preserved for the frontend's stale-response guard.
  if(old){const oldFull=await env.DB.prepare('SELECT context FROM jobs WHERE id=?').bind(old.id).first();if(oldFull?.context===JSON.stringify(context)){
   const result=old.result?JSON.parse(old.result):null;
   if(old.status!=='done'||result?.outcome==='completed')return json({jobId:old.id,cached:old.status==='done'},202);
  }}
  const ip=request.headers.get('CF-Connecting-IP')||'local';const h=await digest(env.RUNNER_TOKEN+':'+taipeiDate()+':'+ip);
  if(!await take(env,'ip-minute:'+h+':'+Math.floor(stamp()/60),3,120)||!await take(env,'ip-day:'+h,6,2*DAY))return err('\u67e5\u8a62\u8f03\u983b\u7e41\uff0c\u8acb\u7a0d\u5f8c\u518d\u8a66\u3002',429);
  if(!await take(env,'jobs:'+taipeiDate(),Math.min(12,Number(env.JOBS_DAILY_LIMIT)||12),2*DAY))return err('\u4eca\u65e5\u514d\u8cbb\u88dc\u67e5\u984d\u5ea6\u5df2\u7528\u5b8c\uff1b\u73fe\u6709\u8cc7\u6599\u4ecd\u53ef\u67e5\u95b1\u3002',429);
  const id=crypto.randomUUID().replaceAll('-','');
  const progress={stages:[{key:'plan',label:'\u7b49\u5f85\u514d\u8cbb\u80cc\u666f\u5de5\u4f5c\u555f\u52d5',status:'running',processed:0,total:1}]};
  await env.DB.prepare("INSERT INTO jobs(id,cache_key,kind,context,status,progress,created,updated) VALUES(?,?,'lookup',?,'queued',?,?,?)").bind(id,key,JSON.stringify(context),JSON.stringify(progress),stamp(),stamp()).run();
  const sent=await dispatch(env);if(!sent.ok){await env.DB.prepare("UPDATE jobs SET status='failed',result=? WHERE id=?").bind(JSON.stringify({message:'\u80cc\u666f\u6392\u7a0b\u672a\u6210\u529f\u555f\u52d5\uff0c\u8acb\u7ba1\u7406\u8005\u6aa2\u67e5 GitHub \u91d1\u9470\u8207 Actions\u3002'}),id).run();}
  return json({jobId:id,queued:true},202);
 }
 if(p.startsWith('/api/jobs/')&&request.method==='GET'){
  const id=p.slice(10);if(!/^[a-f0-9]{32}$/.test(id))return err('Invalid ID');
  const row=await env.DB.prepare('SELECT status,progress,result,created FROM jobs WHERE id=? AND created>?').bind(id,stamp()-DAY).first();
  if(!row)return err('Job expired; please search again.',404);const result=row.result?JSON.parse(row.result):null;
  return json({status:row.status,progress:JSON.parse(row.progress),result,message:result?.message||'',queuedSeconds:stamp()-row.created});
 }
 if(p.startsWith('/internal/')){
  if(!await authorized(request,env))return err('Unauthorized',401);
  if(p==='/internal/dispatch-test'&&request.method==='POST')return json(await dispatch(env));
  if(p==='/internal/diagnostics'&&request.method==='POST'){const b=await body(request);await setting(env,'diagnostics',JSON.stringify(b));return json({ok:true});}
  if(p==='/internal/continue'&&request.method==='POST'){const j=await env.DB.prepare("SELECT id FROM jobs WHERE status='queued' LIMIT 1").first();return json(j?await dispatch(env):{ok:true,empty:true});}
  if(p==='/internal/daily'&&request.method==='POST'){await cleanup(env);const b=await body(request);return json({id:await daily(env,b.force===true)});}
  if(p==='/internal/claim'&&request.method==='POST'){
   await cleanup(env);await setting(env,'runner',iso());
   const row=await env.DB.prepare("UPDATE jobs SET status='running',attempts=attempts+1,updated=? WHERE id=(SELECT id FROM jobs WHERE status='queued' ORDER BY CASE kind WHEN 'lookup' THEN 0 ELSE 1 END,created LIMIT 1) RETURNING *").bind(stamp()).first();
   return json(row?{job:{...row,context:JSON.parse(row.context),progress:JSON.parse(row.progress)}}:{job:null});
  }
  if(p==='/internal/job'&&request.method==='POST'){
   const b=await body(request,650000);if(!/^[a-f0-9]{32}$/.test(b.id)||!['running','done','failed'].includes(b.status))return err('Invalid update');
   await env.DB.prepare('UPDATE jobs SET status=?,progress=?,result=?,updated=? WHERE id=?').bind(b.status,JSON.stringify(b.progress||{}),b.result?JSON.stringify(b.result):null,stamp(),b.id).run();return json({ok:true});
  }
  if(p.startsWith('/internal/dataset/')){
   const name=p.slice(18);if(!PRIVATE_DATA.has(name))return err('Not found',404);
   if(request.method==='GET')return new Response(await dataset(env,name)||'null',{headers:{'Content-Type':'application/json','Cache-Control':'no-store'}});
   if(request.method==='PUT'){const text=await limitedText(request,5_000_000);await env.DB.prepare('INSERT INTO datasets(name,body,updated) VALUES(?,?,?) ON CONFLICT(name) DO UPDATE SET body=excluded.body,updated=excluded.updated').bind(name,text,stamp()).run();return json({ok:true});}
  }
  if(p==='/internal/consume'&&request.method==='POST'){const b=await body(request);return json({allowed:await consume(env,b.type)});}
  if(p==='/internal/ai'&&request.method==='POST'){
   const b=await body(request,100000);if(!Array.isArray(b.messages)||b.messages.length>3||JSON.stringify(b.messages).length>48000)return err('Invalid AI payload');
   if(!await consume(env,'ai'))return err('AI_FREE_DAILY_LIMIT',429);
   try{const result=await env.AI.run('@cf/meta/llama-3.3-70b-instruct-fp8-fast',{messages:b.messages,max_tokens:2400,temperature:0,response_format:{type:'json_object'}});return json({result});}
   catch{return err('AI_UNAVAILABLE_OR_FREE_QUOTA_REACHED',503);}
  }
  return err('Not found',404);
 }
 if(p.startsWith('/api/'))return err('Not found',404);
 return env.ASSETS.fetch(request);
}
export default {
 async fetch(request,env){try{return await handle(request,env);}catch(e){return err(e.message==='Payload too large'?e.message:'Request failed; check deployment configuration.',e.message==='Payload too large'?413:400);}},
 async scheduled(controller,env,ctx){await cleanup(env);await daily(env);await dispatch(env);}
};
