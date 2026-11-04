"""Session responses identify the real Runtime installation generation."""

from types import SimpleNamespace
from uuid import uuid4

import pytest

from tianshu.identity.module import create_module as identity_module
from tianshu.identity.service import IdentityService
from tianshu.identity.sessions import SessionService
from tianshu.runtime.commands import CommandStore
from tianshu.runtime.config import Secrets, Settings
from tianshu.runtime.errors import AppError
from tianshu.runtime.migrations import migrate


async def test_all_session_responses_report_the_real_generation(database, tmp_path):
    settings = Settings(
        {"identity": {"setup_token_ref": None, "installation_seal": str(tmp_path / "installed")}},
        Secrets({}),
        tmp_path,
    )
    sessions = SessionService(database, settings)
    identity = IdentityService(database, CommandStore(database), sessions=sessions)
    await migrate(database, identity_module(identity).migrations)
    async with database.transaction(read_only=True) as conn:
        row = await (await conn.execute(
            "SELECT id FROM runtime.installation_generation WHERE singleton"
        )).fetchone()
    expected = str(row["id"])

    anonymous, _ = await sessions.inspect(SimpleNamespace(cookies={}))
    assert not anonymous.authenticated and not anonymous.initialized
    assert anonymous.data_generation == expected

    result, setup_token = await sessions.setup({
        "username": "operator", "password": "isolated-password", "client_request_id": "setup",
    })
    assert result.data.authenticated and result.data.data_generation == expected
    signed_in, _ = await sessions.inspect(SimpleNamespace(cookies={sessions.cookie: setup_token}))
    assert signed_in.authenticated and signed_in.data_generation == expected

    result, login_token = await sessions.login({
        "username": "operator", "password": "isolated-password", "client_request_id": "login",
    })
    assert result.data.authenticated and result.data.data_generation == expected
    result = await sessions.logout(
        SimpleNamespace(cookies={sessions.cookie: login_token}), {"client_request_id": "logout"}
    )
    assert not result.data.authenticated and result.data.data_generation == expected
    new_generation = str(uuid4())
    async with database.transaction() as conn:
        await conn.execute(
            "UPDATE runtime.installation_generation SET id=%s WHERE singleton", (new_generation,)
        )
    refreshed, _ = await sessions.inspect(SimpleNamespace(cookies={}))
    assert refreshed.data_generation == new_generation


async def test_session_generation_does_not_invent_missing_runtime_singleton():
    class EmptyResult:
        async def fetchone(self):
            return None

    class EmptyConnection:
        async def execute(self, _sql):
            return EmptyResult()

    with pytest.raises(AppError, match="installation_generation_unavailable"):
        await SessionService.generation_on(EmptyConnection())
