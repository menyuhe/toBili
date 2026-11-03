"""Back up and update the existing BaoTa toBili process; no credentials in arguments/logs."""
import argparse,json,os,signal,subprocess,time,shutil,tarfile
from pathlib import Path
from urllib.parse import urlsplit,unquote
import urllib.request

p=argparse.ArgumentParser();p.add_argument('--pid',type=int,required=True);p.add_argument('--root',type=Path,default=Path('/www/wwwroot/tobill'));args=p.parse_args()
root=args.root.resolve();stage=Path(__file__).resolve().parent
proc=Path('/proc')/str(args.pid)
assert proc.joinpath('cwd').resolve()==root
assert b'cloud.app:create_app' in proc.joinpath('cmdline').read_bytes()
env=dict(x.split('=',1) for x in proc.joinpath('environ').read_text().split(chr(0)) if '=' in x)
uid=proc.stat().st_uid;gid=proc.stat().st_gid
url=urlsplit(env['RELAY_DATABASE_URL']);assert url.scheme.startswith('postgresql')
backup=root/'.deploy-backups'/('catalog-'+time.strftime('%Y%m%d-%H%M%S'));backup.mkdir(parents=True,mode=0o700)
pg_env={**os.environ,'PGPASSWORD':unquote(url.password or '')}
pg=['--host',url.hostname or '127.0.0.1','--port',str(url.port or 5432),'--username',unquote(url.username or '')]
db=unquote(url.path.lstrip('/'));dump=backup/'database.dump'
subprocess.run(['pg_dump',*pg,'--format=custom','--file',str(dump),db],env=pg_env,check=True)
assert dump.stat().st_size>0
with tarfile.open(backup/'code.tar.gz','w:gz') as out:
    for name in ('cloud','src','ToBili'):out.add(root/name,arcname=name)
manifest=json.loads((stage/'manifest.json').read_text())
import hashlib
for name,sha in manifest.items():
    source=(stage/name).resolve();assert source.is_relative_to(stage) and (name.startswith('cloud/') or name.startswith('src/'))
    assert hashlib.sha256(source.read_bytes()).hexdigest()==sha
print('BACKUP_READY',str(backup),flush=True)
def launch():
    log=open(backup/'service.log','ab',buffering=0)
    return subprocess.Popen([str(root/'ToBili')],cwd=root,env=env,user=uid,group=gid,stdout=log,stderr=log,start_new_session=True)
def stop(pid):
    os.kill(pid,signal.SIGTERM)
    for _ in range(60):
        if not Path('/proc',str(pid)).exists():return
        time.sleep(.25)
    raise RuntimeError('Existing process did not exit; refusing concurrent restart')
stop(args.pid)
try:
    for name in manifest:
        dest=root/name;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(stage/name,dest);os.chmod(dest,0o644)
    process=launch()
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for _ in range(60):
        if process.poll() is not None:raise RuntimeError('New service exited; see backup service.log')
        try:
            with opener.open('http://127.0.0.1:18788/api/v1/health',timeout=2) as response:health=json.load(response)
            if health.get('mode')=='activity_catalog':break
        except Exception:pass
        time.sleep(1)
    else:raise RuntimeError('Health check timed out')
    from sqlalchemy import create_engine,text
    engine=create_engine(env['RELAY_DATABASE_URL'])
    with engine.connect() as conn:
        counts={table:conn.scalar(text('select count(*) from '+table)) for table in ['relay_catalog','relay_records','relay_tasks','relay_receipts','relay_users','relay_reposts']}
    assert counts['relay_records']==counts['relay_tasks']==counts['relay_receipts']==0
    (backup/'result.json').write_text(json.dumps({'health':health,'counts':counts,'pid':process.pid},indent=2))
    print('DEPLOY_OK',json.dumps({'health':health,'counts':counts,'pid':process.pid}),flush=True)
except BaseException:
    if 'process' in locals() and process.poll() is None:stop(process.pid)
    with tarfile.open(backup/'code.tar.gz') as old:old.extractall(root,filter='data')
    subprocess.run(['pg_restore',*pg,'--clean','--if-exists','--dbname',db,str(dump)],env=pg_env,check=True)
    launch()
    print('ROLLED_BACK',str(backup),flush=True)
    raise
