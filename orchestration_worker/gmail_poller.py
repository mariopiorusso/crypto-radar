"""Isolated Phase-1 worker. No imports from Crypto Radar or scanner DB access."""
import argparse
import base64
import email.policy
import hashlib
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import re
import sqlite3
import subprocess
from contextlib import contextmanager
from datetime import datetime, timezone
from email.parser import BytesParser
from email.utils import getaddresses
from windows_job import Job

ROOT = Path(__file__).resolve().parent
SEND_SCOPE = 'https://www.googleapis.com/auth/gmail.send'
SCOPES = ['https://www.googleapis.com/auth/gmail.readonly', SEND_SCOPE]
LOG = logging.getLogger('handshake_worker')
ID_PATTERN = re.compile(r'HANDSHAKE-[A-Z0-9][A-Z0-9_-]{0,63}\Z')


def now():
    return datetime.now(timezone.utc).isoformat()


def atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix+'.tmp')
    temporary.write_text(json.dumps(data, indent=2), encoding='utf-8')
    temporary.replace(path)


def config(path):
    path = Path(path).resolve()
    cfg = json.loads((ROOT/'config.example.json').read_text())
    raw = json.loads(path.read_text(encoding='utf-8-sig'))
    if set(raw)-set(cfg):
        raise ValueError('Unknown configuration keys')
    cfg.update(raw)
    if type(cfg['reply_enabled']) is not bool:
        raise ValueError('reply_enabled must be boolean')
    if type(cfg['engineering_enabled']) is not bool:
        raise ValueError('engineering_enabled must be boolean')
    if cfg['windows_sandbox'] not in ('elevated','unelevated'):
        raise ValueError('Invalid Windows sandbox implementation')
    if type(cfg['engineering_timeout_seconds']) is not int or not 30 <= cfg['engineering_timeout_seconds'] <= 1200:
        raise ValueError('Invalid engineering timeout')
    address = r'[^\s<>@]+@[^\s<>@]+'
    if not isinstance(cfg['allowed_senders'],list) or any(not isinstance(s,str) or not re.fullmatch(address,s) for s in cfg['allowed_senders']):
        raise ValueError('Invalid sender allow-list')
    if not re.fullmatch(address,cfg['mailbox']):
        raise ValueError('Invalid mailbox')
    for key, maximum in [('lookback_days',30),('max_messages',5000),('max_tasks_per_poll',10),('timeout_seconds',600)]:
        if type(cfg[key]) is not int or not 1 <= cfg[key] <= maximum:
            raise ValueError('Invalid polling limit')
    for key in ('credentials_path','token_path','state_path','log_path','report_path'):
        p = (path.parent/cfg[key]).resolve()
        if not p.is_relative_to(ROOT):
            raise ValueError('Worker files must stay inside orchestration_worker')
        cfg[key] = str(p)
    cfg['repository'] = str(Path(cfg['repository']).resolve())
    if cfg['engineering_repository']:
        checkout=Path(cfg['engineering_repository']).resolve()
        if not checkout.is_relative_to(ROOT/'workspaces') or not (checkout/'.git').is_dir():
            raise ValueError('Engineering checkout must be a Git repository inside worker workspaces')
        cfg['engineering_repository']=str(checkout)
    if not Path(cfg['repository']).is_dir() or Path(cfg['repository']) == ROOT:
        raise ValueError('Repository directory not found')
    executable=Path(cfg['codex_executable'])
    if not executable.is_absolute() or executable.name.lower()!='codex.exe':
        raise ValueError('Configure an absolute native codex.exe path, not a shell wrapper')
    cfg['allowed_senders'] = [s.lower() for s in cfg['allowed_senders']]
    return cfg


@contextmanager
def process_lock(path):
    path = Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('a+b') as handle:
        handle.seek(0)
        if not handle.read(1):
            handle.write(b'0')
            handle.flush()
        handle.seek(0)
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == 'nt':
                msvcrt.locking(handle.fileno(),msvcrt.LK_UNLCK,1)
            else:
                fcntl.flock(handle.fileno(),fcntl.LOCK_UN)


