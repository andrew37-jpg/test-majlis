"""Optional development HTTPS via the user's installed official cloudflared."""
import os,re,shutil,subprocess,threading,time,queue
from collections import deque
import json,ipaddress,urllib.request,urllib.parse
from network import ssl_context
from pathlib import Path

class TunnelError(RuntimeError):pass

class QuickTunnel:
 def __init__(self,port,binary=None,doh_edges=False,protocol="http2",edge_hostnames=False):
  self.port=port;self.binary=binary;self.doh_edges=doh_edges;self.protocol=protocol;self.edge_hostnames=edge_hostnames;self.process=None;self.lines=queue.Queue(maxsize=200);self.url=None;self.connected=False;self.last_error=None;self.errors=deque(maxlen=30)
 def executable(self):
  candidates=[self.binary,os.getenv('CLOUDFLARED_PATH'),shutil.which('cloudflared')]
  if os.name!='nt':candidates+=['/opt/homebrew/bin/cloudflared','/usr/local/bin/cloudflared']
  for p in candidates:
   if p and Path(p).is_file():return str(p)
  raise TunnelError('cloudflared не найден. Установите его с официального сайта Cloudflare или задайте CLOUDFLARED_PATH. Инструкция: TELEGRAM_START.md')
 def edge_addresses(self):
  # Public service discovery only. No OS/VPN changes; transport TLS remains enabled.
  def query(name,kind):
   url='https://cloudflare-dns.com/dns-query?'+urllib.parse.urlencode({'name':name,'type':kind})
   request=urllib.request.Request(url,headers={'Accept':'application/dns-json'})
   with urllib.request.urlopen(request,timeout=12,context=ssl_context()) as response:data=json.load(response)
   if data.get('Status')!=0:raise TunnelError('Cloudflare DNS-over-HTTPS не вернул адреса серверов.')
   return data.get('Answer',[])
  addresses=[]
  records=[a['data'].split() for a in query('_v2-origintunneld._tcp.argotunnel.com','SRV') if a.get('type')==33]
  for record in sorted(records,key=lambda r:int(r[0])):
   if len(record)!=4:continue
   port=int(record[2]);target=record[3].rstrip('.')
   if port!=7844 or target not in ('region1.v2.argotunnel.com','region2.v2.argotunnel.com'):continue
   if self.edge_hostnames:
    addresses.append(f'{target}:{port}');continue
   region=[]
   for answer in query(target,'A'):
    if answer.get('type')!=1:continue
    address=ipaddress.ip_address(answer['data'])
    if address.version==4 and address.is_global:region.append(f'{address}:{port}')
   addresses.extend(region[:2])
  if not addresses:raise TunnelError('Не получены публичные адреса серверов Cloudflare.')
  return addresses[:4]
 def start(self,timeout=90):
  if self.protocol not in ('http2','quic'):raise TunnelError('Протокол cloudflared: http2 или quic.')
  binary=self.executable()
  command=[binary,'tunnel','--no-autoupdate','--url',f'http://127.0.0.1:{self.port}','--protocol',self.protocol]
  if self.doh_edges:
   for address in self.edge_addresses():command.extend(['--edge',address])
  self.process=subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,encoding='utf-8',errors='replace')
  def read():
   for line in self.process.stdout:
    if ('ERR' in line or 'error=' in line) and 'token' not in line.lower():
     safe=re.sub(r'\b\d{7,15}:[A-Za-z0-9_-]{30,}\b','[hidden]',line.strip())
     self.errors.append(safe[-500:])
    try:self.lines.put_nowait(line)
    except queue.Full:pass
  threading.Thread(target=read,daemon=True,name='cloudflare-log-reader').start()
  deadline=time.monotonic()+timeout
  while time.monotonic()<deadline:
   if self.process.poll() is not None:
    detail=' '.join(self.errors)[-700:]
    raise TunnelError('HTTPS-туннель завершился. '+(detail or 'Проверьте Интернет, VPN и конфигурацию cloudflared.'))
   try:line=self.lines.get(timeout=.3)
   except queue.Empty:continue
   match=re.search(r'https://[a-z0-9-]+\.trycloudflare\.com\b',line)
   if match:self.url=match.group(0)
   if 'Registered tunnel connection' in line:self.connected=True
   if 'lookup ' in line and ('connection refused' in line or 'no such host' in line):self.last_error='DNS недоступен. Проверьте сеть/VPN и DNS компьютера.'
   if self.url and self.connected:return self.url
  relevant=[e for e in self.errors if 'error=' in e and 'features' not in e.lower()]
  detail=' '.join(relevant[-2:])[-600:]
  raise TunnelError('Cloudflare не установил соединение. '+(detail or self.last_error or 'Проверьте доступность сервиса в сети.'))
 def close(self):
  if self.process:
   if self.process.poll() is None:
    self.process.terminate()
    try:self.process.wait(timeout=5)
    except subprocess.TimeoutExpired:self.process.kill();self.process.wait(timeout=5)
   if self.process.stdout:self.process.stdout.close()
