import json,queue,re,shutil,subprocess,threading,time
from .config import ROOT

class TunnelError(RuntimeError):pass
class LocalhostTunnel:
    def __init__(self,port):self.port=port;self.process=None;self.url=None
    def start(self,timeout=45):
        binary=shutil.which('ssh')
        if not binary:raise TunnelError('OpenSSH не найден. Установите официальный клиент OpenSSH или используйте HTTPS_MODE=external.')
        data=ROOT/'data';data.mkdir(exist_ok=True)
        command=[binary,'-T','-o','BatchMode=yes','-o','IdentitiesOnly=yes','-o','IdentityFile=none',
            '-o','StrictHostKeyChecking=accept-new','-o',f'UserKnownHostsFile={data / "localhost-run-known-hosts"}',
            '-o','ConnectTimeout=12','-o','ServerAliveInterval=20','-o','ServerAliveCountMax=3','-o','ExitOnForwardFailure=yes',
            '-R',f'80:127.0.0.1:{self.port}','nokey@localhost.run','--','--output','json']
        self.process=subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,encoding='utf-8',errors='replace')
        lines=queue.Queue(maxsize=100)
        def read():
            for line in self.process.stdout:
                try:
                    event=json.loads(line)
                    if event.get('type')=='v1.tcpip_forward.register.accepted':
                        match=re.search(r'https://[a-zA-Z0-9-]+\.(?:lhr\.life|lhr\.rocks|localhost\.run)\b',event.get('data',''))
                        if match:self.url=match.group(0)
                except ValueError:pass
                try:lines.put_nowait(line)
                except queue.Full:pass
        threading.Thread(target=read,daemon=True).start()
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            if self.process.poll() is not None:raise TunnelError('localhost.run завершил соединение. Проверьте сеть/VPN и SSH.')
            try:line=lines.get(timeout=.3)
            except queue.Empty:continue
            try:event=json.loads(line)
            except ValueError:continue
            if event.get('type')!='v1.tcpip_forward.register.accepted':continue
            match=re.search(r'https://[a-zA-Z0-9-]+\.(?:lhr\.life|lhr\.rocks|localhost\.run)\b',event.get('data',''))
            if match:self.url=match.group(0);return self.url
        raise TunnelError('localhost.run не выдал HTTPS-адрес. Проверьте подключение.')
    def close(self):
        if self.process:
            if self.process.poll() is None:
                self.process.terminate()
                try:self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:self.process.kill();self.process.wait(timeout=5)
            if self.process.stdout:self.process.stdout.close()
