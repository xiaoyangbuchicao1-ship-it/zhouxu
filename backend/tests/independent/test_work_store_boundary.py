"""Isolated bootstrap-tool/API store consistency regression, run from repository root."""
import sys
sys.dont_write_bytecode=True
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[3]))
import asyncio,inspect,tempfile,unittest
from types import SimpleNamespace
from unittest.mock import patch
from fastapi import FastAPI
from fastapi.testclient import TestClient
from backend.application.maritime import work_packages as wp
from backend.bootstrap import maritime_tools
from backend.contracts.modules.loop import ActionRequest
from backend.contracts.schemas.core import ModuleRequest
from backend.interfaces.api.maritime import attach_maritime
from backend.tests.unit.test_maritime_work_packages import claim

class WorkStoreBoundaryTests(unittest.TestCase):
 def test_configured_store_shared_by_tool_and_delivery_api(self):
  with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as tmp:
   root=Path(tmp);configured=root/'configured.sqlite3';safe_default=root/'default.sqlite3';OriginalStore=wp.WorkPackageStore
   settings=SimpleNamespace(application={'work_store_path':str(configured)})
   # Preserve explicit configured paths, redirect default writes away from real user runtime.
   def isolated_store(path=None):return OriginalStore(path if path is not None else safe_default)
   with patch.object(wp,'WorkPackageStore',isolated_store):
    tools=maritime_tools(settings) if inspect.signature(maritime_tools).parameters else maritime_tools()
    tool=tools['prepare_maritime_work'][1]
    result=asyncio.run(tool.execute_context(ModuleRequest(run_id='trusted',call_id='c',input=ActionRequest(tool_name=tool.spec.name,arguments=claim()))))
    self.assertEqual(result.result['status'],'saved')
    app=FastAPI();app.state.account_service=SimpleNamespace(authenticate=lambda token:SimpleNamespace(id='owner'))
    class Scheduler:
     async def get_run(self,**kwargs):return SimpleNamespace(account_id='owner',conversation_id='conv')
    app.state.control_service=SimpleNamespace(scheduler=Scheduler())
    attach_maritime(app,settings)
    with TestClient(app) as client:
     response=client.get('/maritime/runs/trusted/deliverables?conversation_id=conv',headers={'Authorization':'Bearer fixture'})
    self.assertEqual(response.status_code,200)
    self.assertEqual(len(response.json()['packages']),1,'tool reports saved but configured delivery API reads a different store')

if __name__=='__main__':unittest.main()
