"""Administrative CLI. Run locally on the application server."""
import argparse, getpass
import app
p=argparse.ArgumentParser(); sub=p.add_subparsers(dest='command',required=True)
b=sub.add_parser('create-superadmin'); b.add_argument('email'); b.add_argument('--name',default='Platform Administrator'); b.add_argument('--organization',default='SprintForge Operations')
a=p.parse_args(); app.init_db()
with app.connect() as c:
    email=a.email.lower().strip()
    row=c.execute('SELECT id FROM users WHERE email=?',(email,)).fetchone()
    if row: user=row['id']
    else:
        password=getpass.getpass('Password (12+ characters): ')
        if len(password)<12: p.error('Password must contain at least 12 characters')
        if password!=getpass.getpass('Repeat password: '): p.error('Passwords do not match')
        user=app.uid(); org=app.uid()
        c.execute('INSERT INTO users VALUES(?,?,?,?,?)',(user,a.name,email,app.password_hash(password),app.now()))
        c.execute('INSERT INTO orgs VALUES(?,?)',(org,a.organization)); c.execute('INSERT INTO members VALUES(?,?,?)',(org,user,'owner'))
    c.execute('INSERT OR IGNORE INTO platform_admins VALUES(?)',(user,))
    c.execute('INSERT INTO platform_audit(user,action,target,created) VALUES(?,?,?,?)',(user,'CLI grant superadmin',user,app.now()))
print('Super administrator is ready. Sign in through the web interface.')
