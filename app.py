"""SprintForge: dependency-free, self-hosted SDLC workspace."""
import base64, datetime as dt, hashlib, hmac, http.cookies, json, os, re, secrets, sqlite3, time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB = os.environ.get('SPRINTFORGE_DB', str(ROOT / 'data' / 'sprintforge.db'))
SESSION_AGE = 60 * 60 * 12
KINDS = ('project', 'ticket', 'sprint', 'test', 'run', 'release', 'requirement')
STATUSES = {'project':['Planned','Active','Completed'], 'ticket':['Backlog','Ready','In Progress','Code Review','Ready for Testing','Testing','Done'], 'sprint':['Planned','Active','Completed'], 'test':['Draft','Ready','Deprecated'], 'run':['Passed','Failed','Blocked','Skipped'], 'release':['Planned','Ready','Released'], 'requirement':['Draft','Approved','Implemented']}

class Problem(Exception):
    def __init__(self, status, message): self.status, self.message = status, message

@contextmanager
def connect():
    c = sqlite3.connect(DB, timeout=15)
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA foreign_keys=ON')
    try:
        yield c
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally: c.close()

def init_db():
    Path(DB).parent.mkdir(parents=True, exist_ok=True)
    with connect() as c:
        c.executescript('''
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY, name TEXT NOT NULL, email TEXT UNIQUE NOT NULL, password TEXT NOT NULL, created TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS orgs(id TEXT PRIMARY KEY, name TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS members(org TEXT REFERENCES orgs(id), user TEXT REFERENCES users(id), role TEXT NOT NULL, PRIMARY KEY(org,user));
        CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY, user TEXT REFERENCES users(id), csrf TEXT NOT NULL, expires INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS records(id TEXT PRIMARY KEY, org TEXT REFERENCES orgs(id), kind TEXT NOT NULL, data TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1, created TEXT NOT NULL, updated TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS record_org ON records(org,kind);
        CREATE TABLE IF NOT EXISTS comments(id TEXT PRIMARY KEY, org TEXT REFERENCES orgs(id), record TEXT REFERENCES records(id) ON DELETE CASCADE, user TEXT REFERENCES users(id), body TEXT NOT NULL, created TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY, org TEXT REFERENCES orgs(id), user TEXT REFERENCES users(id), action TEXT NOT NULL, target TEXT NOT NULL, created TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS platform_admins(user TEXT PRIMARY KEY REFERENCES users(id));
        CREATE TABLE IF NOT EXISTS disabled_users(user TEXT PRIMARY KEY REFERENCES users(id));
        CREATE TABLE IF NOT EXISTS suspended_orgs(org TEXT PRIMARY KEY REFERENCES orgs(id));
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS platform_audit(id INTEGER PRIMARY KEY, user TEXT REFERENCES users(id), action TEXT NOT NULL, target TEXT NOT NULL, created TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS attempts(ip TEXT PRIMARY KEY, count INTEGER NOT NULL, until INTEGER NOT NULL);
        ''')

def now(): return dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')
def uid(): return secrets.token_hex(8)
def digest(token): return hashlib.sha256(token.encode()).hexdigest()
def password_hash(password):
    salt = secrets.token_hex(16)
    return salt + ':' + hashlib.scrypt(password.encode(), salt=salt.encode(), n=16384, r=8, p=1).hex()
def password_ok(password, stored):
    salt, expected = stored.split(':')
    return hmac.compare_digest(hashlib.scrypt(password.encode(), salt=salt.encode(), n=16384, r=8, p=1).hex(), expected)
def require(condition, message, status=400):
    if not condition: raise Problem(status, message)
def audit(c, org, user, action, target): c.execute('INSERT INTO audit(org,user,action,target,created) VALUES(?,?,?,?,?)', (org,user,action,target,now()))
def unpack(r): return dict(json.loads(r['data']), id=r['id'], kind=r['kind'], version=r['version'], created=r['created'], updated=r['updated'])
def text_value(d,key,limit=5000):
    value=d.get(key,'')
    require(isinstance(value,str) and len(value)<=limit, f'Invalid {key}')
    return value.strip()
