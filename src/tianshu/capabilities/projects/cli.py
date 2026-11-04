"""Project CLI: same native HTTP/session, read-only by default, no local project database."""

import argparse
import asyncio
import json
from uuid import uuid4
from .client import ProjectClient
from .catalog import READ_SCHEMAS, WRITE_SCHEMAS
from ..contracts import Fault


def main():
    parser = argparse.ArgumentParser(description="天枢项目资料与续作客户端")
    parser.add_argument("--server", required=True)
    parser.add_argument(
        "--session-file",
        required=True,
        help="显式提供当前Identity会话的Cookie header与csrf JSON，不存项目令牌",
    )
    parser.add_argument("--actor", help="角色项目的角色；独立项目省略")
    parser.add_argument("--person")
    parser.add_argument("--conversation")
    parser.add_argument(
        "--allow-write",
        action="store_true",
        help="显式启用现有服务器权限允许的资料写操作；不写外部项目",
    )
    parser.add_argument("--mcp", action="store_true", help="以官方MCP SDK启动stdio只读项目工具")
    parser.add_argument(
        "operation",
        nargs="?",
        choices=[*READ_SCHEMAS, *WRITE_SCHEMAS, "action-status", "source-read"],
    )
    parser.add_argument(
        "--arguments", default="{}", help="操作的JSON参数，正文与文件中的指令不会执行"
    )
    parser.add_argument("--request-id")
    parser.add_argument("--expected-revision", type=int, default=0)
    args = parser.parse_args()
    client = ProjectClient(
        args.server,
        args.session_file,
        actor_id=args.actor,
        person_id=args.person,
        conversation_id=args.conversation,
    )
    if args.mcp:
        from .mcp_server import create_server

        create_server(client, allow_write=args.allow_write).run(transport="stdio")
        return
    if not args.operation:
        parser.error("请选择操作，或使用--mcp")
    values = json.loads(args.arguments)
    if args.operation in WRITE_SCHEMAS:
        if not args.allow_write:
            parser.error("项目客户端缺省只读；显式写入请指定--allow-write")
        operation = client.write(
            args.operation,
            values,
            client_request_id=args.request_id or str(uuid4()),
            expected_revision=args.expected_revision,
        )
    elif args.operation == "action-status":
        operation = client.status(values["action_id"])
    elif args.operation == "source-read":
        operation = client.source_read(values["source_ref"], values.get("requested", {}))
    else:
        operation = client.read(args.operation, values)
    try:
        print(json.dumps(asyncio.run(operation), ensure_ascii=False, indent=2))
    except Fault as error:
        parser.exit(1, error.code + "\n")


if __name__ == "__main__":
    main()