def parse_message(raw, cfg, include_body=False, sent_by_mailbox=False):
    if len(raw) > 65536:
        raise ValueError('message_too_large')
    message = BytesParser(policy=email.policy.default).parsebytes(raw)
    if message.defects:
        raise ValueError('malformed_message')
    for header in ('Subject','From'):
        if len(message.get_all(header,[])) != 1:
            raise ValueError('missing_or_duplicate_header')
    subject = str(message['Subject'])
    if '[CRADAR TASK]' not in subject:
        raise ValueError('invalid_subject')
    if any(str(value).strip().lower() != 'no' for value in message.get_all('Auto-Submitted', [])):
        raise ValueError('automated_message')
    senders = getaddresses([str(message['From'])])
    if len(senders)!=1 or senders[0][1].lower() not in cfg['allowed_senders']:
        raise ValueError('unauthorized_sender')
    sender = senders[0][1].lower()
    # Require Gmail's receiver-added result, not just the easily forged From field.
    auth = [str(h) for h in message.get_all('Authentication-Results',[])
            if re.match(r'^\s*mx\.google\.com\s*;',str(h),re.I)]
    domain = sender.rsplit('@',1)[1]
    trusted_self = sent_by_mailbox and sender == cfg['mailbox'].lower()
    if not trusted_self and (not auth or not re.search(r'\bdmarc=pass\b[^;]*\bheader\.from='+re.escape(domain)+r'(?=[\s;)]|$)',auth[0],re.I)):
        raise ValueError('sender_authentication_failed')
    parts = [p for p in message.walk() if p.get_content_type()=='text/plain'
             and p.get_content_disposition()!='attachment']
    if len(parts)!=1 or any(p.get_content_disposition()=='attachment' for p in message.walk()):
        raise ValueError('require_plain_text_without_attachments')
    body = parts[0].get_content().replace('\r\n','\n')
    # Preserve legacy IDs for deduplication; all other messages get a stable ID.
    # Email-supplied MODE never changes the worker's read-only permissions.
    values = re.findall(r'^TASK_ID:[ \t]*([^\n]*)$',body,re.M)
    legacy = values[0].strip() if len(values)==1 else ''
    task_id = legacy if ID_PATTERN.fullmatch(legacy) else 'EMAIL-'+hashlib.sha256(raw).hexdigest()
    request = 'Subject: '+subject+'\n\n'+body.strip()
    return (task_id, redact(request)) if include_body else task_id