def record(c, org, rid, kind=None):
    r=c.execute('SELECT * FROM records WHERE org=? AND id=?',(org,rid)).fetchone()
    require(r is not None and (kind is None or r['kind']==kind),'Record not found',404)
    return unpack(r)
def add(c,org,kind,data):
    rid=uid(); stamp=now()
    c.execute('INSERT INTO records VALUES(?,?,?,?,1,?,?)',(rid,org,kind,json.dumps(data),stamp,stamp))
    return rid

def seed(c,org):
    project=add(c,org,'project',{'title':'SprintForge launch','status':'Active','description':'Ship a reliable workspace from requirements through release.','owner':'','due':'','priority':'High'})
    sprint=add(c,org,'sprint',{'title':'Sprint 01 · Foundation','project':project,'status':'Active','description':'Build, review and verify the core experience.','start':dt.date.today().isoformat(),'due':(dt.date.today()+dt.timedelta(days=14)).isoformat()})
    req=add(c,org,'requirement',{'title':'Every release has traceable verification','project':project,'status':'Approved','description':'Link requirements, tickets and executed tests.','acceptance':'Required tests pass before a release can be marked ready.'})
    ticket=add(c,org,'ticket',{'title':'Verify account registration','project':project,'sprint':sprint,'requirement':req,'status':'Ready for Testing','type':'Story','priority':'High','description':'New users create an isolated organization.','acceptance':'A new user can sign in and sees only their own workspace.','assignee':'','points':3})
    test=add(c,org,'test',{'title':'Registration creates an isolated workspace','project':project,'ticket':ticket,'status':'Ready','category':'Integration','steps':'1. Register a new account.\n2. Create a project.\n3. Sign in as another user.','expected':'The project is visible only to organization members.','required':True})
    add(c,org,'run',{'title':'Initial verification','project':project,'test':test,'status':'Passed','actual':'Workspace isolation verified.','environment':'Development','evidence':'','ticket':ticket})
    add(c,org,'ticket',{'title':'Review release acceptance criteria','project':project,'sprint':sprint,'status':'In Progress','type':'Task','priority':'Medium','description':'Review quality gates with the team.','acceptance':'All required cases have current passing results.','assignee':'','points':2})
    add(c,org,'release',{'title':'v1.0 · Foundation','project':project,'status':'Planned','description':'Initial SprintForge release.','due':(dt.date.today()+dt.timedelta(days=14)).isoformat()})

