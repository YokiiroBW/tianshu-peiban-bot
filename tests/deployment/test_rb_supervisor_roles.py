"""Observation child failure remains local to that child."""

import asyncio
from pathlib import Path
from types import SimpleNamespace

from tianshu.runtime.supervisor import Supervisor


async def test_observation_exit_does_not_stop_product_child(tmp_path):
    settings = SimpleNamespace(
        artifact_directory=Path(tmp_path) / "files",
        get=lambda key, default=None: {"uid": 1, "gid": 1} if key == "supervisor" else default,
        secrets=None,
    )
    supervisor = Supervisor(settings, tmp_path / "settings.json")
    started = asyncio.Event()
    release = asyncio.Event()

    class Child:
        def __init__(self, name):
            self.name = name
            self.pid = 101 if name == "interactive" else 102

        async def wait(self):
            if self.name == "vector":
                return 1
            started.set()
            await release.wait()
            return 0

    async def spawn(name, _command, **_options):
        return Child(name)

    supervisor.spawn = spawn
    observation = asyncio.create_task(supervisor.supervise("vector", ["fixture"], restart_limit=0))
    api = asyncio.create_task(supervisor.supervise("interactive", ["fixture"], core=True, restart_limit=0))
    await started.wait()
    await observation
    assert supervisor.role_status["vector"]["state"] == "failed"
    assert supervisor.role_status["interactive"]["state"] == "running"
    assert not supervisor.stop.is_set() and not api.done()
    supervisor.stop.set()
    release.set()
    await api
