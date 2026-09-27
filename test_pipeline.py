import unittest,sys,json,copy,datetime as dt,tempfile
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from model import compile_analysis,date_value,all_expired,plain_json,document_copies
from net import canonical,public_ips,Page
import runner
URL='https://test.example.edu.tw/award'
QUOTE='114\u5b78\u5e74\u5ea6\u7b2c2\u5b78\u671f\u6210\u7e3e80\u5206\u4ee5\u4e0a\u3002\u5927\u5b78\u751f\u53ef\u7533\u8acb\u3002\u9700\u7e73\u4ea4\u6210\u7e3e\u55ae1\u4efd\u3001\u5b78\u751f\u8b49\u6216\u5728\u5b78\u8b49\u660e\u64c7\u4e00\u3002\u7533\u8acb\u4eba\u53ef\u81ea\u884c\u90f5\u5bc4\uff0c\u4ee52026\u5e7412\u670831\u65e5\u90f5\u6233\u70ba\u6191\u3002'
def raw():
 return {'isScholarship':True,'name':'\u6e2c\u8a66\u734e\u5b78\u91d1','organizer':'\u6e2c\u8a66\u57fa\u91d1\u6703','summary':'\u6e2c\u8a66\u8cc7\u6599\uff0c\u975e\u771f\u5be6\u516c\u544a','period':'115','complete':True,'issues':[],
 'rules':{'all':[{'field':'level','op':'eq','value':'university','label':'\u5927\u5b78\u751f','quote':QUOTE,'source':URL},{'field':'semesterScore','period':'114-2','op':'gte','value':80,'label':'\u6210\u7e3e80\u5206','quote':QUOTE,'source':URL}]},
 'documents':[{'name':'\u6210\u7e3e\u55ae','mode':'single','copies':1,'detail':'\u4e00\u4efd','quote':QUOTE,'source':URL},{'name':'\u5728\u5b78\u8b49\u660e\u6587\u4ef6','mode':'any','detail':'\u64c7\u4e00','quote':QUOTE,'source':URL,'options':[{'name':'\u5b78\u751f\u8b49','detail':''},{'name':'\u5728\u5b78\u8b49\u660e','detail':''}]}],
 'routes':[{'kind':'direct','deadline':'2026-12-31','start':None,'method':'\u81ea\u884c\u90f5\u5bc4','allowsIndividual':True,'quote':QUOTE,'source':URL}]}
def compile(r=None,**kwargs):return compile_analysis(r or raw(),{URL:QUOTE},URL,runner.stamp(),'115-1',verification={'consistent':True},**kwargs)
class ModelTests(unittest.TestCase):
 def test_chinese_document_copies(self):
  self.assertEqual(document_copies(2,'\u6210\u7e3e\u55ae\u4e8c\u4efd'),2);self.assertEqual(document_copies(None,'\u6210\u7e3e\u55ae'),1)
 def test_document_copies_not_year_substring(self):
  self.assertRaises(ValueError,document_copies,2,'2026\u5e74\u6210\u7e3e\u55ae');self.assertRaises(ValueError,document_copies,1,'11\u4efd')
 def test_valid_structured_machine_ready(self):
  p,m=compile();self.assertTrue(m['automation']['ready']);self.assertEqual(m['status'],'pending');self.assertEqual(m['documents'][1]['mode'],'any');self.assertEqual(len(m['documents']),2)
 def test_missing_document_evidence_not_ready(self):
  r=raw();r['documents'][0]['quote']='this is not quoted';self.assertFalse(compile(r)[1]['automation']['ready'])
 def test_wrong_grade_period_becomes_question(self):
  r=raw();r['rules']['all'][1]['period']='115-1';m=compile(r)[1];self.assertTrue(m['rules']['all'][1]['field'].startswith('extra:'));self.assertEqual(len(m['questions']),1)
 def test_numeric_substring_not_accepted(self):
  r=raw();r['rules']['all'][1]['value']=8;self.assertTrue(compile(r)[1]['rules']['all'][1]['field'].startswith('extra:'))
 def test_direct_submission_not_inferred(self):
  r=raw();r['routes'][0]['allowsIndividual']=False;m=compile(r)[1];self.assertFalse(m['automation']['ready']);self.assertEqual(m['routes'][0]['availability'],'unknown')
 def test_unknown_deadline_not_fabricated(self):
  r=raw();r['routes'][0]['deadline']=None;p,m=compile(r);self.assertIsNone(p['deadline']);self.assertFalse(m['automation']['ready'])
 def test_scan_missing_attachment_not_ready(self):self.assertFalse(compile(page_issues=['Scan not read'])[1]['automation']['ready'])
 def test_new_host_needs_confirmation(self):self.assertFalse(compile(trusted=False)[1]['automation']['ready'])
 def test_expiry_respects_all_routes(self):
  routes=[{'availability':'allowed','deadline':'2020-01-01T00:00:00+08:00'},{'availability':'unknown','deadline':None}];self.assertFalse(all_expired(routes));routes[1]['deadline']='2020-01-02T00:00:00+08:00';self.assertTrue(all_expired(routes))
 def test_dates_need_evidence(self):
  self.assertRaises(ValueError,date_value,'2026-12-31','12/31');self.assertRaises(ValueError,date_value,'2026-11-30',QUOTE);self.assertEqual(date_value('2026-12-31',QUOTE)[-14:],'23:59:59+08:00')
 def test_exact_time(self):self.assertIn('T17:30:00',date_value('2026-12-31',QUOTE+' 17:30','17:30'))
 def test_or_rule_not_flattened(self):
  r=raw();r['rules']={'any':r['rules']['all']};self.assertIn('any',compile(r)[1]['rules'])
 def test_json_wrapper(self):self.assertTrue(plain_json({'result':{'response':'```json\n{"ok":true}\n```'}})['ok'])
 def test_reject_private_url(self):
  for u in ['http://localhost/a','http://a.internal','https://user:pw@x.example','https://x.example:9090/a']:self.assertRaises(ValueError,canonical,u)
 def test_private_dns_rejected(self):
  with patch('socket.getaddrinfo',return_value=[(2,1,6,'',('127.0.0.1',443))]):self.assertRaises(ValueError,public_ips,'x.example',443)
 def test_canonical_tracking_removed(self):self.assertEqual(canonical('https://X.example/a?utm_source=x&id=2#f'),'https://x.example/a?id=2')
