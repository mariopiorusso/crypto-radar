"""Persist Gmail conversation -> Codex session identity and a readable task index."""
import json
import hashlib
from pathlib import Path
import re
import sqlite3
from contextlib import closing
from datetime import datetime, timezone


def identity(task):
    keys=task.keys()
    return (task['gmail_thread_id'] if 'gmail_thread_id' in keys else '',
            task['sender'] if 'sender' in keys else '')


def lookup_session(cfg,task):
    thread,sender=identity(task)
    if not thread or not sender:
        return None
    with closing(sqlite3.connect(cfg['state_path'])) as db:
        row=db.execute('SELECT session_id,repository,branch FROM email_sessions WHERE gmail_thread_id=? AND sender=?',
                       (thread,sender)).fetchone()
    if row and Path(row[1]).resolve()==Path(cfg['repository']).resolve():
        return dict(session_id=row[0],repository=row[1],branch=row[2])
    return None


def decode_events(stdout):
    final=None; session_id=None
    for line in stdout.splitlines():
        try:
            event=json.loads(line)
        except ValueError:
            continue
        if not isinstance(event,dict):
            continue
        if event.get('type')=='thread.started':
            value=event.get('thread_id','')
            if re.fullmatch(r'[0-9a-fA-F-]{36}',value):
                session_id=value
        item=event.get('item',{})
        if event.get('type')=='item.completed' and item.get('type')=='agent_message':
            final=item.get('text','')
    # Also accept old/fake runners' schema-valid final JSON, never tool output.
    return (final if final is not None else stdout),session_id


def save_session(cfg,task,session_id,final):
    thread,sender=identity(task)
    if not thread or not sender:
        return
    try:
        branch=json.loads(final).get('branch')
    except (ValueError,AttributeError):
        branch=None
    with closing(sqlite3.connect(cfg['state_path'])) as db, db:
        db.execute('INSERT OR REPLACE INTO email_sessions VALUES (?,?,?,?,?,?)',
                   (thread,sender,session_id,cfg['repository'],branch,datetime.now(timezone.utc).isoformat()))


def write_history(cfg):
    """Derived local report; the database remains authoritative."""
    from gmail_poller import atomic_json
    with closing(sqlite3.connect(cfg['state_path'])) as db:
        db.row_factory=sqlite3.Row
        tasks=[dict(row) for row in db.execute('''SELECT task_id,gmail_message_id,gmail_thread_id,
            received_at,status,error,request_text,session_id FROM tasks
            WHERE task_id IS NOT NULL ORDER BY received_at DESC''')]
    target=Path(cfg['report_path'])
    target.mkdir(parents=True,exist_ok=True)
    lines=['# Email agent history','',
           'Local task archive. Reply in the same Gmail thread to continue its saved Codex session.','']
    for task in tasks:
        filename=hashlib.sha256(task['gmail_message_id'].encode()).hexdigest()+'.json'
        report_path=target/filename
        report=json.loads(report_path.read_text(encoding='utf-8')) if report_path.exists() else {}
        task['summary']=report.get('summary','')
        lines += ['## '+task['received_at']+' — '+task['status'],'',
                  'Task: '+str(task['task_id']),'',
                  'Gmail thread: '+(task['gmail_thread_id'] or 'legacy; not recorded'),'',
                  'Codex session: '+(task['session_id'] or 'not recorded'),'',
                  '    '+task['request_text'].replace('\n','\n    '),'']
        if task['session_id']:
            lines += ['Resume: `codex resume '+task['session_id']+'`','']
        if report:
            lines += ['[Full result](./'+filename+')','',
                      '    '+task['summary'].replace('\n','\n    '),'']
        if task['error']:
            lines += ['Error: '+task['error'],'']
    temporary=target/'history.md.tmp'
    temporary.write_text('\n'.join(lines),encoding='utf-8')
    temporary.replace(target/'history.md')
    atomic_json(target/'history.json',tasks)
