import http.cookiejar, json, os, tempfile, threading, unittest, urllib.request, urllib.error
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import app

class Client:
    def __init__(self,url):
        self.url=url; self.jar=http.cookiejar.CookieJar(); self.open=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar)); self.csrf='';self.org=''
    def request(self,path,method='GET',data=None):
        req=urllib.request.Request(self.url+'/api/'+path,data=json.dumps(data).encode() if data is not None else None,method=method,headers={'Content-Type':'application/json','X-CSRF-Token':self.csrf,'X-Organization':self.org})
        try:
            with self.open.open(req) as r:return r.status,json.load(r)
        except urllib.error.HTTPError as e:return e.code,json.load(e)
    def register(self,email):
        status,_=self.request('register','POST',{'name':email,'email':email,'password':'a-strong-password-123','organization':email,'sample':False})
        assert status==200
        _,self.me=self.request('me');self.csrf=self.me['csrf'];self.org=self.me['organizations'][0]['id']
        return self
    def create(self,kind,**data):return self.request('records','POST',{'kind':kind,**data})

class AppTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();app.DB=self.tmp.name+'/test.db';app.init_db()
        self.server=app.ThreadingHTTPServer(('127.0.0.1',0),app.Handler);self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        url='http://127.0.0.1:'+str(self.server.server_port);self.a=Client(url).register('a@example.com');self.b=Client(url).register('b@example.com')
        _,self.p=self.a.create('project',title='Project',status='Active')
    def tearDown(self):self.server.shutdown();self.server.server_close();self.tmp.cleanup()
    def test_tenant_isolation(self):
        _,data=self.b.request('state');self.assertEqual(data['records'],[])
        self.b.org=self.a.org
        self.assertEqual(self.b.request('state')[0],403)
        self.assertEqual(self.b.request('records/'+self.p['id'],'PATCH',{**self.p,'title':'Stolen'})[0],403)
    def test_csrf_and_conflict(self):
        token=self.a.csrf;self.a.csrf='bad';self.assertEqual(self.a.create('project',title='No')[0],403);self.a.csrf=token
        self.assertEqual(self.a.request('records/'+self.p['id'],'PATCH',{**self.p,'title':'New'})[0],200)
        self.assertEqual(self.a.request('records/'+self.p['id'],'PATCH',{**self.p,'title':'Old'})[0],409)
    def test_viewer_and_member_controls(self):
        self.assertEqual(self.a.request('members','POST',{'email':'b@example.com','role':'viewer'})[0],200)
        self.b.org=self.a.org
        self.assertEqual(self.b.request('state')[0],200)
        self.assertEqual(self.b.create('project',title='Forbidden')[0],403)
        self.assertEqual(self.b.request('members','POST',{'email':'a@example.com','role':'admin'})[0],403)
        self.assertEqual(self.a.request('members','DELETE',{'email':'a@example.com'})[0],403)
    def test_admin_cannot_remove_admin(self):
        self.a.request('members','POST',{'email':'b@example.com','role':'admin'});self.b.org=self.a.org
        self.assertEqual(self.b.request('members','DELETE',{'email':'b@example.com'})[0],403)
    def test_link_scope(self):
        _,p2=self.a.create('project',title='Second')
        _,ticket=self.a.create('ticket',title='Ticket',project=p2['id'])
        self.assertEqual(self.a.create('test',title='Invalid link',project=self.p['id'],ticket=ticket['id'])[0],400)
    def test_quality_gate_defect_and_retest(self):
        project=self.p['id'];_,ticket=self.a.create('ticket',title='Story',project=project,type='Story',acceptance='Works')
        _,test=self.a.create('test',title='Check',project=project,ticket=ticket['id'],status='Ready',required=True)
        release={'title':'Release','project':project,'status':'Ready'}
        self.assertEqual(self.a.create('release',**release)[0],409)
        status,run=self.a.create('run',title='Fail',project=project,test=test['id'],status='Failed',actual='Broken',create_defect=True)
        self.assertEqual(status,201);self.assertIn('defect',run)
        self.assertEqual(self.a.request('records/'+run['id'],'PATCH',{**run,'status':'Passed'})[0],409)
        self.assertEqual(self.a.request('records/'+ticket['id'],'PATCH',{**ticket,'status':'Done'})[0],409)
        self.a.create('run',title='Retest',project=project,test=test['id'],status='Passed',actual='Fixed')
        self.assertEqual(self.a.create('release',**release)[0],409)
        _,state=self.a.request('state');bug=next(r for r in state['records'] if r['id']==run['defect'])
        self.assertEqual(self.a.request('records/'+bug['id'],'PATCH',{**bug,'status':'Done'})[0],200)
        self.assertEqual(self.a.create('release',**release)[0],201)
        self.a.request('records/'+test['id'],'PATCH',{**test,'steps':'Changed'})
        self.assertEqual(self.a.create('release',**release)[0],409)
    def test_superadmin_controls(self):
        self.assertEqual(self.a.request('platform')[0],403)
        with app.connect() as c:c.execute('INSERT INTO platform_admins VALUES(?)',(self.a.me['user']['id'],))
        self.assertEqual(self.a.request('platform')[0],200)
        self.assertEqual(self.a.request('platform','POST',{'action':'suspend_tenant','target':self.b.org})[0],200)
        self.assertEqual(self.b.request('state')[0],403)
        self.a.request('platform','POST',{'action':'restore_tenant','target':self.b.org})
        self.assertEqual(self.b.request('state')[0],200)
        self.a.request('platform','POST',{'action':'disable_user','target':self.b.me['user']['id']})
        self.assertEqual(self.b.request('me')[0],401)
        self.assertEqual(self.b.request('login','POST',{'email':'b@example.com','password':'a-strong-password-123'})[0],403)
        self.assertEqual(self.a.request('platform','POST',{'action':'revoke_superadmin','target':self.a.me['user']['id']})[0],409)
    def test_registration_and_login_limits(self):
        with app.connect() as c:c.execute("INSERT INTO platform_admins VALUES(?)",(self.a.me['user']['id'],))
        self.a.request('platform','POST',{'action':'registration','value':'0'})
        self.assertEqual(self.b.request('register','POST',{'name':'C','organization':'C','email':'c@example.com','password':'a-strong-password-123'})[0],403)
        for i in range(20):self.b.request('login','POST',{'email':'bad@example.com','password':'bad'})
        self.assertEqual(self.b.request('login','POST',{'email':'bad@example.com','password':'bad'})[0],429)
    def test_comments_and_deletion_links(self):
        _,ticket=self.a.create('ticket',title='Commented',project=self.p['id'])
        self.assertEqual(self.a.request('comments','POST',{'record':ticket['id'],'body':'Checked'})[0],201)
        self.assertEqual(self.b.request('comments','POST',{'record':ticket['id'],'body':'Intrusion'})[0],404)
        self.assertEqual(self.a.request('records/'+self.p['id'],'DELETE')[0],409)
        self.assertEqual(self.a.request('records/'+ticket['id'],'DELETE')[0],200)
    def test_legal_pages_public_and_company_named(self):
        for page in ('terms','privacy','copyright'):
            with urllib.request.urlopen(self.a.url+'/'+page) as response:
                self.assertEqual(response.status,200)
                self.assertIn('Olamide Olatayo Bello Corporation',response.read().decode())
    def test_existing_account_cli_promotion_is_idempotent(self):
        import subprocess
        env=dict(os.environ,SPRINTFORGE_DB=app.DB)
        command=[sys.executable,str(Path(app.__file__).parent/'manage.py'),'create-superadmin','a@example.com','--existing-only']
        one=subprocess.run(command,env=env,capture_output=True,text=True)
        self.assertEqual(one.returncode,0,one.stderr)
        two=subprocess.run(command,env=env,capture_output=True,text=True)
        self.assertEqual(two.returncode,0,two.stderr)
        self.assertIn('Already',two.stdout)
        self.assertEqual(self.a.request('platform')[0],200)
        with app.connect() as c:self.assertEqual(c.execute("SELECT count(*) FROM platform_audit WHERE action='CLI grant superadmin'").fetchone()[0],1)
        missing=subprocess.run(command[:-2]+['missing@example.com','--existing-only'],env=env,capture_output=True,text=True)
        self.assertNotEqual(missing.returncode,0)
    def test_password_session_revocation(self):
        other=Client(self.a.url);other.request('login','POST',{'email':'a@example.com','password':'a-strong-password-123'})
        self.assertEqual(other.request('me')[0],200)
        self.assertEqual(self.a.request('password','POST',{'current':'a-strong-password-123','password':'new-strong-password-123'})[0],200)
        self.assertEqual(other.request('me')[0],401)
        self.assertEqual(self.a.request('me')[0],200)

if __name__=='__main__':unittest.main()
