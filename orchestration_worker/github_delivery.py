"""GitHub delivery for explicit engineering instructions in validated emails."""
import hashlib
import json
from pathlib import Path
import re


from workflow import normalize, plan


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
    delivery_plan=plan(request)
    report['implementation_outcome']=report['outcome']
    completed=report['outcome']=='COMPLETED'
    states={name: {'status': 'PENDING' if delivery_plan[name] else 'NOT_REQUESTED'}
            for name in ('publish','create_pr','post_comment')}
    report['delivery']=states
    if not completed:
        report['summary']='Implementation outcome: '+report['outcome']+'\n\n'+report['summary']
        for name in ('publish','create_pr'):
            if delivery_plan[name]: states[name]={'status':'SKIPPED_INCOMPLETE'}
        report['github_access_status']+='\nPush and PR skipped: local work is incomplete.'

    def attempt(name, action):
        try:
            url=action()
            states[name]={'status':'DELIVERED','url':url}
            return url
        except Exception as exc:
            # Persistent per-action journals retain uncertain attempts; never retry
            # mutations blindly. A failed push/PR must not suppress a status comment.
            states[name]={'status':'FAILED_OR_UNCERTAIN','error':type(exc).__name__}
            report['outcome']='BLOCKED'
            report['summary']+='\nSupervisor '+name+' failed or is uncertain: '+type(exc).__name__
            report['github_access_status']+='\nSupervisor '+name+' failed or is uncertain: '+type(exc).__name__
            return None

    if completed and delivery_plan['publish']:
        url=attempt('publish',lambda:publish(cfg,report['branch'],report['head']))
        if url:
            report['github_access_status']='Published and remote SHA verified: '+url
            report['summary']+='\nSupervisor publication verified: '+url
    if completed and delivery_plan['create_pr']:
        if states['publish']['status']!='DELIVERED':
            states['create_pr']={'status':'SKIPPED_DEPENDENCY'}
        else:
            url=attempt('create_pr',lambda:pull_request(cfg,report['branch'],report['head'],report['summary']))
            if url:
                report['github_access_status']+='\nPR: '+url
                report['summary']+='\nReview PR: '+url
    if delivery_plan['post_comment']:
        _,number,_,key=issue_fields(cfg,request)
        url=attempt('post_comment',lambda:comment(cfg,dict(gmail_message_id=task['gmail_message_id'],request_text=
            f'ISSUE_NUMBER: {number}\nCOMMENT_KEY: {key}\nBODY:\n'+report['summary']))['url'])
        if url:
            report['github_access_status']+='\nComment posted: '+url
            report['summary']+='\nSupervisor comment verified: '+url
