"""Public-source reader: pinned public IP, TLS hostname, robots, bounded redirects.
No source response can execute code or receive an API secret. HTML and files are
untrusted input. Failed/partial extraction is never called a successful review.
"""
from __future__ import annotations
import dataclasses, hashlib, http.client, ipaddress, json, re, socket, ssl, subprocess, sys, tempfile, time
import urllib.parse as U
import urllib.robotparser
from pathlib import Path
from bs4 import BeautifulSoup

AGENT='ScholarshipFree/14 (+public-scholarship-research; respects robots.txt)'
MAX_BYTES=6_000_000
WORDS=re.compile(r'\u734e\u5b78|\u734e\u52a9|\u52a9\u5b78|scholarship',re.I)
SKIP=re.compile(r'\u7372\u734e\u540d\u55ae|\u9304\u53d6\u540d\u55ae|\u5f97\u734e\u540d\u55ae|\u5be9\u6838\u7d50\u679c')
ATTACH=re.compile(r'\.(pdf|docx?|odt|xlsx?)(?:$|\?)',re.I)

def canonical(url):
    p=U.urlsplit(url)
    if p.scheme not in ('http','https') or not p.hostname or p.username or p.password or p.port not in (None,80,443):raise ValueError('Invalid public URL')
    host=p.hostname.encode('idna').decode('ascii').lower()
    if host in ('localhost','metadata.google.internal') or host.endswith(('.localhost','.local','.internal')):raise ValueError('Private host denied')
    q=[(k,v) for k,v in U.parse_qsl(p.query,keep_blank_values=True) if not k.lower().startswith('utm_') and k not in ('fbclid','gclid')]
    path=U.quote(U.unquote(p.path or '/'),safe='/,:;@!$&\'()*+=-._~')
    return U.urlunsplit((p.scheme,host,path,U.urlencode(q),''))

def public_ips(host,port):
    ips=list(dict.fromkeys(x[4][0] for x in socket.getaddrinfo(host,port,type=socket.SOCK_STREAM)))
    if not ips or any(not ipaddress.ip_address(ip).is_global for ip in ips):raise ValueError('Private/reserved IP denied')
    return ips

class PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self,host,port,ip):super().__init__(host,port,timeout=15,context=ssl.create_default_context());self.ip=ip
    def connect(self):
        sock=socket.create_connection((self.ip,self.port),self.timeout)
        self.sock=self._context.wrap_socket(sock,server_hostname=self.host)

@dataclasses.dataclass
class Page:
    url:str
    title:str
    text:str
    links:list
    attachments:list
    issues:list
    digest:str
    content_type:str='text/html'
    body_text:str=''