class State:
    def __init__(self,path):
        Path(path).parent.mkdir(parents=True,exist_ok=True)
        self.db = sqlite3.connect(path,timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.execute('''CREATE TABLE IF NOT EXISTS tasks (
            gmail_message_id TEXT PRIMARY KEY,task_id TEXT UNIQUE,received_at TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('PENDING','RUNNING','COMPLETED','FAILED','REJECTED')),
            started_at TEXT,completed_at TEXT,exit_code INTEGER,error TEXT)''')
        self.db.execute('''CREATE TABLE IF NOT EXISTS replies (
            gmail_message_id TEXT PRIMARY KEY REFERENCES tasks(gmail_message_id),
            report_json TEXT NOT NULL,status TEXT NOT NULL,attempted_at TEXT,
            sent_at TEXT,sent_message_id TEXT,error TEXT)''')
        if 'request_text' not in {row[1] for row in self.db.execute('PRAGMA table_info(tasks)')}:
            self.db.execute("ALTER TABLE tasks ADD COLUMN request_text TEXT NOT NULL DEFAULT ''")
        self.db.commit()

    def record(self,mid,tid,received,error=None,request_text=''):
        self.db.execute('BEGIN IMMEDIATE')
        try:
            if self.db.execute('SELECT 1 FROM tasks WHERE gmail_message_id=?',(mid,)).fetchone():
                self.db.rollback()
                return 'duplicate_message'
            if tid and self.db.execute('SELECT 1 FROM tasks WHERE task_id=?',(tid,)).fetchone():
                tid,error = None,'duplicate_task_id'
            state = 'REJECTED' if error else 'PENDING'
            self.db.execute('INSERT INTO tasks(gmail_message_id,task_id,received_at,status,error,request_text) VALUES (?,?,?,?,?,?)',
                            (mid,tid,received,state,error,request_text if not error else ''))
            self.db.commit()
            return state
        except BaseException:
            self.db.rollback()
            raise

    def recover(self):
        with self.db:
            self.db.execute("UPDATE replies SET status='DELIVERY_UNKNOWN',error='interrupted_during_send' WHERE status='SENDING'")
            self.db.execute("UPDATE tasks SET status='FAILED',completed_at=?,error='interrupted_no_automatic_retry' WHERE status='RUNNING'",(now(),))

    def pending(self,limit):
        return self.db.execute("SELECT * FROM tasks WHERE status='PENDING' ORDER BY received_at,gmail_message_id LIMIT ?",(limit,)).fetchall()

    def claim(self,mid):
        with self.db:
            return self.db.execute("UPDATE tasks SET status='RUNNING',started_at=? WHERE gmail_message_id=? AND status='PENDING'",(now(),mid)).rowcount==1

    def finish(self,mid,code,error,completion=None):
        with self.db:
            changed = self.db.execute("UPDATE tasks SET status=?,completed_at=?,exit_code=?,error=? WHERE gmail_message_id=? AND status='RUNNING'",
                ('COMPLETED' if code==0 and not error else 'FAILED',now(),code,error,mid)).rowcount
            if changed!=1:
                raise RuntimeError('Invalid state transition')
            if completion is not None:
                self.db.execute("INSERT OR IGNORE INTO replies(gmail_message_id,report_json,status) VALUES (?,?,'PENDING')",
                                (mid,json.dumps(completion)))


def repository_facts(repository):
    root = Path(repository)
    git = root/'.git'
    facts = dict(repository=str(root),repository_detected=root.is_dir(),branch=None,head=None,
        test_status='not_run',github_access_status='not_verified_no_push_attempted')
    # No Git process, hooks, credentials or Git config are evaluated.
    if git.is_dir():
        head = (git/'HEAD').read_text(encoding='ascii').strip()
        if head.startswith('ref: refs/heads/'):
            ref = head[5:]
            path = (git/ref).resolve()
            if path.is_relative_to(git.resolve()) and len(ref)<256:
                facts['branch'] = ref[len('refs/heads/'):]
                if path.is_file():
                    head = path.read_text(encoding='ascii').strip()
                elif (git/'packed-refs').is_file():
                    head = next((line.split()[0] for line in (git/'packed-refs').read_text().splitlines()
                                 if line.endswith(' '+ref)), '')
        if re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}',head):
            facts['head']=head
    if (root/'tests').is_dir():
        facts['test_status']='tests_directory_present; execution_not_attempted'
    return facts


def redact(text):
    text = re.sub(r'(?i)(bearer\s+|sk-(?:proj-)?|ya29\.)[A-Za-z0-9_.-]+', '[REDACTED]',text)
    text = re.sub(r'(?im)^.*(?:api[_-]?key|access_token|refresh_token|client_secret|password)\s*[=:].*$', '[REDACTED]',text)
    return text[:262144]


def codex_command(cfg):
    executable = Path(cfg['codex_executable'])
    if not executable.is_file() or executable.name.lower()!='codex.exe':
        raise ValueError('Configured native codex.exe not found')
    engineering=cfg.get('engineering_enabled',False)
    if engineering and cfg.get('engineering_repository'):
        cfg=dict(cfg,repository=cfg['engineering_repository'])
    args = [str(executable),'-a','never','exec','--sandbox','workspace-write' if engineering else 'read-only','--ignore-user-config',
            '--ignore-rules','--ephemeral','--color','never','--output-schema',str(ROOT/'result.schema.json')]
    # Verified using installed codex features list, 0.154.0-alpha.6.2.
    for feature in ('apps','plugins','hooks','multi_agent','multi_agent_v2',
                    'computer_use','browser_use','browser_use_external',
                    'image_generation','view_image','skill_search'):
        args += ['--disable',feature]
    for feature in ('shell_tool','unified_exec','code_mode_host'):
        args += ['--enable' if engineering else '--disable',feature]
    if engineering:
        args += ['-c','windows.sandbox='+json.dumps(cfg.get('windows_sandbox','unelevated'))]
        git=Path(cfg['repository'])/'.git'
        if not git.is_dir():
            raise ValueError('Engineering requires a repository with a local .git directory')
        args += ['--add-dir',str(git.resolve()),'-c','sandbox_workspace_write.network_access=true']
    args += ['--enable','skip_host_skill_discovery','-c','web_search="disabled"','-c','project_doc_max_bytes=0','-']
    return args


