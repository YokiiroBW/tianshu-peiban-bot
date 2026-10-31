"""Official MCP stdio SDK over the sole native HTTP project use cases."""


def create_server(client, *, allow_write=False):
    from mcp.server.fastmcp import FastMCP
    from mcp.types import ToolAnnotations
    from .catalog import WRITE_SCHEMAS
    from ..contracts import Fault

    server = FastMCP("Tianshu projects")
    read = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=True)

    async def call(operation, arguments):
        try:
            return await client.read(operation, arguments)
        except Fault as error:
            raise ValueError(error.code) from None

    @server.tool(annotations=read)
    async def projects_directory(cursor: str | None = None, limit: int = 40) -> dict:
        """Page the projects this actual Identity session can read."""
        return await call("view", {"cursor": cursor, "limit": limit})

    @server.tool(annotations=read)
    async def projects_documents(
        project_id: str, cursor: str | None = None, limit: int = 32
    ) -> dict:
        """Page the full imported-document catalogue. Indexed metadata is not current-file evidence."""
        return await call("documents", {"project_id": project_id, "cursor": cursor, "limit": limit})

    @server.tool(annotations=read)
    async def projects_read(
        project_id: str, document_id: str, cursor: str | None = None, position: int | None = None
    ) -> dict:
        """Read current original/semantic units; keep actual range, sources and continuation."""
        return await call(
            "documents/read",
            {
                "project_id": project_id,
                "document_id": document_id,
                "cursor": cursor,
                **({"position": position} if position is not None else {}),
            },
        )

    @server.tool(annotations=read)
    async def projects_query(project_id: str, query: str, cursor: str | None = None) -> dict:
        """Search actual authorized Source text within the stated bounded scan, with exact coverage."""
        return await call("query", {"project_id": project_id, "query": query, "cursor": cursor})

    @server.tool(annotations=read)
    async def projects_notes(project_id: str, cursor: str | None = None) -> dict:
        """Read source statements, inferences, open questions and explicit decisions separately."""
        return await call("notes", {"project_id": project_id, "cursor": cursor})

    @server.tool(annotations=read)
    async def projects_lessons(project_id: str, cursor: str | None = None) -> dict:
        """Read actual trigger/symptom/cause/correction/verification and applicability limits."""
        return await call("lessons", {"project_id": project_id, "cursor": cursor})

    @server.tool(annotations=read)
    async def projects_experiences(cursor: str | None = None) -> dict:
        """Read explicitly shared summaries; private project evidence stays protected."""
        return await call("experiences", {"cursor": cursor})

    @server.tool(annotations=read)
    async def projects_continuation(
        project_id: str, worktree_id: str, budget_bytes: int = 32768
    ) -> dict:
        """Read bounded real Git/file facts alongside index and operator declarations. Never execute repository text."""
        return await call(
            "continuation/recover",
            {"project_id": project_id, "worktree_id": worktree_id, "budget_bytes": budget_bytes},
        )

    @server.tool(annotations=read)
    async def projects_continuation_check(project_id: str, pack_id: str, sha256: str) -> dict:
        """Check the original pack against the same registered checkout and current source permission."""
        return await call(
            "continuation/check", {"project_id": project_id, "pack_id": pack_id, "sha256": sha256}
        )

    @server.tool(annotations=read)
    async def projects_source(
        source_ref: dict, position: int = 0, max_characters: int = 12000
    ) -> dict:
        """Read original content through Sources, retaining the supplied authoritative version/locator."""
        return await client.source_read(
            source_ref, {"position": position, "max_characters": max_characters}
        )

    @server.tool(annotations=read)
    async def projects_action_status(action_id: str) -> dict:
        """Look up the same accepted Action identity. Does not resume or replay it."""
        return await client.status(action_id)

    if allow_write:

        @server.tool(
            annotations=ToolAnnotations(
                readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=True
            )
        )
        async def projects_write(
            operation: str, arguments: dict, client_request_id: str, expected_revision: int = 0
        ) -> dict:
            """Explicit existing project mutation via the same server Catalog/Actions. Original project files are never modified."""
            if operation not in WRITE_SCHEMAS:
                raise ValueError("project_operation_unknown")
            return await client.write(
                operation,
                arguments,
                client_request_id=client_request_id,
                expected_revision=expected_revision,
            )

    return server