class Handler(BaseHTTPRequestHandler):
    server_version='SprintForge'
    def log_message(self, fmt, *args): pass
    def send(self,status,data,headers=None):
        # Commit before publishing a successful response so immediate reads see it.
        c=getattr(self,'database',None)
        if status<400 and c is not None:
            c.commit()
        payload=json.dumps(data).encode() if not isinstance(data,bytes) else data
        self.send_response(status)
        self.send_header('Content-Type','application/json; charset=utf-8')
        self.send_header('Content-Length',str(len(payload)))
        self.send_header('Cache-Control','no-store')
        self.security()
        for k,v in (headers or {}).items(): self.send_header(k,v)
        self.end_headers(); self.wfile.write(payload)
    def security(self):
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('X-Frame-Options','DENY')
        self.send_header('Referrer-Policy','same-origin')
        self.send_header('Content-Security-Policy',"default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'")
    def cookie(self,token,age=SESSION_AGE):
        secure='; Secure' if os.environ.get('SPRINTFORGE_SECURE_COOKIE')=='1' else ''
        return f'sf_session={token}; HttpOnly; SameSite=Lax; Path=/; Max-Age={age}{secure}'
    def body(self):
        try:
            size=int(self.headers.get('Content-Length','0'))
            require(0<size<=100000,'Request too large or empty',413)
            value=json.loads(self.rfile.read(size))
            require(isinstance(value,dict),'Expected a JSON object')
            return value
        except (ValueError,UnicodeDecodeError): raise Problem(400,'Invalid JSON request')
    def identity(self,c,write=False):
        cookie=http.cookies.SimpleCookie()
        try: cookie.load(self.headers.get('Cookie',''))
        except http.cookies.CookieError: pass
        token=cookie.get('sf_session')
        session=c.execute('SELECT * FROM sessions WHERE token=? AND expires>?',(digest(token.value) if token else '',int(time.time()))).fetchone()
        require(session is not None,'Please sign in',401)
        if write: require(hmac.compare_digest(self.headers.get('X-CSRF-Token',''),session['csrf']),'Invalid security token',403)
        user=c.execute('SELECT id,name,email FROM users WHERE id=?',(session['user'],)).fetchone()
        require(not c.execute('SELECT 1 FROM disabled_users WHERE user=?',(user['id'],)).fetchone(),'Account disabled',403)
        return dict(user),session
    def context(self,c,write=False):
        user,session=self.identity(c,write)
        org=self.headers.get('X-Organization','')
        member=c.execute('SELECT m.*,o.name FROM members m JOIN orgs o ON o.id=m.org WHERE m.user=? AND m.org=?',(user['id'],org)).fetchone()
        require(member is not None,'Organization access denied',403)
        require(not c.execute('SELECT 1 FROM suspended_orgs WHERE org=?',(org,)).fetchone(),'Organization suspended. Contact the platform administrator.',403)
        if write: require(member['role']!='viewer','Viewer access is read-only',403)
        return user,org,member
    def do_GET(self): self.dispatch('GET')
    def do_POST(self): self.dispatch('POST')
    def do_PATCH(self): self.dispatch('PATCH')
    def do_DELETE(self): self.dispatch('DELETE')
    def dispatch(self,method):
        path=self.path.split('?')[0]
        try:
            if not path.startswith('/api/'):
                require(method=='GET','Method not allowed',405)
                return self.static(path)
            if path=='/api/health': return self.send(200,{'status':'ok'})
            with connect() as c:
                self.database=c
                if method!='GET': c.execute('BEGIN IMMEDIATE')
                if path in ('/api/register','/api/login') and method=='POST': return self.auth(c,path,self.body())
                if path=='/api/me' and method=='GET':
                    user,s=self.identity(c)
                    orgs=[dict(x) for x in c.execute('SELECT o.*,m.role FROM members m JOIN orgs o ON o.id=m.org WHERE m.user=?',(user['id'],))]
                    return self.send(200,{'user':user,'csrf':s['csrf'],'organizations':orgs,'superadmin':bool(c.execute('SELECT 1 FROM platform_admins WHERE user=?',(user['id'],)).fetchone())})
                if path=='/api/logout' and method=='POST':
                    _,s=self.identity(c,True); c.execute('DELETE FROM sessions WHERE token=?',(s['token'],))
                    return self.send(200,{'ok':True},{'Set-Cookie':self.cookie('',0)})
                if path.startswith('/api/platform'):
                    user,_=self.identity(c,method!='GET')
                    require(c.execute('SELECT 1 FROM platform_admins WHERE user=?',(user['id'],)).fetchone(),'Platform administrator access required',403)
                    return self.platform(c,user,path,method)
                if path=='/api/organizations' and method=='POST':
                    user,_=self.identity(c,True); d=self.body(); name=text_value(d,'name',100); require(name,'Organization name is required')
                    org=uid(); c.execute('INSERT INTO orgs VALUES(?,?)',(org,name)); c.execute('INSERT INTO members VALUES(?,?,?)',(org,user['id'],'owner'))
                    audit(c,org,user['id'],'created organization',org)
                    return self.send(201,{'id':org})
                user,org,member=self.context(c,method!='GET')
                if path=='/api/organization' and method=='PATCH':
                    require(member['role'] in ('owner','admin'),'Administrator access required',403)
                    name=text_value(self.body(),'name',100); require(name,'Organization name is required')
                    c.execute('UPDATE orgs SET name=? WHERE id=?',(name,org)); audit(c,org,user['id'],'renamed organization',name)
                    return self.send(200,{'ok':True})
                if path=='/api/state' and method=='GET':
                    records=[unpack(r) for r in c.execute('SELECT * FROM records WHERE org=? ORDER BY rowid',(org,))]
                    people=[dict(r) for r in c.execute('SELECT u.id,u.name,u.email,m.role FROM members m JOIN users u ON u.id=m.user WHERE m.org=?',(org,))]
                    comments=[dict(r) for r in c.execute('SELECT co.*,u.name FROM comments co JOIN users u ON u.id=co.user WHERE co.org=? ORDER BY co.created',(org,))]
                    history=[dict(r) for r in c.execute('SELECT a.*,u.name FROM audit a JOIN users u ON u.id=a.user WHERE a.org=? ORDER BY a.id DESC LIMIT 100',(org,))]
                    return self.send(200,{'records':records,'members':people,'comments':comments,'audit':history,'statuses':STATUSES})
                if path=='/api/members' and method in ('POST','PATCH','DELETE'):
                    require(member['role'] in ('owner','admin'),'Administrator access required',403)
                    d=self.body(); email=text_value(d,'email',254).lower()
                    target=c.execute('SELECT id FROM users WHERE email=?',(email,)).fetchone()
                    require(target is not None,'This person must register an account first',404)
                    current=c.execute('SELECT role FROM members WHERE org=? AND user=?',(org,target['id'])).fetchone()
                    require(not current or current['role']!='owner','The organization owner cannot be changed here',403)
                    require(member['role']=='owner' or not current or current['role']!='admin','Only owners can modify administrators',403)
                    if method=='DELETE': c.execute('DELETE FROM members WHERE org=? AND user=?',(org,target['id']))
                    else:
                        role=d.get('role','developer'); require(role in ('admin','manager','developer','tester','viewer'),'Invalid role')
                        require(member['role']=='owner' or role!='admin','Only owners can grant administrator access',403)
                        require(member['role']=='owner' or not current or current['role']!='admin','Only owners can modify administrators',403)
                        c.execute('INSERT INTO members VALUES(?,?,?) ON CONFLICT(org,user) DO UPDATE SET role=excluded.role',(org,target['id'],role))
                    audit(c,org,user['id'],'member '+method.lower(),email)
                    return self.send(200,{'ok':True})
                if path=='/api/password' and method=='POST':
                    d=self.body(); stored=c.execute('SELECT password FROM users WHERE id=?',(user['id'],)).fetchone()[0]
                    require(password_ok(text_value(d,'current',256),stored),'Current password is incorrect',403)
                    password=text_value(d,'password',256); require(len(password)>=12,'Use at least 12 characters')
                    c.execute('UPDATE users SET password=? WHERE id=?',(password_hash(password),user['id']))
                    _,s=self.identity(c); c.execute('DELETE FROM sessions WHERE user=? AND token!=?',(user['id'],s['token']))
                    return self.send(200,{'ok':True})
                if path=='/api/comments' and method=='POST':
                    d=self.body(); rid=text_value(d,'record',40); record(c,org,rid)
                    body=text_value(d,'body'); require(body,'Comment cannot be empty')
                    c.execute('INSERT INTO comments VALUES(?,?,?,?,?,?)',(uid(),org,rid,user['id'],body,now()))
                    audit(c,org,user['id'],'commented',rid)
                    return self.send(201,{'ok':True})
                match=re.fullmatch(r'/api/records(?:/([a-f0-9]+))?',path)
                if match:
                    rid=match.group(1)
                    if method=='DELETE':
                        require(member['role'] in ('owner','admin','manager'),'Manager access required',403)
                        existing=record(c,org,rid)
                        require(existing['kind']!='run','Test executions cannot be deleted',409)
                        linked=[r for r in c.execute('SELECT id,data FROM records WHERE org=? AND id!=?',(org,rid)) if rid in json.loads(r['data']).values()]
                        require(not linked,'Remove linked references before deleting',409)
                        c.execute('DELETE FROM records WHERE org=? AND id=?',(org,rid)); audit(c,org,user['id'],'deleted',rid)
                        return self.send(200,{'ok':True})
                    if method in ('POST','PATCH'):
                        d=self.body(); old=record(c,org,rid) if rid else None
                        kind=old['kind'] if old else d.get('kind')
                        require(kind in KINDS,'Invalid record type')
                        if kind=='run': require(method=='POST','Test executions are immutable; record a new execution',409)
                        if old: require(d.get('version')==old['version'],'This item changed. Refresh before saving.',409)
                        data=self.validate(c,org,kind,d,old,user)
                        if old:
                            c.execute('UPDATE records SET data=?,version=version+1,updated=? WHERE id=? AND org=?',(json.dumps(data),now(),rid,org))
                        else: rid=add(c,org,kind,data)
                        if kind=='run' and data['status']=='Failed' and d.get('create_defect'):
                            bug=add(c,org,'ticket',{'title':'Failed: '+data['title'],'project':data['project'],'test':data['test'],'run':rid,'status':'Backlog','type':'Bug','priority':'High','description':data.get('actual',''),'acceptance':'Linked test passes on retest.','assignee':'','points':0})
                            data['defect']=bug; c.execute('UPDATE records SET data=? WHERE id=?',(json.dumps(data),rid))
                        audit(c,org,user['id'],'updated '+kind if old else 'created '+kind,rid)
                        return self.send(200 if old else 201,record(c,org,rid))
                raise Problem(404,'Endpoint not found')
        except Problem as e: self.send(e.status,{'error':e.message})
        except sqlite3.IntegrityError: self.send(409,{'error':'This record already exists or has linked data'})
        except Exception:
            import traceback; traceback.print_exc()
            self.send(500,{'error':'An unexpected error occurred'})
    def auth(self,c,path,d):
        ip=self.client_address[0]; attempt=c.execute('SELECT * FROM attempts WHERE ip=?',(ip,)).fetchone()
        require(not attempt or attempt['until']<time.time() or attempt['count']<20,'Too many attempts. Try again in 15 minutes.',429)
        email=text_value(d,'email',254).lower(); password=text_value(d,'password',256)
        require(re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',email),'Enter a valid email')
        if path=='/api/register':
            setting=c.execute("SELECT value FROM settings WHERE key='registration'").fetchone()
            require((setting[0] if setting else os.environ.get('SPRINTFORGE_REGISTRATION','1'))=='1','Registration is closed',403)
            require(len(password)>=12,'Use at least 12 password characters')
            name=text_value(d,'name',100); orgname=text_value(d,'organization',100)
            require(name and orgname,'Name and organization are required')
            user=uid(); org=uid()
            c.execute('INSERT INTO users VALUES(?,?,?,?,?)',(user,name,email,password_hash(password),now()))
            c.execute('INSERT INTO orgs VALUES(?,?)',(org,orgname)); c.execute('INSERT INTO members VALUES(?,?,?)',(org,user,'owner'))
            if d.get('sample') is True: seed(c,org)
            audit(c,org,user,'created organization',org)
        else:
            row=c.execute('SELECT * FROM users WHERE email=?',(email,)).fetchone()
            if not row or not password_ok(password,row['password']):
                count=attempt['count']+1 if attempt and attempt['until']>time.time() else 1
                c.execute('INSERT OR REPLACE INTO attempts VALUES(?,?,?)',(ip,count,int(time.time())+900)); c.commit()
                raise Problem(401,'Email or password is incorrect')
            require(not c.execute('SELECT 1 FROM disabled_users WHERE user=?',(row['id'],)).fetchone(),'Account disabled',403)
            user=row['id']; c.execute('DELETE FROM attempts WHERE ip=?',(ip,))
        token=secrets.token_urlsafe(32); csrf=secrets.token_urlsafe(32)
        c.execute('DELETE FROM sessions WHERE expires<?',(int(time.time()),))
        c.execute('INSERT INTO sessions VALUES(?,?,?,?)',(digest(token),user,csrf,int(time.time())+SESSION_AGE))
        c.commit()
        self.send(200,{'ok':True},{'Set-Cookie':self.cookie(token)})
    def platform(self,c,user,path,method):
        if path=='/api/platform' and method=='GET':
            tenants=[dict(r) for r in c.execute("SELECT o.*, EXISTS(SELECT 1 FROM suspended_orgs s WHERE s.org=o.id) suspended, (SELECT count(*) FROM members m WHERE m.org=o.id) members, (SELECT count(*) FROM records r WHERE r.org=o.id) records FROM orgs o ORDER BY o.name")]
            users=[dict(r) for r in c.execute("SELECT u.id,u.name,u.email,u.created,EXISTS(SELECT 1 FROM disabled_users d WHERE d.user=u.id) disabled,EXISTS(SELECT 1 FROM platform_admins p WHERE p.user=u.id) superadmin FROM users u ORDER BY u.created")]
            history=[dict(r) for r in c.execute('SELECT p.*,u.name FROM platform_audit p JOIN users u ON u.id=p.user ORDER BY p.id DESC LIMIT 100')]
            settings=dict(c.execute('SELECT key,value FROM settings').fetchall())
            return self.send(200,{'tenants':tenants,'users':users,'audit':history,'settings':settings,'health':{'database':'connected','storage':'SQLite WAL','version':'1.0.0'}})
        require(method=='POST','Method not allowed',405)
        d=self.body(); action=d.get('action'); target=text_value(d,'target',100)
        if action in ('suspend_tenant','restore_tenant','rename_tenant'):
            require(c.execute('SELECT 1 FROM orgs WHERE id=?',(target,)).fetchone(),'Tenant not found',404)
            own=c.execute('SELECT 1 FROM members WHERE org=? AND user=?',(target,user['id'])).fetchone()
            require(action!='suspend_tenant' or not own,'Use another administrator to suspend your own organization',409)
            if action=='suspend_tenant': c.execute('INSERT OR IGNORE INTO suspended_orgs VALUES(?)',(target,))
            elif action=='restore_tenant': c.execute('DELETE FROM suspended_orgs WHERE org=?',(target,))
            else:
                name=text_value(d,'name',100); require(name,'Name is required'); c.execute('UPDATE orgs SET name=? WHERE id=?',(name,target))
        elif action in ('disable_user','enable_user','grant_superadmin','revoke_superadmin','revoke_sessions'):
            require(c.execute('SELECT 1 FROM users WHERE id=?',(target,)).fetchone(),'User not found',404)
            require(target!=user['id'],'You cannot modify your own platform access',409)
            if action in ('disable_user','revoke_superadmin'):
                admin=c.execute('SELECT 1 FROM platform_admins WHERE user=?',(target,)).fetchone()
                if admin: require(c.execute('SELECT count(*) FROM platform_admins p LEFT JOIN disabled_users d ON d.user=p.user WHERE d.user IS NULL').fetchone()[0]>1,'Keep at least one active super admin',409)
            if action=='disable_user': c.execute('INSERT OR IGNORE INTO disabled_users VALUES(?)',(target,))
            if action=='enable_user': c.execute('DELETE FROM disabled_users WHERE user=?',(target,))
            if action=='grant_superadmin': c.execute('INSERT OR IGNORE INTO platform_admins VALUES(?)',(target,))
            if action=='revoke_superadmin': c.execute('DELETE FROM platform_admins WHERE user=?',(target,))
            if action in ('disable_user','revoke_sessions'): c.execute('DELETE FROM sessions WHERE user=?',(target,))
        elif action=='registration':
            value=d.get('value'); require(value in ('0','1'),'Invalid registration setting')
            c.execute("INSERT OR REPLACE INTO settings VALUES('registration',?)",(value,)); target=value
        else: raise Problem(400,'Unknown platform action')
        c.execute('INSERT INTO platform_audit(user,action,target,created) VALUES(?,?,?,?)',(user['id'],action,target,now()))
        return self.send(200,{'ok':True})
    def validate(self,c,org,kind,d,old,user):
        data={key:old[key] for key in ('defect','run') if old and key in old}
        for key in ('title','description','acceptance','steps','expected','actual','environment','evidence','due','start','assignee','priority','type','category','project','sprint','ticket','test','requirement','release','owner'):
            if key in d: data[key]=text_value(d,key)
        require(data.get('title'),'Title is required')
        require(len(data['title'])<=200,'Title is too long')
        data['status']=d.get('status',STATUSES[kind][0]); require(data['status'] in STATUSES[kind],'Invalid status')
        for key in ('due','start'):
            if data.get(key):
                try: dt.date.fromisoformat(data[key])
                except ValueError: raise Problem(400,'Use a valid date')
        if data.get('start') and data.get('due'): require(data['start']<=data['due'],'End date must follow start date')
        if kind!='project':
            require(data.get('project'),'Select a project'); record(c,org,data['project'],'project')
        for field,refkind in [('sprint','sprint'),('ticket','ticket'),('test','test'),('requirement','requirement'),('release','release')]:
            if data.get(field):
                ref=record(c,org,data[field],refkind)
                require(ref.get('project')==data.get('project'),'Linked records must belong to the same project')
        for field in ('assignee','owner'):
            if data.get(field): require(c.execute('SELECT 1 FROM members WHERE org=? AND user=?',(org,data[field])).fetchone(),'Select an organization member')
        if kind=='ticket':
            require(data.get('type','Task') in ('Task','Story','Bug','Epic'),'Invalid ticket type')
            try: data['points']=int(d.get('points',0))
            except (ValueError,TypeError): raise Problem(400,'Points must be a number')
            require(0<=data['points']<=100,'Points must be 0–100')
            if data['status']=='Done':
                require(data.get('acceptance'),'Acceptance criteria are required before completion')
                self.gate(c,org,data['project'],old['id'] if old else '')
        if kind=='test': data['required']=bool(d.get('required',False))
        if kind=='run':
            require(data.get('test'),'Select a test case'); test=record(c,org,data['test'],'test')
            require(test['status']=='Ready','Only ready test cases can be executed')
            require(data.get('actual'),'Record the actual result')
            evidence=data.get('evidence',''); require(not evidence or evidence.startswith('https://'),'Evidence links must use HTTPS')
            data['test_version']=test['version']; data['executed_by']=user['id']
        if kind=='release' and data['status'] in ('Ready','Released'): self.gate(c,org,data['project'])
        return data
    def gate(self,c,org,project,ticket=None):
        records=[unpack(r) for r in c.execute('SELECT * FROM records WHERE org=? ORDER BY rowid',(org,))]
        tests=[r for r in records if r['kind']=='test' and r.get('project')==project and r.get('required') and r['status']!='Deprecated' and (ticket is None or r.get('ticket')==ticket)]
        for test in tests:
            runs=[r for r in records if r['kind']=='run' and r.get('test')==test['id']]
            require(test['status']=='Ready' and runs and runs[-1]['status']=='Passed' and runs[-1].get('test_version',1)==test['version'],'Quality gate: required tests need a current passing execution',409)
        if ticket is None:
            require(not any(r['kind']=='ticket' and r.get('project')==project and r.get('type')=='Bug' and r['status']!='Done' for r in records),'Quality gate: close open defects before release',409)
    def static(self,path):
        paths={'/':'index.html','/app':'index.html','/terms':'terms.html','/privacy':'privacy.html','/copyright':'copyright.html'}
        rel=paths.get(path,path.removeprefix('/static/') if path.startswith('/static/') else '')
        file=(ROOT/'static'/rel).resolve()
        require(rel and file.is_relative_to((ROOT/'static').resolve()) and file.is_file(),'Not found',404)
        types={'.html':'text/html; charset=utf-8','.css':'text/css; charset=utf-8','.js':'text/javascript; charset=utf-8','.png':'image/png','.webp':'image/webp','.svg':'image/svg+xml'}
        payload=file.read_bytes(); self.send_response(200); self.send_header('Content-Type',types.get(file.suffix,'application/octet-stream')); self.send_header('Content-Length',str(len(payload))); self.security(); self.end_headers(); self.wfile.write(payload)

if __name__=='__main__':
    init_db()
    host=os.environ.get('HOST','127.0.0.1'); port=int(os.environ.get('PORT','8080'))
    print(f'SprintForge listening on http://{host}:{port}',flush=True)
    ThreadingHTTPServer((host,port),Handler).serve_forever()