def run_codex(cfg,task):
    facts = repository_facts(cfg['repository'])
    request = task['request_text'] if 'request_text' in task.keys() else ''
    import issue_action
    from github_delivery import normalize
    request=issue_action.review_request(cfg,normalize(request))
    if issue_action.requested(request):
        issue_facts=repository_facts(cfg.get('engineering_repository') or cfg['repository'])
        return issue_action.run(cfg,task,issue_facts)
    from snapshot_action import requested, run
    if requested(request):
        return run(cfg,task,facts)
    engineering=cfg.get('engineering_enabled',False)
    if engineering and cfg.get('engineering_repository'):
        cfg=dict(cfg,repository=cfg['engineering_repository'])
        facts=repository_facts(cfg['repository'])
    if engineering and re.search(r'^POST_COMMENT:[ \t]*YES[ \t]*$',request,re.M):
        repo,number,_,_=issue_action.issue_fields(cfg,request)
        with issue_action.github_session() as session:
            facts['github_issue']=issue_action.lookup(session,repo,number)
    prompt = (ROOT/('engineering.txt' if engineering else 'bootstrap.txt')).read_text()+'\n'+json.dumps({'MODE':'ENGINEERING' if engineering else 'TEST','TASK_ID':task['task_id'],'facts':facts,'email_request':request})
    allowed = {'SYSTEMROOT','SYSTEMDRIVE','PROGRAMDATA','PROGRAMFILES','PROGRAMFILES(X86)','WINDIR','PATH','TEMP','TMP','USERPROFILE','LOCALAPPDATA','APPDATA','PATHEXT','COMSPEC'}
    env = {k:v for k,v in os.environ.items() if k.upper() in allowed}
    env['GIT_TERMINAL_PROMPT']='0'
    try:
        with Job() as job:
            with subprocess.Popen(codex_command(cfg),stdin=subprocess.PIPE,text=True,encoding='utf-8',errors='replace',
                stdout=subprocess.PIPE,stderr=subprocess.PIPE,cwd=cfg['repository'],env=env,
                shell=False,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0)) as process:
                job.assign(process)
                try:
                    stdout,stderr=process.communicate(prompt,timeout=cfg.get('engineering_timeout_seconds',1200) if engineering else cfg['timeout_seconds'])
                except subprocess.TimeoutExpired:
                    job.__exit__()
                    process.kill()
                    process.communicate()
                    raise
                return dict(exit_code=process.returncode,stdout=redact(stdout),stderr=redact(stderr),facts=facts,engineering=engineering,execution_repository=cfg['repository'])
    except subprocess.TimeoutExpired as exc:
        def output(value):
            return redact(value.decode('utf-8',errors='replace') if isinstance(value,bytes) else value or '')
        return dict(exit_code=-1,stdout=output(exc.stdout),stderr=output(exc.stderr)+'\nCodex timed out; no automatic retry',facts=facts)


