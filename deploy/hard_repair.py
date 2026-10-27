"""One-shot SprintForge repair, invoked locally by Ansible as root."""
import time
import argparse, datetime, hashlib, ipaddress, json, os, re, shutil, socket, subprocess, sys, urllib.request
from pathlib import Path

DOMAIN='sprintforge.netwizzard.tech'
STATE=Path('/var/lib/sprintforge-hard-repair')
MANAGED=Path('/etc/nginx/sprintforge-managed.conf')
TOKEN=re.compile(r'\s+|\#[^\n]*|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|[{};]|[^\s{};#]+')
def tokens(source):
    return [(m.group(),m.start(),m.end()) for m in TOKEN.finditer(source) if not m.group().isspace() and not m.group().startswith('#')]
def remove_name(source):
    ts=tokens(source); changes=[]
    for i,(t,a,b) in enumerate(ts):
        if t!='server_name':continue
        end=i+1
        while end<len(ts) and ts[end][0]!=';':end+=1
        if end==len(ts):raise RuntimeError('Unterminated server_name')
        names=[x[0] for x in ts[i+1:end]]
        kept=[n for n in names if n.strip('"\'')!=DOMAIN]
        if len(kept)!=len(names):
            if not kept:kept=['sprintforge-replaced-'+hashlib.sha256(source[a:ts[end][2]].encode()).hexdigest()[:12]+'.invalid']
            changes.append((a,ts[end][2],'server_name '+' '.join(kept)+';'))
    for a,b,value in reversed(changes):source=source[:a]+value+source[b:]
    return source

def inject(source):
    ts=tokens(source)
    for i,(t,a,b) in enumerate(ts[:-1]):
        if t=='http' and ts[i+1][0]=='{':
            include='include '+str(MANAGED)+';'
            # Repair owns one explicit include; refuse ambiguous repeated references.
            if str(MANAGED) in source:
                if source.count(str(MANAGED))!=1:raise RuntimeError('Repeated managed include requires review')
                return source
            pos=ts[i+1][2];return source[:pos]+'\n    '+include+'\n'+source[pos:]
    raise RuntimeError('No http block in /etc/nginx/nginx.conf')

