import concurrent.futures as cf
import json
from pathlib import Path
import urllib.request

OUT = Path(__file__).resolve().parent
REPOS = ['obra/superpowers','garrytan/gstack','trailofbits/skills','anthropics/skills',
         'affaan-m/everything-claude-code','pskoett/self-improving-agent','mattpocock/skills']

def get(url):
    req = urllib.request.Request(url,headers={'User-Agent':'Codex-skill-research','Accept':'application/vnd.github+json'})
    with urllib.request.urlopen(req,timeout=45) as r:
        return json.load(r)

def discover(repo):
    try:
        meta=get('https://api.github.com/repos/'+repo)
        commit=get('https://api.github.com/repos/'+meta['full_name']+'/commits/'+meta['default_branch'])['sha']
        tree=get('https://api.github.com/repos/'+meta['full_name']+'/git/trees/'+commit+'?recursive=1')
        record={k:meta.get(k) for k in ['full_name','html_url','default_branch','stargazers_count','license','archived','pushed_at']}
        record.update(commit=commit,checked_on='2026-11-04',requested_repo=repo,tree_truncated=tree.get('truncated'),paths=[x['path'] for x in tree['tree'] if x['type']=='blob'])
        return record
    except Exception as e:
        return {'requested_repo':repo,'error':str(e)}

with cf.ThreadPoolExecutor(max_workers=4) as pool:
    rows=list(pool.map(discover,REPOS))
(OUT/'discovery.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8')
for row in rows:
    if 'error' in row: print(row); continue
    print(json.dumps({k:v for k,v in row.items() if k!='paths'},ensure_ascii=False))
    for p in row['paths']:
        if (p.endswith('SKILL.md') or 'LICENSE' in p.upper()) and any(w in p.lower() for w in ['verif','review','test','qa/','learn','improv','eval','spec','feedback','license','diagnos','grill']):
            print(' ',p)