class Gmail:
    def __init__(self,cfg,authorize=False):
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import AuthorizedSession, Request
        self.cfg=cfg
        path=Path(cfg['token_path'])
        if authorize:
            # This library logs callback URLs containing authorization codes at INFO.
            logging.getLogger('google_auth_oauthlib.flow').disabled=True
            from google_auth_oauthlib.flow import InstalledAppFlow
            flow=InstalledAppFlow.from_client_secrets_file(cfg['credentials_path'],SCOPES)
            creds=flow.run_local_server(host='localhost',port=0,access_type='offline',prompt='consent',
                login_hint=cfg['mailbox'],authorization_prompt_message='Complete Gmail authorization in your browser.')
        else:
            if not path.exists():
                raise ValueError('OAuth_setup_required')
            creds=Credentials.from_authorized_user_file(path)
            if not creds.valid:
                creds.refresh(Request())
        granted=creds.granted_scopes if creds.granted_scopes is not None else creds.scopes
        self.can_send=SEND_SCOPE in (granted or [])
        self.session=AuthorizedSession(creds)
        try:
            profile=self.get('profile')
            if profile['emailAddress'].lower()!=cfg['mailbox'].lower():
                raise ValueError('OAuth_mailbox_mismatch')
            atomic_json(path,json.loads(creds.to_json()))
        except BaseException:
            self.session.close()
            raise

    def get(self,resource,params=None):
        r=self.session.get('https://gmail.googleapis.com/gmail/v1/users/me/'+resource,params=params,timeout=30)
        if not r.ok:
            reasons=[]
            try:
                error=r.json().get('error',{})
                reasons=[e.get('reason','') for e in error.get('errors',[])]
                reasons += [e.get('reason','') for e in error.get('details',[])]
            except (ValueError,TypeError,AttributeError):
                pass
            safe=[v for v in reasons if isinstance(v,str) and re.fullmatch('[A-Za-z_]{1,80}',v)]
            LOG.error('Gmail API status=%s reasons=%s',r.status_code,','.join(safe) or 'unspecified')
        r.raise_for_status()
        return r.json()

    def messages(self):
        token=None
        count=0
        while count < self.cfg['max_messages']:
            params=dict(q='in:inbox subject:"[CRADAR TASK]" newer_than:'+str(self.cfg['lookback_days'])+'d',
                        maxResults=min(100,self.cfg['max_messages']-count))
            if token:
                params['pageToken']=token
            page=self.get('messages',params)
            for entry in page.get('messages',[]):
                mid=entry['id']
                if not re.fullmatch(r'[A-Za-z0-9_-]{1,200}',mid):
                    raise ValueError('Invalid_Gmail_message_ID')
                data=self.get('messages/'+mid,{'format':'raw'})
                encoded=data['raw']
                raw=base64.urlsafe_b64decode(encoded+'===') if len(encoded)<=90000 else b'x'*65537
                received=datetime.fromtimestamp(int(data['internalDate'])/1000,timezone.utc).isoformat()
                yield mid,raw,received,'SENT' in data.get('labelIds',[])
                count+=1
            token=page.get('nextPageToken')
            if not token:
                break
        if token:
            LOG.warning('Mailbox scan capped; increase max_messages or archive processed task messages')


def poll(cfg,messages,runner=run_codex,dry_run=False,gmail=None):
    LOG.info('poll started dry_run=%s',dry_run)
    state=None
    preview=None
    try:
        with process_lock(str(cfg['state_path'])+'.lock'):
            if not dry_run:
                state=State(cfg['state_path'])
                state.recover()
            elif Path(cfg['state_path']).exists():
                preview=sqlite3.connect(Path(cfg['state_path']).as_uri()+'?mode=ro',uri=True)
            count=0
            seen_messages=set()
            seen_tasks=set()
            for entry in messages:
                mid,raw,received=entry[:3]
                sent_by_mailbox=entry[3] if len(entry)>3 else False
                count+=1
                tid,error=None,None
                request_text=''
                try:
                    tid,request_text=parse_message(raw,cfg,include_body=True,sent_by_mailbox=sent_by_mailbox)
                except Exception as exc:
                    error=str(exc) if isinstance(exc,ValueError) and re.fullmatch('[a-z_]+',str(exc)) else 'malformed_message'
                result=state.record(mid,tid,received,error,request_text) if state else ('would_reject' if error else 'would_accept')
                if dry_run:
                    if mid in seen_messages or (preview and preview.execute('SELECT 1 FROM tasks WHERE gmail_message_id=?',(mid,)).fetchone()):
                        result='duplicate_message'
                    elif tid and (tid in seen_tasks or (preview and preview.execute('SELECT 1 FROM tasks WHERE task_id=?',(tid,)).fetchone())):
                        result='duplicate_task_id'
                    seen_messages.add(mid)
                    if tid:
                        seen_tasks.add(tid)
                LOG.info('email decision=%s task=%s reason=%s',result,tid,error)
            LOG.info('candidate emails=%s',count)
            if state:
                for task in state.pending(cfg['max_tasks_per_poll']):
                    if not state.claim(task['gmail_message_id']):
                        continue
                    execute_task(cfg,state,task,runner)
                if gmail is not None:
                    from replies import deliver_pending
                    deliver_pending(state,cfg,gmail,now)
    finally:
        if state:
            state.db.close()
        if preview:
            preview.close()
        LOG.info('poll finished')


