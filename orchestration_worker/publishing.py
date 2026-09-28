"""Publish an exact worker-branch commit through the supervising user's Git login."""
import os
from pathlib import Path
import re
import subprocess


def publish(cfg,branch,sha):
    if not isinstance(branch,str) or not re.fullmatch(r'worker/[A-Za-z0-9][A-Za-z0-9_/-]{0,100}',branch) or '..' in branch:
        raise ValueError('Only worker branches can be published')
    if not isinstance(sha,str) or not re.fullmatch('[a-f0-9]{40}',sha):
        raise ValueError('Invalid commit SHA')
    remote=cfg.get('publish_remote','')
    if not re.fullmatch(r'https://github\.com/[A-Za-z0-9_-]+/[A-Za-z0-9_.-]+(?:\.git)?',remote):
        raise ValueError('Configure a pinned HTTPS GitHub publishing destination')
    root=Path(cfg['engineering_repository']).resolve()
    if not root.is_relative_to(Path(__file__).resolve().parent/'workspaces'):
        raise ValueError('Publishing requires an isolated checkout')
    # Read the local config as data first; reject includes, URL rewrites, custom
    # helpers and executable hooks before privileged Git touches this checkout.
    text=(root/'.git/config').read_text(encoding='utf-8')
    allowed={'core':{'repositoryformatversion':'0','filemode':'false','bare':'false','logallrefupdates':'true','symlinks':'false','ignorecase':'true'},
             'remote "origin"':{'url':remote,'fetch':'+refs/heads/*:refs/remotes/origin/*'}}
    section=''
    for line in text.splitlines():
        line=line.strip()
        if not line or line.startswith(('#',';')): continue
        if line.startswith('[') and line.endswith(']'):
            section=line[1:-1].lower()
            if section not in allowed and section!='user' and not section.startswith('branch "'):
                raise ValueError('Unexpected Git configuration section')
            continue
        key,separator,value=line.partition('='); key=key.strip().lower(); value=value.strip()
        if not separator: raise ValueError('Unexpected Git configuration')
        if section in allowed and allowed[section].get(key)!=value:
            raise ValueError('Unexpected Git configuration value')
        if section=='user' and key not in ('name','email'): raise ValueError('Unexpected identity setting')
        if section.startswith('branch "') and key not in ('remote','merge'): raise ValueError('Unexpected branch setting')
    env={k:v for k,v in os.environ.items() if not k.upper().startswith(('GIT_','SSH_'))}
    env.update(GIT_CONFIG_NOSYSTEM='1',GIT_CONFIG_GLOBAL=os.devnull,GIT_TERMINAL_PROMPT='0',GCM_INTERACTIVE='never')
    def git(*args):
        result=subprocess.run(['git','-C',str(root),'-c','core.hooksPath='+os.devnull,
            '-c','credential.helper=','-c','credential.helper=manager',*args],
            env=env,capture_output=True,text=True,timeout=120,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        if result.returncode: raise RuntimeError('Git publishing command failed (exit '+str(result.returncode)+'); credentials were not exposed')
        return result.stdout.strip()
    if git('rev-parse','refs/heads/'+branch)!=sha:
        raise ValueError('Branch moved before publication')
    git('push','--',remote,sha+':refs/heads/'+branch)
    observed=git('ls-remote','--exit-code',remote,'refs/heads/'+branch).split()
    if not observed or observed[0]!=sha: raise RuntimeError('Remote verification failed')
    return remote.removesuffix('.git')+'/commit/'+sha
