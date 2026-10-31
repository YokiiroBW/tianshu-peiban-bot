"""Fresh media composition and the shared native sealing boundary; no providers."""

import asyncio
import base64
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import Mock

from tianshu.capabilities.contracts import Fault
from tianshu.capabilities.media.accounts import Accounts
from tianshu.capabilities.media.runtime import Media
from tianshu.runtime.credentials import CredentialStore
from tianshu.runtime.errors import AppError


def credentials(database, key=b"\x17" * 32):
    secrets = SimpleNamespace(
        resolve=Mock(return_value=base64.urlsafe_b64encode(key).decode("ascii"))
    )
    return CredentialStore(database, secrets)


class FreshMediaCredentials(IsolatedAsyncioTestCase):
    async def test_parallel_unconfigured_owners_need_no_local_key_or_account_scan(self):
        database = SimpleNamespace(
            sync_transaction=Mock(side_effect=AssertionError("unexpected startup SQL"))
        )
        runtimes = [
            SimpleNamespace(database=database, files=object(), credentials=credentials(database))
            for _ in range(2)
        ]

        async def no_work(*args, **kwargs):
            raise AssertionError("unconfigured composition must not execute provider work")

        with TemporaryDirectory() as directory:
            anchor = str(Path(directory) / "library")

            def compose(runtime):
                return Media(
                    {"database_path": anchor}, runtime=runtime, clock=lambda: 1, local=no_work
                )

            owners = await asyncio.gather(
                *(asyncio.to_thread(compose, runtime) for runtime in runtimes)
            )
            try:
                for owner, runtime in zip(owners, runtimes):
                    self.assertIsNone(owner.config)
                    self.assertIs(owner.accounts.credentials, runtime.credentials)
                    self.assertIsNotNone(owner.books)
                    self.assertIsNotNone(owner.comics)
                    runtime.credentials.secrets.resolve.assert_not_called()
                database.sync_transaction.assert_not_called()
                self.assertEqual(list(Path(directory).iterdir()), [])
            finally:
                await asyncio.gather(*(owner.close() for owner in owners))


class MediaCredentialBoundary(TestCase):
    def test_instances_share_master_but_seals_bind_media_domain_and_identity(self):
        writer = Accounts(object(), credentials(object()))
        reader = Accounts(object(), credentials(object()))
        sealed = writer.seal("bilibili-42", "synthetic-cookie")
        self.assertNotIn("synthetic-cookie", sealed)
        self.assertEqual(reader.unseal("bilibili-42", sealed), "synthetic-cookie")
        with self.assertRaises(Fault) as wrong_identity:
            reader.unseal("bilibili-43", sealed)
        self.assertEqual(wrong_identity.exception.code, "media_credentials_unavailable")
        with self.assertRaises(AppError):
            reader.credentials.unseal_bytes(
                base64.b64decode(sealed),
                domain="capabilities.configuration",
                reference="bilibili-42",
            )
        wrong_master = Accounts(object(), credentials(object(), b"\x18" * 32))
        with self.assertRaises(Fault):
            wrong_master.unseal("bilibili-42", sealed)
