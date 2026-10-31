"""The actual supervisor drains its own process group and leaves unrelated work alone."""

import asyncio
import os
import signal
import sys

import pytest

from tianshu.runtime.config import Settings, Secrets
from tianshu.runtime.supervisor import Supervisor


@pytest.mark.skipif(
    sys.platform != "linux" or os.geteuid() != 0 or os.getpid() == 1,
    reason="isolated Linux root test process required; never exercise PID 1 cleanup in the runner",
)
async def test_term_cleans_owned_child_group_without_stopping_unrelated_process(tmp_path):
    settings = Settings(
        {
            "artifacts": str(tmp_path / "files"),
            "supervisor": {"uid": os.getuid(), "gid": os.getgid(), "term_timeout_seconds": 5},
        },
        Secrets({}),
        tmp_path,
    )
    supervisor = Supervisor(settings, tmp_path / "unused.json")
    supervisor.logs.mkdir()
    unrelated = await asyncio.create_subprocess_exec(
        sys.executable, "-c", "import time; time.sleep(60)", start_new_session=True
    )
    owned = None
    try:
        owned = await supervisor.spawn(
            "worker",
            [
                sys.executable,
                "-c",
                "import signal,subprocess,sys,time; child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); signal.signal(signal.SIGTERM,lambda *_:(child.terminate(),child.wait(),sys.exit(0))); print('ready',flush=True); time.sleep(60)",
            ],
            core=False,
            critical=False,
        )
        async with asyncio.timeout(5):
            while not any(
                b"ready" in path.read_bytes()
                for path in supervisor.logs.glob("stdout-worker-*.jsonl")
            ):
                await asyncio.sleep(0.02)
        await supervisor.shutdown()
        assert owned.returncode == 0 and unrelated.returncode is None
        assert list(supervisor.logs.glob("stdout-worker-*.jsonl"))
    finally:
        supervisor.terminate("worker", signal.SIGKILL)
        if owned is not None:
            await owned.wait()
        await asyncio.gather(*supervisor.readers, return_exceptions=True)
        if unrelated.returncode is None:
            unrelated.terminate()
        await unrelated.wait()
