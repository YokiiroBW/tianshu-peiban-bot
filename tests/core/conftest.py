"""Explicit disposable PG/vector fixtures for the coherent identity/source/memory domains."""

from types import SimpleNamespace

import pytest_asyncio

from tianshu.runtime.config import Settings,Secrets
from tianshu.runtime.commands import CommandStore
from tianshu.runtime.files import FileStore
from tianshu.runtime.jobs import JobQueue
from tianshu.runtime.migrations import migrate
from tianshu.runtime.errors import AppError
from tianshu.identity.service import IdentityService
from tianshu.identity.sessions import SessionService
from tianshu.identity.management import IdentityManagement
from tianshu.identity.module import create_module as identity_module
from tianshu.sources.service import SourceService
from tianshu.sources.module import create_module as source_module
from tianshu.memory.service import MemoryService
from tianshu.memory.claims import ClaimService
from tianshu.memory.steps import ModelSteps
from tianshu.memory.embedding import Embeddings
from tianshu.memory.retrieval import MemoryRetrieval
from tianshu.memory.workers import MemoryWorkers
from tianshu.memory.module import create_module as memory_module
from tianshu.conversation.context.assembly import ContextAssembler


class UnconfiguredModels:
    async def embedding_profile(self):
        raise AppError("embedding_profile_not_configured",status=503)
    async def generate(self,request):
        raise AppError("model_function_not_configured",status=503)


@pytest_asyncio.fixture
async def core(tmp_path,database):
    jobs=JobQueue(database.config.dsn); commands=CommandStore(database); files=FileStore(tmp_path/"files")
    settings=Settings({"identity":{"setup_token_ref":None,"installation_seal":str(tmp_path/"installed")}},Secrets({}),tmp_path)
    identity=IdentityService(database,commands,sessions=SessionService(database,settings))
    sources=SourceService(database,identity,files,commands,jobs); identity.bind_sources(sources)
    models=UnconfiguredModels(); memory=MemoryService(database,identity,sources,commands,jobs,models)
    claims=ClaimService(memory); memory.bind_claims(claims); sources.register_dependant(memory.source_changed)
    steps=ModelSteps(database,models); embeddings=Embeddings(database,models,steps)
    retrieval=MemoryRetrieval(memory,embeddings,{"vector":False})
    assembler=ContextAssembler(identity,memory,retrieval)
    workers=MemoryWorkers(memory,assembler,embeddings,steps)
    modules=(identity_module(identity),source_module(sources,identity),memory_module(memory,retrieval,workers,identity))
    try:
        await migrate(database,tuple(migration for module in modules for migration in module.migrations))
        for module in modules:
            if module.register_tasks:
                module.register_tasks(jobs,None)
        result,_=await identity.sessions.setup({"username":"owner","password":"isolated-password","client_request_id":"setup"})
        owner=await identity.context(result.data.principal.principal_id)
        async with database.transaction() as conn:
            actor=await identity.register_actor(owner,connection=conn)
        yield SimpleNamespace(database=database,identity=identity,sources=sources,memory=memory,claims=claims,
            management=IdentityManagement(identity),owner=owner,actor=actor,models=models,retrieval=retrieval,files=files,assembler=assembler)
    finally:
        # The one shared disposable fixture owns database teardown.
        pass


@pytest_asyncio.fixture
async def ordinary_users(core):
    users=[]
    permissions=["chat","conversation.read","conversation.send","companion.read","life.read","life.published.read","memory.read","memory.manage","memory.share"]
    for username in ("alice","bobby"):
        granted=permissions+(["life.manage"] if username=="alice" else [])
        result=await core.management.execute(core.owner,"principals.save",{"username":username,"display_name":username,
            "password":"isolated-password","permissions":granted,"expected_revision":0,"client_request_id":"create:"+username})
        principal=result.data["principal_id"]
        async with core.database.transaction(read_only=True) as conn:
            row=await (await conn.execute("SELECT access_epoch FROM identity.actors WHERE actor_id=%s",(core.actor,))).fetchone()
        await core.management.execute(core.owner,"actors.grant",{"actor_id":core.actor,"principal_id":principal,
            "permissions":granted,"expected_revision":row["access_epoch"],"client_request_id":"grant:"+username})
        context=await core.identity.context(principal,actor_id=core.actor)
        conversation=await core.identity.open_web_conversation(context,core.actor,client_request_id="open:"+username)
        context=await core.identity.context(principal,actor_id=core.actor,conversation_id=conversation.data["conversation_id"])
        users.append(context)
    return tuple(users)
