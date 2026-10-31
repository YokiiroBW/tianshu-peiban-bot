"""Explicit capabilities domain assembly; all dependencies are injected public ports."""
from pathlib import Path
from tianshu.runtime.composition import DomainModule
from tianshu.runtime.migrations import Migration
from .service import Capabilities

def build_module(runtime,*,settings,authority,sources,life,expression=None,result_events=None,authorize_actor=None,actor_directory=None,worker=None):
    service=Capabilities(runtime,settings,authority=authority,sources=sources,life=life,expression=expression,result_events=result_events,authorize_actor=authorize_actor,actor_directory=actor_directory,worker=worker)
    from .api import register_routes
    def routes(app,_runtime):register_routes(app,service)
    def tasks(_jobs,_runtime):service.register_tasks()
    async def start(_runtime):await service.initialize()
    async def stop(_runtime):await service.close()
    module=DomainModule("capabilities",routes,start,stop,tasks,(Migration("capabilities-0001",Path(__file__).with_name("schema.sql").read_text(encoding="utf-8")),))
    return module,service
