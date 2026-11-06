import concurrent.futures as cf
import hashlib
import json
from pathlib import Path
import urllib.request

ROOT=Path(__file__).resolve().parent
SELECTION={
 'obra/superpowers':['skills/verification-before-completion/','skills/requesting-code-review/','skills/receiving-code-review/','skills/test-driven-development/'],
 'garrytan/gstack':['qa/','review/','test-audit/','learn/','verify/'],
 'trailofbits/skills':['plugins/differential-review/skills/differential-review/','plugins/property-based-testing/skills/property-based-testing/','plugins/spec-to-code-compliance/skills/spec-to-code-compliance/'],
 'anthropics/skills':['skills/webapp-testing/'],
 'affaan-m/ECC':['skills/verification-loop/','skills/eval-harness/','skills/continuous-learning-v2/','skills/e2e-testing/','skills/browser-qa/'],
 'mattpocock/skills':['skills/engineering/code-review/','skills/engineering/diagnosing-bugs/','skills/engineering/to-spec/','skills/engineering/implement-spec/']
}

def fetch(job):
 repo,commit,path=job
 url=f'https://raw.githubusercontent.com/{repo}/{commit}/{path}'
 try:
  with urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'Codex-skill-research'}),timeout=45) as r: body=r.read()
  target=ROOT/'sources'/repo/path
  target.parent.mkdir(parents=True,exist_ok=True)
  target.write_bytes(body)
  return dict(repo=repo,commit=commit,path=path,url=url,sha256=hashlib.sha256(body).hexdigest(),bytes=len(body))
 except Exception as e: return dict(repo=repo,path=path,error=str(e))

discovery=json.loads((ROOT/'discovery.json').read_text(encoding='utf-8'))
jobs=[]
for row in discovery:
 repo=row.get('full_name')
 if repo not in SELECTION: continue
 for p in row['paths']:
  selected=any(p.startswith(prefix) for prefix in SELECTION[repo])
  license_file=p in ['LICENSE','LICENSE.md','LICENSE.txt','NOTICE']
  if (selected and p.endswith(('.md','.txt','.py','.json','.yaml','.yml'))) or license_file:
   jobs.append((repo,row['commit'],p))
with cf.ThreadPoolExecutor(max_workers=6) as pool: rows=list(pool.map(fetch,jobs))
(ROOT/'source-lock.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8')
print('Downloaded',sum('error' not in r for r in rows),'files; errors',sum('error' in r for r in rows))
for r in rows:
 if 'error' in r: print(r)
for repo in SELECTION:
 print(repo, sum(r['repo']==repo and r['path'].endswith('SKILL.md') and 'error' not in r for r in rows),'skills')