def execute_task(cfg,state,task,runner):
    started=now()
    LOG.info('Codex launch requested task=%s',task['task_id'])
    result=dict(exit_code=-1,stdout='',stderr='',facts={})
    error=None
    code=None
    report={}
    valid_report=False
    try:
        result=runner(cfg,task)
        code=result['exit_code']
        if code!=0:
            error='codex_nonzero_exit'
        report=json.loads(result['stdout']) if code==0 else {}
        schema=json.loads((ROOT/'result.schema.json').read_text())
        if code==0 and (not isinstance(report,dict) or set(report)!=set(schema['required'])
            or type(report['repository_detected']) is not bool
            or report['outcome'] not in ('COMPLETED','BLOCKED','FAILED')
            or any(not isinstance(report[k],str) for k in ('test_status','github_access_status','summary'))
            or any(report[k] is not None and not isinstance(report[k],str) for k in ('branch','head'))):
            raise ValueError('Invalid report')
        if code==0:
            valid_report=True
            if result.get('engineering') and report['outcome']=='COMPLETED':
                from github_delivery import deliver
                deliver(cfg,task,report)
            if report['outcome']!='COMPLETED':
                error='task_'+report['outcome'].lower()
    except Exception as exc:
        error=type(exc).__name__
    stem=hashlib.sha256(task['gmail_message_id'].encode()).hexdigest()
    logs=Path(cfg['log_path'])
    logs.mkdir(parents=True,exist_ok=True)
    for channel in ('stdout','stderr'):
        (logs/(stem+'.'+channel+'.log')).write_text(redact(result.get(channel,'')),encoding='utf-8')
    facts=report if valid_report and result.get('engineering') else result.get('facts',{})
    completion=dict(task_id=task['task_id'],gmail_message_id=task['gmail_message_id'],
        outcome=report['outcome'] if valid_report else 'FAILED',
        supervisor=dict(process_id=os.getpid(),child_exit_code=code,child_result_received=code is not None,
                        source='worker process; scheduler provenance not independently verified'),
        status='COMPLETED' if code==0 and not error else 'FAILED',started_at=started,completed_at=now(),
        codex_exit_code=code,repository=result.get('execution_repository',cfg['repository']),**{k:facts.get(k) for k in ('repository_detected','branch','head','test_status','github_access_status')},
        summary=redact(report['summary']) if valid_report else result.get('failure_summary','Task failed; review sanitized logs'),error=error)
    atomic_json(Path(cfg['report_path'])/(stem+'.json'),completion)
    state.finish(task['gmail_message_id'],code,error,completion if cfg.get('reply_enabled',False) else None)
    LOG.info('Codex result task=%s status=%s',task['task_id'],completion['status'])


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--config',type=Path,default=ROOT/'config.json')
    parser.add_argument('--authorize',action='store_true')
    parser.add_argument('--dry-run',action='store_true')
    parser.add_argument('--mock',action='store_true',help='Offline sample email and fake Codex; separate mock state')
    args=parser.parse_args()
    try:
        cfg=config(args.config)
        if args.mock:
            cfg.update(state_path=str(ROOT/'state/mock.db'),log_path=str(ROOT/'logs/mock'),report_path=str(ROOT/'reports/mock'))
        Path(cfg['log_path']).mkdir(parents=True,exist_ok=True)
        logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(message)s',handlers=[
            RotatingFileHandler(Path(cfg['log_path'])/'worker.log',maxBytes=1000000,backupCount=3,encoding='utf-8')])
        if args.mock:
            cfg['allowed_senders']=['trusted@example.invalid']
            raw=(ROOT/'sample_handshake.eml').read_bytes()
            def fake(cfg,task):
                facts=repository_facts(cfg['repository'])
                output={k:facts[k] for k in ('repository_detected','branch','head','test_status','github_access_status')}
                return dict(exit_code=0,stdout=json.dumps(output|{'summary':'Mock only','outcome':'COMPLETED'}),stderr='',facts=facts)
            poll(cfg,[('offline-handshake-001',raw,now())],fake,args.dry_run)
        else:
            with process_lock(str(cfg['token_path'])+'.lock'):
                gmail=Gmail(cfg,args.authorize)
                try:
                    if args.authorize:
                        LOG.info('OAuth mailbox verified; no polling or Codex execution')
                    else:
                        poll(cfg,gmail.messages(),dry_run=args.dry_run,gmail=gmail)
                finally:
                    gmail.session.close()
    except Exception as exc:
        LOG.error('Worker stopped: %s',type(exc).__name__)
        return 1
    return 0


if __name__=='__main__':
    raise SystemExit(main())
