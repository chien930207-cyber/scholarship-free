"""Rendered UI with simulated Storage; local HTTP bridged due managed navigation restrictions. External providers are fixtures, NOT live API tests."""
import contextlib,http.server,json,threading,time,unittest,urllib.request,urllib.error
from pathlib import Path
from playwright.sync_api import sync_playwright
ROOT=Path(__file__).resolve().parents[1];WEB=ROOT/'web';FIX=json.loads((ROOT/'tests/compiled-fixture.json').read_text())
LOG=[];JOB={'polls':0,'payload':None,'mode':'ok'}
class Handler(http.server.SimpleHTTPRequestHandler):
 def __init__(self,*a,**k):super().__init__(*a,directory=str(WEB),**k)
 def log_message(self,*args):pass
 def sendjson(self,x,status=200):
  b=json.dumps(x,ensure_ascii=False).encode();self.send_response(status);self.send_header('Content-Type','application/json');self.send_header('Cache-Control','no-store');self.end_headers();self.wfile.write(b)
 def do_POST(self):
  if self.path=='/api/discovery':
   JOB['payload']=json.loads(self.rfile.read(int(self.headers['Content-Length'])));JOB['polls']=0
   if JOB['mode']=='quota':return self.sendjson({'error':'\u4eca\u65e5\u514d\u8cbb\u984d\u5ea6\u5df2\u7528\u5b8c'},429)
   return self.sendjson({'jobId':'a'*32},202)
  self.send_error(404)
 def do_GET(self):
  path=self.path.split('?')[0]
  if path=='/api/status':return self.sendjson({'appVersion':14,'schemaVersion':6,'discovery':True,'searchConfigured':True,'scheduler':{'enabled':True}})
  if path.startswith('/api/jobs/'):
   JOB['polls']+=1
   if JOB['polls']==1:return self.sendjson({'status':'queued','progress':{'stages':[{'key':'plan','status':'running'}]}})
   aid=FIX['autoId'];return self.sendjson({'status':'done','result':{'context':JOB['payload'],'outcome':'completed','candidates':[{'url':'https://test.example.edu.tw/award','title':'\u6e2c\u8a66\u734e\u5b78\u91d1','modelIds':[aid],'evidence':[]}],'stats':{'pagesSucceeded':1,'pagesAttempted':1,'added':1,'autoReady':1},'progress':{'stages':[{'key':k,'status':'done','processed':1,'total':1} for k in ['plan','school','city','district','inspect','reconcile']]}}})
  if path=='/data/catalog.json':return self.sendjson(FIX['catalog'])
  if path=='/data/library.json':return self.sendjson(FIX['library'])
  return super().do_GET()
class BrowserTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler);threading.Thread(target=cls.server.serve_forever,daemon=True).start();cls.url='http://127.0.0.1:'+str(cls.server.server_port)
  cls.pw=sync_playwright().start();cls.browser=cls.pw.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox']);cls.context=cls.browser.new_context(viewport={'width':1440,'height':1000});cls.errors=[];cls.load_page({})
 @classmethod
 def load_page(cls,saved):
  cls.page=cls.context.new_page();cls.page.on('pageerror',lambda e:cls.errors.append(str(e)))
  def bridge(source,payload):
   req=urllib.request.Request(cls.url+'/'+payload['path'].lstrip('/'),data=payload.get('body','').encode() if payload.get('method')=='POST' else None,method=payload.get('method','GET'),headers={'Content-Type':'application/json'})
   try:
    with urllib.request.urlopen(req) as r:return {'status':r.status,'text':r.read().decode()}
   except urllib.error.HTTPError as e:return {'status':e.code,'text':e.read().decode()}
  cls.page.expose_binding('fetchBridge',bridge)
  shim='''<script>window.__store=SEED;Object.defineProperty(window,'localStorage',{configurable:true,value:{getItem:k=>window.__store[k]??null,setItem:(k,v)=>window.__store[k]=String(v),removeItem:k=>delete window.__store[k]}});window.fetch=async function(path,opts={}){const r=await window.fetchBridge({path:String(path),method:opts.method||'GET',body:opts.body||''});return {ok:r.status>=200&&r.status<300,status:r.status,text:async()=>r.text,json:async()=>JSON.parse(r.text)};};</script>'''.replace('SEED',json.dumps(saved))
  text=(WEB/'index.html').read_text();text=text.replace('<head>','<head>'+shim,1);cls.page.set_content(text,wait_until='load');cls.page.wait_for_timeout(600)
 @classmethod
 def tearDownClass(cls):cls.browser.close();cls.pw.stop();cls.server.shutdown();cls.server.server_close()
 def test_01_startup_and_machine_model_validation(self):
  self.assertEqual(self.errors,[]);self.assertTrue(self.page.evaluate("window.ScholarshipApp.getState().catalog.scholarships.some(x=>x.automation?.ready)"))
  self.assertTrue(self.page.locator('#goMatch').is_visible());self.assertFalse(self.page.locator('#school').is_visible())
  self.page.screenshot(path='/mnt/data/scholarship-v14-home.png',full_page=False)
 def test_02_new_machine_model_matches_grade_and_respects_expiry(self):
  aid=FIX['autoId'];r=self.page.evaluate("""id=>{const s=catalog.scholarships.find(x=>x.id===id);const p={level:'university',gradeTerms:['114-2'],gradeRecords:{'114-2':{semesterScore:88}}};return [classify(s,p,new Date('2026-09-20T12:00:00+08:00')).state,classify(s,p,new Date('2027-01-01T12:00:00+08:00')).state];}""",aid)
  self.assertEqual(r,['match','expired'])
 def test_03_uncertain_conditions_stay_visible(self):
  aid=FIX['autoId'];r=self.page.evaluate("""id=>{const s=structuredClone(catalog.scholarships.find(x=>x.id===id));s.automation.ready=false;return classify(s,{level:'high'},new Date('2026-09-20T12:00:00+08:00')).state;}""",aid);self.assertEqual(r,'pending')
 def test_04_save_restore_with_simulated_storage(self):
  page=self.page;page.locator('#goMatch').click();page.locator('#school').fill('\u570b\u7acb\u81fa\u7063\u5927\u5b78');page.locator('#saveProfileBtn').click();self.assertTrue(page.evaluate("!!localStorage.getItem('scholarship-finder-v4')"));saved=page.evaluate('window.__store');page.close();type(self).load_page(saved);self.assertEqual(self.page.evaluate('profile.school'),'\u570b\u7acb\u81fa\u7063\u5927\u5b78')
 def test_05_queue_real_progress_and_catalog_refresh(self):
  page=self.page;page.evaluate("showView('find');clearTimeout(schoolTimer);void requestSchoolLookup();");page.wait_for_timeout(5700);self.assertIn('\u7b49\u5f85',page.locator('#discoveryProgressLabel').inner_text());page.wait_for_timeout(5500);self.assertEqual(page.evaluate('discoveryState.status'),'done');self.assertEqual(page.evaluate('discoveryState.result.stats.added'),1);self.assertNotIn('gradeRecords',JOB['payload']);self.assertNotIn('economic',JOB['payload'])
 def test_06_document_plan_default_copies_and_any(self):
  page=self.page;aid=FIX['autoId'];page.evaluate("""id=>{clearTimeout(schoolTimer);profile.level='university';profile.gradeTerms=['114-2'];profile.gradeRecords={'114-2':{semesterScore:88}};selected.add(id);render();showView('plan');}""",aid);text=page.locator('#planContent').inner_text();self.assertIn('\u6210\u7e3e\u55ae',text);self.assertIn('1',text);self.assertNotIn('\u81ea\u8a02\u6e96\u5099\u4efd\u6578',text);self.assertGreater(page.locator('#planContent input[type=checkbox]').count(),0);page.screenshot(path='/mnt/data/scholarship-v14-plan.png',full_page=False)
 def test_07_mobile_no_horizontal_overflow(self):
  for width in [390,320]:
   self.page.set_viewport_size({'width':width,'height':900});self.page.evaluate("showView('library')");self.page.wait_for_timeout(150);self.assertLessEqual(self.page.evaluate('document.documentElement.scrollWidth'),width+1)
  self.page.set_viewport_size({'width':390,'height':900});self.page.screenshot(path='/mnt/data/scholarship-v14-mobile.png',full_page=False)
 def test_08_quota_error_visible(self):
  JOB['mode']='quota';self.page.evaluate("showView('find');clearTimeout(schoolTimer);void requestSchoolLookup();");self.page.wait_for_timeout(500);self.assertIn('\u514d\u8cbb\u984d\u5ea6',self.page.locator('#schoolLookupStatus').inner_text());JOB['mode']='ok'
 def test_09_no_runtime_errors(self):self.assertEqual(self.errors,[])
if __name__=='__main__':unittest.main()
