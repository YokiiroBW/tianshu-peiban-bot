"""Global management reads retain each role's existing Identity checks."""
from contextlib import asynccontextmanager
from dataclasses import replace
import json
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from aiohttp import web

from tianshu.channels.api import register_routes
from tianshu.runtime.api import TRUSTED_CONTEXT
from tianshu.runtime.errors import AppError
from tianshu.types import OriginKind, TrustedContext


class ManagementView(unittest.IsolatedAsyncioTestCase):
    def setup_view(self, *, actor=None, allowed=("role-a", "role-b"), permissions=("external.manage",)):
        context = TrustedContext("manager", actor, None, None, (), (), 1,
                                 OriginKind.HUMAN, frozenset(permissions))
        connections = [
            {"id": "bot-a", "actor_id": "role-a", "adapter": "telegram", "revision": 1},
            {"id": "bot-b", "actor_id": "role-b", "adapter": "telegram", "revision": 1},
        ]

        async def execute(sql):
            rows = [{"body": row} for row in connections] if sql.startswith("SELECT body ") else []
            return SimpleNamespace(fetchall=AsyncMock(return_value=rows),
                                   fetchone=AsyncMock(return_value=None))

        @asynccontextmanager
        async def transaction(**kwargs):
            yield SimpleNamespace(execute=execute)

        async def resolve(principal, *, actor_id, permission):
            if principal != context.principal_id or actor_id not in allowed:
                raise AppError("actor_forbidden", status=403)
            return replace(context, actor_id=actor_id)

        async def check_connection(current, connection_id):
            row = next(row for row in connections if row["id"] == connection_id)
            if current.actor_id != row["actor_id"] or current.actor_id not in allowed:
                raise AppError("connection_forbidden", status=403)
            return row

        identity = SimpleNamespace(check_context=AsyncMock(), context=AsyncMock(side_effect=resolve),
                                   connection=AsyncMock(side_effect=check_connection))
        app = web.Application()
        register_routes(app, SimpleNamespace(identity=identity,
            runtime=SimpleNamespace(database=SimpleNamespace(transaction=transaction))))
        handler = next(route.handler for route in app.router.routes()
                       if route.method == "GET" and route.resource.canonical == "/api/v1/channels/view")
        return handler, {TRUSTED_CONTEXT: context}, identity

    async def test_global_settings_shows_both_authorized_role_connections(self):
        handler, request, _ = self.setup_view()
        response = await handler(request)
        data = json.loads(response.body)
        self.assertEqual({row["id"] for row in data["connections"]}, {"bot-a", "bot-b"})

    async def test_actor_scoped_settings_remains_scoped(self):
        handler, request, _ = self.setup_view(actor="role-a")
        response = await handler(request)
        self.assertEqual([row["id"] for row in json.loads(response.body)["connections"]], ["bot-a"])

    async def test_global_settings_omits_roles_without_current_authorization(self):
        handler, request, _ = self.setup_view(allowed=("role-a",))
        response = await handler(request)
        self.assertEqual([row["id"] for row in json.loads(response.body)["connections"]], ["bot-a"])

    async def test_management_permission_is_still_required(self):
        handler, request, _ = self.setup_view(permissions=())
        with self.assertRaises(AppError) as raised:
            await handler(request)
        self.assertEqual(raised.exception.status, 403)
