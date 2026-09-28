"""GitHub delivery for explicit engineering instructions in validated emails."""
import hashlib
import json
from pathlib import Path
import re


def normalize(text):
    text=text.replace('\r\n','\n')
    if re.findall(r'^TASK_TYPE[ \t]*=[ \t]*(\w+)[ \t]*$',text,re.M)!=['ENGINEERING']:
        return text
    def section(name):
        match=re.search(r'^'+name+r'[ \t]*\n(.*?)(?=\n[A-Z][A-Z /_]{2,}\n|\Z)',text,re.M|re.S)
        return match[1] if match else ''
    git=section('GIT'); roadmap=section('ROADMAP')
    instructions='\n'.join(line for line in git.splitlines() if re.match(r'^(Work|Commit|Push|Create|Open)\b',line,re.I))
    extra=[]
    if re.search(r'\bpush\b',instructions,re.I) and not re.search(r'\b(?:not|never|without|no)\b[^.\n]*\bpush\b',git,re.I):
        if not re.search(r'^PUBLISH:',text,re.M): extra.append('PUBLISH: YES')
    if re.search(r'\b(?:create|open)\s+(?:a\s+)?(?:PR|pull request)\b',instructions,re.I) and not re.search(r'\b(?:not|never|without|no)\b[^.\n]*\b(?:create|open)\b',git,re.I):
        if not re.search(r'^CREATE_PR:',text,re.M): extra.append('CREATE_PR: YES')
    numbers=set(re.findall(r'\bGitHub issue\s*#?([1-9][0-9]*)\b',roadmap,re.I))
    if re.search(r'^\s*(?:Add|Post)\b[^\n]*\bcomment\b',roadmap,re.I|re.M) and len(numbers)==1:
        number=numbers.pop()
        existing=re.findall(r'^ISSUE_NUMBER:[ \t]*(.*)$',text,re.M)
        if existing and existing!=[number]: raise ValueError('Conflicting issue targets')
        if not re.search(r'^POST_COMMENT:',text,re.M): extra.append('POST_COMMENT: YES')
        if not existing: extra.append('ISSUE_NUMBER: '+number)
    return '\n'.join(extra+[text])


def pull_request(cfg, branch, sha, summary, session_factory=None):
    from issue_action import github_session
    from gmail_poller import atomic_json, process_lock, now
    remote=cfg.get('publish_remote','').removesuffix('.git')
    match=re.fullmatch(r'https://github\.com/([A-Za-z0-9_-]+)/([A-Za-z0-9_.-]+)',remote)
    if not match or not re.fullmatch(r'worker/[A-Za-z0-9_/-]+',branch) or not re.fullmatch('[a-f0-9]{40}',sha):
        raise ValueError('Invalid PR destination')
    owner,repo=match.groups(); api=f'https://api.github.com/repos/{owner}/{repo}'
    digest=hashlib.sha256((remote+'\n'+branch+'\n'+sha).encode()).hexdigest()
    path=Path(cfg['state_path']).parent/'pull_requests'/(digest+'.json')
    with process_lock(str(path)+'.lock'):
        if path.exists():
            saved=json.loads(path.read_text(encoding='utf-8'))
            if saved['status']=='CREATED': return saved['url']
            raise RuntimeError('PR delivery uncertain; inspect GitHub before retrying')
        with (session_factory or github_session)() as session:
            result=session.get(api+'/pulls',params={'state':'open','head':owner+':'+branch},timeout=30,allow_redirects=False)
            result.raise_for_status()
            existing=result.json()
            if len(existing)>1: raise ValueError('Ambiguous open PR')
            if existing:
                pr=existing[0]
                if pr['head']['sha']!=sha: raise ValueError('PR head does not match published commit')
            else:
                repository=session.get(api,timeout=30,allow_redirects=False)
                repository.raise_for_status(); base=repository.json()['default_branch']
                atomic_json(path,dict(status='SENDING',started_at=now()))
                response=session.post(api+'/pulls',json={'title':'Crypto Radar: '+branch.removeprefix('worker/').replace('-',' '),
                    'head':branch,'base':base,'body':summary[:60000],'draft':False},timeout=30,allow_redirects=False)
                if response.status_code!=201: raise RuntimeError('PR creation not acknowledged')
                pr=response.json()
            number=pr.get('number')
            if type(number) is not int or number<1: raise ValueError('Invalid PR response')
            url=remote+'/pull/'+str(number)
            atomic_json(path,dict(status='CREATED',url=url,completed_at=now()))
            return url


def deliver(cfg, task, report):
    from issue_action import review_request, issue_fields, comment
    from publishing import publish
    request=review_request(cfg,normalize(task['request_text']))
    def enabled(name): return bool(re.search(r'^'+name+r':[ \t]*YES[ \t]*$',request,re.M))
    try:
        if enabled('PUBLISH'):
            url=publish(cfg,report['branch'],report['head'])
            report['github_access_status']='Published and remote SHA verified: '+url
            report['summary']+='\nSupervisor publication verified: '+url
        if enabled('CREATE_PR'):
            if not enabled('PUBLISH'): raise ValueError('PR creation requires publication authorization')
            url=pull_request(cfg,report['branch'],report['head'],report['summary'])
            report['github_access_status']+='\nPR: '+url
            report['summary']+='\nReview PR: '+url
        if enabled('POST_COMMENT'):
            _,number,_,key=issue_fields(cfg,request)
            posted=comment(cfg,dict(gmail_message_id=task['gmail_message_id'],request_text=
                f'ISSUE_NUMBER: {number}\nCOMMENT_KEY: {key}\nBODY:\n'+report['summary']))
            report['github_access_status']+='\nComment posted: '+posted['url']
            report['summary']+='\nSupervisor comment verified: '+posted['url']
    except Exception as exc:
        report['outcome']='BLOCKED'
        report['github_access_status']+='\nSupervisor delivery failed: '+type(exc).__name__
        report['summary']+='\nDelivery failed or is uncertain; completed steps above are preserved. Inspect persistent delivery state before retrying.'
