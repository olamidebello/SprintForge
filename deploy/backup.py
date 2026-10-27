"""Create a consistent SQLite backup without stopping the service."""
import argparse, os, sqlite3
p=argparse.ArgumentParser();p.add_argument('source');p.add_argument('destination');a=p.parse_args()
if os.path.exists(a.destination):p.error('Destination already exists; choose a new filename')
fd=os.open(a.destination,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600);os.close(fd)
try:
    with sqlite3.connect('file:'+os.path.abspath(a.source)+'?mode=ro',uri=True) as source, sqlite3.connect(a.destination) as dest: source.backup(dest)
except Exception:
    os.unlink(a.destination);raise
print('Backup created')
