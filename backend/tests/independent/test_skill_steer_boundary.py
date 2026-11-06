"""Independent route seam regression. Run from repository root with backend/.venv/Scripts/python.exe FILE."""
import sys
sys.dont_write_bytecode = True
import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from fastapi import FastAPI, HTTPException
sys.path.insert(0,str(Path(__file__).resolve().parents[3]))
from backend.application.maritime.service import MaritimeOffice
from backend.interfaces.api.maritime import attach_maritime

ROOT = Path(__file__).resolve().parents[3]

def review_temp():
    scratch=Path(__file__).resolve().parents[3] / ".ai-tmp/maritime-platform-20261105/independent"
    scratch.mkdir(parents=True,exist_ok=True)
    return tempfile.TemporaryDirectory(dir=scratch)


class Captured(Exception):
    pass

class SkillSteerBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = review_temp()
        self.addCleanup(self.tmp.cleanup)
        self.office = MaritimeOffice(Path(self.tmp.name) / 'office.sqlite3')
        self.app = FastAPI()
        self.app.state.maritime_office = self.office
        attach_maritime(self.app, SimpleNamespace(application={}))
        self.request = SimpleNamespace(app=self.app)
        self.capture = {}
        capture = self.capture
        class Service:
            async def steer(self, **kwargs):
                capture.update(kwargs)
                raise Captured()
        tree = ast.parse((ROOT / 'backend/interfaces/api/app.py').read_text(encoding='utf-8'))
        fn = next(n for n in ast.walk(tree) if isinstance(n,ast.AsyncFunctionDef) and n.name=='workbench_steer')
        fn.decorator_list = []
        mod = ast.Module(body=[ast.ImportFrom(module='__future__',names=[ast.alias(name='annotations')],level=0),fn],type_ignores=[])
        ns = dict(Header=lambda **k:None, HTTPException=HTTPException,
                  require_conversation=lambda *a:SimpleNamespace(id='bob'),
                  workspace=lambda r:SimpleNamespace(list_conversation_material_ids=lambda **k:[]),
                  service=lambda r:Service(),
                  SchedulerCommandError=type('SchedulerCommandError',(Exception,),{}))
        exec(compile(ast.fix_missing_locations(mod),'actual-current-workbench-steer','exec'),ns)
        self.route = ns['workbench_steer']

    def steer(self, facts):
        body=SimpleNamespace(conversation_id='bob-owned', material_ids=[], attached_material_ids=[],
                             text='change approach', facts=facts, constraints=[])
        return asyncio.run(self.route('run',body,self.request,'Bearer test'))

    def test_private_skill_rejected_before_dispatch(self):
        skill=self.office.save_skill('alice',dict(name='Private',summary='s',instructions='private',visibility='private'))
        with self.assertRaises(HTTPException) as caught:
            self.steer({'skill_id':skill['id']})
        self.assertEqual(caught.exception.status_code,404)
        self.assertEqual(self.capture,{})

    def test_community_skill_context_is_server_resolved(self):
        skill=self.office.save_skill('alice',dict(name='Public',summary='s',instructions='real server text',visibility='community'))
        with self.assertRaises(Captured):
            self.steer({'skill_id':skill['id'],'skill_context':{'instructions':'forged'}})
        self.assertEqual(self.capture['payload'].facts['skill_context']['instructions'],'real server text')

    def test_forged_context_without_selection_removed(self):
        with self.assertRaises(Captured):
            self.steer({'skill_context':{'instructions':'forged'}})
        self.assertNotIn('skill_context',self.capture['payload'].facts)

if __name__=='__main__':
    unittest.main()

