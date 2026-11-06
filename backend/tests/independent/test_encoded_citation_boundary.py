"""Independent encoded internal citation regression; no external services or models."""
import sys
sys.dont_write_bytecode=True
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[3]))
import asyncio
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from fastapi import FastAPI
from fastapi.testclient import TestClient
from backend.application.maritime.citations import invalid_citations
from backend.bootstrap import build_harness
from backend.config.settings import load_settings
from backend.contracts.modules.loop import DecisionProposal,RunInput
from backend.infrastructure.observability.memory import MemoryEventSink
from backend.infrastructure.search.maritime_corpus import MaritimeCorpus
from backend.infrastructure.tools.maritime import CorpusTool
from backend.interfaces.api.maritime import attach_maritime
from backend.tests.support import ScriptedModel

def review_temp():
    scratch=Path(__file__).resolve().parents[3] / ".ai-tmp/maritime-platform-20261105/independent"
    scratch.mkdir(parents=True,exist_ok=True)
    return tempfile.TemporaryDirectory(dir=scratch)


class EncodedCitationTests(unittest.TestCase):
 def test_encoded_source_route_cannot_bypass_completion_validation(self):
  with review_temp() as tmp:
   corpus=MaritimeCorpus(Path(tmp)/'absent.sqlite3')
   inner=FastAPI()
   inner.state.account_service=SimpleNamespace(authenticate=lambda token:SimpleNamespace(id='member'))
   attach_maritime(inner,SimpleNamespace(application={}))
   outer=FastAPI();outer.mount('/api',inner)
   good={'id':'real','kind':'laws','title':'fixture','text':'fixture source'}
   key=corpus.remember_source(good)
   with patch('backend.interfaces.api.maritime.MaritimeCorpus',lambda:corpus):
    with TestClient(outer) as client:
     self.assertEqual(client.get('/api/maritime/%73ources/laws/real?snapshot='+key,headers={'Authorization':'Bearer fixture'}).status_code,200)
     bad='/api/maritime/%73ources/laws/invented?snapshot='+'a'*64
     self.assertEqual(client.get(bad,headers={'Authorization':'Bearer fixture'}).status_code,404)
   settings=load_settings();tool=CorpusTool(corpus)
   model=ScriptedModel(*[DecisionProposal(kind='answer',answer=f'[原文]({bad})') for _ in range(3)],settings=settings)
   harness=build_harness(model=model,settings=settings,events=MemoryEventSink(),tools={tool.spec.name:(tool.spec,tool)})
   result=asyncio.run(harness.run(RunInput(text='检查引用')))
   self.assertNotEqual(result.status,'completed','Real HTTP source route returns 404 but encoded internal citation completed')

if __name__=='__main__':unittest.main()