class Reader:
    def __init__(self):self.robot={};self.last={};self.delays={}
    def raw(self,url):
        url=canonical(url);p=U.urlsplit(url);port=443 if p.scheme=='https' else 80
        ips=public_ips(p.hostname,port)
        wait=self.delays.get(p.hostname,1.0)-(time.monotonic()-self.last.get(p.hostname,0))
        if wait>0:time.sleep(min(wait,30))
        self.last[p.hostname]=time.monotonic()
        if p.scheme=='https':c=PinnedHTTPS(p.hostname,port,ips[0])
        else:
            c=http.client.HTTPConnection(p.hostname,port,timeout=15)
            c.sock=socket.create_connection((ips[0],port),15)
        try:
            c.request('GET',p.path+('?' +p.query if p.query else ''),headers={'User-Agent':AGENT,'Host':p.hostname,'Accept-Encoding':'identity','Accept':'text/html,application/pdf,application/octet-stream;q=0.5'})
            r=c.getresponse();headers={k.lower():v for k,v in r.getheaders()}
            if r.status in (301,302,303,307,308):return r.status,headers,b''
            if int(headers.get('content-length','0') or 0)>MAX_BYTES:raise ValueError('Source file too large')
            data=r.read(MAX_BYTES+1)
            if len(data)>MAX_BYTES:raise ValueError('Source file too large')
            return r.status,headers,data
        finally:c.close()
    def robots(self,url):
        p=U.urlsplit(url);origin=f'{p.scheme}://{p.netloc}'
        if origin not in self.robot:
            try:
                code,h,b=self.raw(origin+'/robots.txt')
                if code==404:self.robot[origin]=True
                elif code==200:
                    rp=urllib.robotparser.RobotFileParser();rp.parse(b.decode('utf-8','replace').splitlines());delay=rp.crawl_delay(AGENT) or rp.crawl_delay('*') or 1
                    self.robot[origin]=rp if delay<=30 else False;self.delays[p.hostname]=delay
                else:self.robot[origin]=False
            except Exception:self.robot[origin]=False
        r=self.robot[origin];return r if isinstance(r,bool) else r.can_fetch(AGENT,url)
    def get(self,url):
        for _ in range(4):
            url=canonical(url)
            if not self.robots(url):raise PermissionError('Robots unavailable or disallowed')
            code,h,b=self.raw(url)
            if code in (301,302,303,307,308):url=U.urljoin(url,h.get('location',''));continue
            if code!=200:raise ValueError('Source HTTP '+str(code))
            return url,h,b
        raise ValueError('Too many redirects')
    def attachment(self,url):
        final,h,b=self.get(url);suffix=Path(U.urlsplit(final).path).suffix.lower()
        if b.startswith(b'%PDF-'):suffix='.pdf'
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/('attachment'+suffix);p.write_bytes(b)
            try:
                r=subprocess.run([sys.executable,str(Path(__file__).with_name('parse_attachment.py')),str(p),suffix],capture_output=True,text=True,timeout=15,check=True)
                parsed=json.loads(r.stdout)
            except Exception:parsed={'state':'manual_review','text':'','note':'Parser timeout or unsupported file'}
        return {'url':final,'hash':hashlib.sha256(b).hexdigest(),**parsed}
    def page(self,url,max_attachments=3):
        final,h,b=self.get(url);content_type=h.get('content-type','')
        if b.startswith(b'%PDF-') or ATTACH.search(final) and 'html' not in content_type:
            # Use the already-downloaded bytes, no second source request.
            suffix='.pdf' if b.startswith(b'%PDF-') else Path(U.urlsplit(final).path).suffix
            with tempfile.TemporaryDirectory() as tmp:
                f=Path(tmp)/('file'+suffix);f.write_bytes(b)
                try:
                    x=subprocess.run([sys.executable,str(Path(__file__).with_name('parse_attachment.py')),str(f),suffix],capture_output=True,text=True,timeout=15,check=True);a=json.loads(x.stdout)
                except Exception:a={'state':'manual_review','text':''}
            issues=[] if a['state']=='text_extracted' else ['\u9644\u4ef6\u70ba\u6383\u63cf\u3001\u52a0\u5bc6\u6216\u7121\u6cd5\u5b8c\u6574\u89e3\u6790\uff0c\u9700\u4eba\u5de5\u6838\u5c0d']
            return Page(final,Path(U.unquote(U.urlsplit(final).path)).name,a.get('text',''),[],[],issues,hashlib.sha256(b).hexdigest(),content_type)
        if not any(x in content_type for x in ('html','text/plain')):raise ValueError('Unsupported source format')
        soup=BeautifulSoup(b,'html.parser')
        title=soup.title.get_text(' ',strip=True)[:240] if soup.title else ''
        links=[{'url':U.urljoin(final,a.get('href','')),'title':a.get_text(' ',strip=True)[:180]} for a in soup.select('a[href]')]
        for tag in soup.select('script,style,noscript,nav,header,footer'):tag.decompose()
        # Full visible body is retained; do not cut eligibility in sidebar tables.
        text=soup.get_text('\n',strip=True)
        attachments=[];issues=[];urls=[]
        for l in links:
            if ATTACH.search(l['url']) and l['url'] not in urls:urls.append(l['url'])
        for aurl in urls[:max_attachments]:
            try:
                a=self.attachment(aurl);attachments.append(a)
                if a['state']!='text_extracted':issues.append('\u9644\u4ef6\u7121\u6cd5\u5b8c\u6574\u8b80\u53d6\uff1a'+aurl)
            except Exception:issues.append('\u9644\u4ef6\u8b80\u53d6\u5931\u6557\uff1a'+aurl)
        if len(urls)>max_attachments:issues.append('\u9644\u4ef6\u8d85\u904e\u55ae\u6b21\u4e0a\u9650\uff0c\u9084\u6709\u9644\u4ef6\u5f85\u6838\u5c0d')
        joined=text+'\n'+'\n'.join(a.get('text','') for a in attachments)
        if len(joined)>28000:issues.append('\u5167\u5bb9\u8f03\u9577\uff0c\u672c\u6b21\u50c5\u8655\u7406\u90e8\u5206\uff0c\u5b8c\u6574\u8cc7\u683c\u5f85\u6838\u5c0d')
        basis=joined+json.dumps([(a['url'],a.get('hash')) for a in attachments])+json.dumps(urls)
        return Page(final,title,joined[:28000],links,attachments,issues,hashlib.sha256(basis.encode()).hexdigest(),content_type,text)