class Repair:
    def __init__(self,email,rebuild=False):
        self.email=email; self.rebuild=rebuild; self.port=18870; self.stage='initializing'; self.saved={};self.routing=False
        STATE.mkdir(mode=0o700,parents=True,exist_ok=True)
        stamp=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+str(os.getpid())
        self.backup=STATE/stamp;self.backup.mkdir(mode=0o700)
        self.log=(self.backup/'repair.log').open('w');os.chmod(self.backup/'repair.log',0o600)
    def run(self,args):
        p=subprocess.run(args,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
        self.log.write('$ '+' '.join(args)+'\n'+p.stdout+'\n');self.log.flush()
        if p.returncode:raise RuntimeError('Command failed: '+args[0]+'; see private repair log')
        return p.stdout
    def save(self,path):
        path=Path(path).resolve()
        if str(path) not in self.saved:
            if path.exists():
                dest=self.backup/('file-'+str(len(self.saved)));shutil.copy2(path,dest);self.saved[str(path)]=str(dest)
            else:self.saved[str(path)]=None
            (self.backup/'files.json').write_text(json.dumps(self.saved,indent=2))
    def write(self,path,value):
        path=Path(path).resolve();self.save(path);path.write_text(value);os.chmod(path,0o644)
    def request(self,url,marker=None):
        # Public checks must use direct DNS/TLS, matching local probes' --noproxy.
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(url,timeout=25) as response:
                body=response.read(200000).decode('utf-8','replace')
                if marker and marker not in body:raise RuntimeError('Wrong application at '+url)
                return body
        except urllib.error.HTTPError as error:
            raise RuntimeError('Public check returned HTTP '+str(error.code)+' at '+url) from error
    @staticmethod
    def prepare_challenge():
        root=Path('/var/lib/sprintforge-acme')
        for path in (root,root/'.well-known',root/'.well-known'/'acme-challenge'):
            path.mkdir(parents=True,exist_ok=True)
            path.chmod(0o755)
        return root/'.well-known'/'acme-challenge'
    def probe(self,ip,port,tls=False,path='/',attempts=10):
        header=self.backup/('probe-'+hashlib.sha256((ip+str(port)+path).encode()).hexdigest()[:12]+'.headers')
        args=['curl','--fail','--silent','--show-error','--connect-timeout','3','--max-time','8','--noproxy','*','--dump-header',str(header),'--resolve',f'{DOMAIN}:{port}:{"["+ip+"]" if ":" in ip else ip}',f'{"https" if tls else "http"}://{DOMAIN}:{port}{path}']
        expected='"status": "ok"' if path=='/api/health' else '<title>SprintForge'
        detail='no response'
        for attempt in range(attempts):
            try:
                body=self.run(args)
                headers=header.read_text() if header.exists() else ''
                route='x-sprintforge-route: managed-v2' in headers.lower()
                if expected in body and route:return
                title=re.search(r'<title[^>]*>(.*?)</title>',body,re.I|re.S)
                detail='title='+repr(title.group(1)[:120] if title else '(none)')+'; managed route marker='+str(route)
            except RuntimeError as e:detail=str(e)
            if attempt+1<attempts:time.sleep(1)
        raise RuntimeError('SprintForge routing not ready on '+ip+':'+str(port)+' after '+str(attempts)+' checks; '+detail)
    def config(self,addresses,tls=False):
        def listens(port):
            out=[f'    listen {port}'+(' ssl' if port==443 else '')+';']
            for ip in addresses:
                value=f'[{ip}]' if ':' in ip else ip
                out.append(f'    listen {value}:{port}'+(' ssl' if port==443 else '')+';')
            return '\n'.join(out)
        proxy='''    add_header X-SprintForge-Route managed-v2 always;\n    location / {\n        proxy_pass http://127.0.0.1:SPRINTFORGE_BACKEND_PORT;\n        proxy_set_header Host $host;\n        proxy_set_header X-Forwarded-Proto $scheme;\n        proxy_read_timeout 30s;\n    }\n'''
        proxy=proxy.replace('SPRINTFORGE_BACKEND_PORT',str(getattr(self,'port',18870)))
        acme='    location ^~ /.well-known/acme-challenge/ { root /var/lib/sprintforge-acme; }\n'
        http='server {\n'+listens(80)+f'\n    server_name {DOMAIN};\n    client_max_body_size 100k;\n'+acme
        if not tls:return '# Managed by SprintForge hard repair\n'+http+proxy+'}\n'
        return '# Managed by SprintForge hard repair\n'+http+f'    location / {{ return 301 https://{DOMAIN}$request_uri; }}\n}}\nserver {{\n'+listens(443)+f'\n    server_name {DOMAIN};\n    ssl_certificate /etc/letsencrypt/live/{DOMAIN}/fullchain.pem;\n    ssl_certificate_key /etc/letsencrypt/live/{DOMAIN}/privkey.pem;\n    ssl_protocols TLSv1.2 TLSv1.3;\n    client_max_body_size 100k;\n'+proxy+'}\n'
    @staticmethod
    def source_permissions(source):
        source.chmod(0o755)
        tracked=subprocess.check_output(['git','-C',str(source),'ls-files','-z']).decode().split('\0')
        for name in filter(None,tracked):
            p=source/name
            if p.is_symlink():raise RuntimeError('Unexpected symlink in release: '+name)
            if not p.is_file():continue
            p.chmod(0o644)
            for parent in p.parents:
                if parent==source:break
                parent.chmod(0o755)
    def choose_port(self):
        settings=Path('/etc/sprintforge/backend-port')
        candidates=[]
        if settings.exists():
            try:candidates.append(int(settings.read_text().strip()))
            except ValueError:pass
        candidates+=list(range(18870,18971))
        for port in dict.fromkeys(candidates):
            if not 1024<=port<=65535:continue
            with socket.socket(socket.AF_INET,socket.SOCK_STREAM) as sock:
                try:sock.bind(('127.0.0.1',port))
                except OSError:continue
                return port
        raise RuntimeError('No available SprintForge backend port in 18870–18970')
    def repair(self):
        self.stage='packages and application'
        self.run(['apt-get','update']);self.run(['apt-get','install','-y','git','python3','nginx','curl','certbot','ca-certificates'])
        self.run(['nginx','-t'])
        source=Path('/opt/sprintforge')
        if self.rebuild:
            self.stage='fresh source checkout and verification'
            releases=Path('/opt/sprintforge-releases');releases.mkdir(mode=0o755,exist_ok=True);releases.chmod(0o755)
            source=releases/self.backup.name
            self.run(['git','clone','--depth','1','--branch','main','https://github.com/olamidebello/SprintForge.git',str(source)])
        elif (source/'.git').exists():
            if self.run(['git','-C',str(source),'status','--porcelain']).strip():raise RuntimeError('Application checkout has local changes; use rebuild-and-repair.yml to deploy a separate fresh release')
            self.run(['git','-C',str(source),'pull','--ff-only','origin','main'])
        elif source.exists() and any(source.iterdir()):raise RuntimeError('Non-Git application directory is not empty; use rebuild-and-repair.yml')
        else:self.run(['git','clone','https://github.com/olamidebello/SprintForge.git',str(source)])
        self.source=source
        # The repair's private umask must not make application code unreadable by its service user.
        self.source_permissions(source)
        self.run(['python3','-m','py_compile',str(source/'app.py'),str(source/'manage.py')])
        if self.rebuild:
            self.run(['python3','-m','unittest','discover','-s',str(source/'tests'),'-q'])
        required=['app.py','manage.py','static/index.html','static/app.js','static/style.css','static/logo.webp','static/hero.webp']
        for relative in required:
            if not (source/relative).is_file() or not (source/relative).stat().st_size:raise RuntimeError('Missing release asset: '+relative)
        if '<title>SprintForge' not in (source/'static/index.html').read_text():raise RuntimeError('Release is missing the SprintForge identity')
        db=Path('/var/lib/sprintforge/sprintforge.db')
        if db.exists():
            import sqlite3
            with sqlite3.connect('file:'+str(db)+'?mode=ro',uri=True) as src,sqlite3.connect(str(self.backup/'database.db')) as dst:src.backup(dst)
        try:self.run(['id','-u','sprintforge'])
        except RuntimeError:self.run(['useradd','--system','--no-create-home','--shell','/usr/sbin/nologin','sprintforge'])
        self.run(['install','-d','-m','0700','-o','sprintforge','-g','sprintforge','/var/lib/sprintforge'])
        self.write('/etc/systemd/system/sprintforge.service','''[Unit]\nDescription=SprintForge\nAfter=network.target\n[Service]\nUser=sprintforge\nGroup=sprintforge\nWorkingDirectory=/opt/sprintforge\nEnvironment=SPRINTFORGE_DB=/var/lib/sprintforge/sprintforge.db\nEnvironment=SPRINTFORGE_SECURE_COOKIE=1\nEnvironment=HOST=127.0.0.1\nEnvironment=PORT=18870\nExecStart=/usr/bin/python3 /opt/sprintforge/app.py\nRestart=on-failure\nRestartSec=3\nUMask=0077\nNoNewPrivileges=true\nPrivateTmp=true\nProtectSystem=strict\nProtectHome=true\nReadWritePaths=/var/lib/sprintforge\n[Install]\nWantedBy=multi-user.target\n''')
        # Stop only the SprintForge unit before selecting its backend port.
        self.run(['systemctl','stop','sprintforge'])
        self.port=self.choose_port()
        Path('/etc/sprintforge').mkdir(mode=0o755,exist_ok=True)
        self.write('/etc/sprintforge/backend-port',str(self.port)+'\n')
        override=Path('/etc/systemd/system/sprintforge.service.d')
        override.mkdir(parents=True,exist_ok=True)
        self.write(override/'zzzz-sprintforge-repair.conf',f"""[Service]
Type=simple
User=sprintforge
Group=sprintforge
WorkingDirectory={source}
EnvironmentFile=
ExecStart=
ExecStart=/usr/bin/env HOST=127.0.0.1 PORT={self.port} SPRINTFORGE_DB=/var/lib/sprintforge/sprintforge.db SPRINTFORGE_SECURE_COOKIE=1 /usr/bin/python3 {source}/app.py
Restart=on-failure
RestartSec=3
""")
        self.run(['systemctl','daemon-reload'])
        self.run(['systemctl','reset-failed','sprintforge'])
        self.run(['systemctl','enable','--now','sprintforge'])
        self.run(['systemctl','show','sprintforge','-p','MainPID','-p','ExecStart','-p','DropInPaths'])
        backend_url='http://127.0.0.1:'+str(self.port)
        root=self.run(['curl','--fail','--silent','--show-error','--noproxy','*','--max-time','10','--retry','8','--retry-connrefused','--retry-delay','1',backend_url+'/'])
        if '<title>SprintForge' not in root:
            title=re.search(r'<title[^>]*>(.*?)</title>',root,re.I|re.S)
            raise RuntimeError('Backend '+str(self.port)+' returned '+repr(title.group(1)[:120] if title else 'no page title')+'; inspect private service diagnostics')
        health=self.run(['curl','--fail','--silent','--show-error','--noproxy','*','--max-time','10',backend_url+'/api/health'])
        if json.loads(health).get('status')!='ok':raise RuntimeError('SprintForge backend health check failed')
        self.stage='active Nginx routing'
        dump=self.run(['nginx','-T']);(self.backup/'nginx-before.txt').write_text(dump)
        files=re.findall(r'^# configuration file (.+):$',dump,re.M)
        addresses={'127.0.0.1'}
        local=json.loads(self.run(['ip','-j','address','show']))
        for interface in local:
            for a in interface.get('addr_info',[]):
                ip=a['local']
                if a['family']=='inet' or a.get('scope')=='global':addresses.add(ip)
        # Add explicit IPv6 loopback only when configured locally.
        if any(a.get('local')=='::1' for i in local for a in i.get('addr_info',[])):addresses.add('::1')
        # Include explicit wildcard IPv6 where already used, matching its socket group.
        if re.search(r'listen\s+\[::\]:(?:80|443)',dump):addresses.add('::')
        addresses=sorted(addresses)
        self.routing=True
        for name in sorted(set(files)):
            p=Path(name).resolve()
            if p==MANAGED or not p.is_file():continue
            old=p.read_text();new=remove_name(old)
            if new!=old:self.write(p,new)
        main=Path('/etc/nginx/nginx.conf');self.write(main,inject(main.read_text()))
        challenge=self.prepare_challenge()
        self.write(MANAGED,self.config(addresses))
        self.run(['nginx','-t']);self.run(['systemctl','reload','nginx'])
        (self.backup/'nginx-candidate.txt').write_text(self.run(['nginx','-T']))
        self.run(['systemctl','show','nginx','-p','MainPID','-p','ExecStart','-p','ExecReload'])
        for ip in addresses:
            if ip!='::':self.probe(ip,80)
        self.stage='public HTTP application identity'
        self.request('http://'+DOMAIN+'/', '<title>SprintForge')
        self.stage='public ACME challenge readability'
        nonce=os.urandom(16).hex();probe=challenge/nonce;probe.write_text(nonce);probe.chmod(0o644)
        try:
            if self.request('http://'+DOMAIN+'/.well-known/acme-challenge/'+nonce)!=nonce:raise RuntimeError('Public ACME challenge routed incorrectly')
        finally:probe.unlink(missing_ok=True)
        self.stage='TLS certificate'
        # Certbot inherits a normal public-webroot umask for challenge files.
        os.umask(0o022)
        try:
            self.run(['certbot','certonly','--webroot','-w','/var/lib/sprintforge-acme','--non-interactive','--agree-tos','--email',self.email,'-d',DOMAIN,'--keep-until-expiring'])
        finally:os.umask(0o077)
        self.write(MANAGED,self.config(addresses,True));self.run(['nginx','-t']);self.run(['systemctl','reload','nginx'])
        self.stage='HTTPS application identity'
        for ip in addresses:
            if ip!='::':self.probe(ip,443,True);self.probe(ip,443,True,'/api/health')
        self.request('https://'+DOMAIN+'/', '<title>SprintForge');self.request('https://'+DOMAIN+'/static/app.js','function boot(')
        self.request('https://'+DOMAIN+'/api/health','"status": "ok"')
        Path('/etc/letsencrypt/renewal-hooks/deploy').mkdir(parents=True,exist_ok=True)
        self.write('/etc/letsencrypt/renewal-hooks/deploy/sprintforge-nginx-reload','#!/bin/sh\nnginx -t && systemctl reload nginx\n');os.chmod('/etc/letsencrypt/renewal-hooks/deploy/sprintforge-nginx-reload',0o755)
        self.run(['systemctl','enable','--now','certbot.timer'])
    def restore(self):
        # Restore only changed Nginx files. Application data and source are retained.
        for name,backup in self.saved.items():
            if not name.startswith('/etc/nginx/'):continue
            path=Path(name)
            if backup:shutil.copy2(backup,path)
            else:path.unlink(missing_ok=True)
        self.run(['nginx','-t']);self.run(['systemctl','reload','nginx'])
    def execute(self):
        report={'status':'failed','backup':str(self.backup),'log':str(self.backup/'repair.log')}
        try:self.repair();report.update(status='passed',stage='complete',url='https://'+DOMAIN);code=0
        except Exception as e:
            report.update(stage=self.stage,reason=str(e));code=1
            for command in [['ss','-ltnp'],['systemctl','show','sprintforge','-p','MainPID','-p','ExecStart','-p','DropInPaths'],['journalctl','-u','sprintforge','-n','60','--no-pager']]:
                try:self.run(command)
                except Exception:pass
            if self.routing:
                try:self.restore();report['routing_restored']=True
                except Exception as rollback:report['routing_restore_error']=str(rollback)
        report['backend_port']=self.port
        report['source']=str(getattr(self,'source',''))
        (STATE/'report.json').write_text(json.dumps(report,indent=2));print(json.dumps(report));return code

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--email',required=True);p.add_argument('--rebuild',action='store_true');a=p.parse_args()
    if os.geteuid()!=0:p.error('Run as root')
    if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',a.email):p.error('Invalid certificate email')
    os.umask(0o077)
    import fcntl
    with open('/run/lock/sprintforge-deploy.lock','w') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:sys.exit('Another SprintForge deployment is running')
        sys.exit(Repair(a.email,a.rebuild).execute())