class FakeAPI:
 def __init__(self):
  self.data={n:json.loads((ROOT/'web/data'/n).read_text()) for n in ['catalog.json','library.json','schools.json','sources.json','daily-status.json']};self.data['runner-meta.json']={};self.events=[]
 def read(self,n):return copy.deepcopy(self.data.get(n))
 def write(self,n,x):self.data[n]=copy.deepcopy(x)
 def call(self,path,b=None):
  self.events.append((path,copy.deepcopy(b)));return {'allowed':True}
 def ai(self,messages):return {'response':{'consistent':True,'issues':[]}} if 'Verify a proposed' in messages[0]['content'] else {'response':raw()}
class EndToEndTests(unittest.TestCase):
 def test_new_search_extract_store_repeat_and_expire(self):
  api=FakeAPI();before=len(api.data['library.json']['programs']);context={'schoolName':'Test University','term':'115-1','city':'','district':'','schoolUrl':'','noticeName':'','noticeUrl':'','gradeTerms':['114-2']}
  page=Page(URL,'\u6e2c\u8a66\u734e\u5b78\u91d1',QUOTE,[],[],[],'hash1',body_text=QUOTE)
  with patch.object(runner,'search',return_value=[{'url':URL,'title':page.title}]),patch.object(runner.Reader,'page',return_value=page):
   out=runner.Job(api,{'id':'a'*32,'kind':'lookup','context':context}).run()
   self.assertEqual(out['stats']['added'],1);self.assertEqual(out['stats']['autoReady'],1);self.assertEqual(len(api.data['library.json']['programs']),before+1)
   self.assertTrue(api.events[-1][1]['result']['candidates'][0]['modelIds'])
   out2=runner.Job(api,{'id':'b'*32,'kind':'lookup','context':context}).run();self.assertEqual(out2['stats']['added'],0)
   self.assertEqual(len(api.data['library.json']['programs']),before+1)
  program=next(p for p in api.data['library.json']['programs'] if p['sourceUrl']==URL);self.assertTrue(all_expired(program['routes'],dt.datetime(2027,1,1,tzinfo=dt.timezone.utc)))
  (ROOT/'tests/compiled-fixture.json').write_text(json.dumps({'library':api.data['library.json'],'catalog':api.data['catalog.json'],'autoId':program['modelIds'][0]},ensure_ascii=False))
 def test_outage_not_false_success(self):
  api=FakeAPI();before=len(api.data['library.json']['programs'])
  with patch.object(runner,'search',side_effect=RuntimeError('SEARCH_FREE_QUOTA_REACHED')):
   r=runner.Job(api,{'id':'b'*32,'kind':'lookup','context':{'schoolName':'Unknown','term':'115-1'}}).run()
  self.assertEqual(r['outcome'],'partial');self.assertEqual(r['stats']['added'],0);self.assertEqual(len(api.data['library.json']['programs']),before)
if __name__=='__main__':unittest.main()
