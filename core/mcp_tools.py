# Proprietary and confidential source code.
# Developer: Sunilkumar Pathipati
# Responsibility: Provides the asynchronous MCP client wrapper for file, search, and build tools.
import os
import sys
from contextlib import AsyncExitStack

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


class LocalMcpTools:
    def __init__(self, server_script_path: str, allowed_root: str = None):
        self.server_script_path = os.path.abspath(os.path.expanduser(server_script_path))
        self.allowed_root = os.path.abspath(os.path.expanduser(allowed_root)) if allowed_root else os.getcwd()
        self.exit_stack = AsyncExitStack()
        self.session = None

    async def __aenter__(self):
        if not os.path.exists(self.server_script_path):
            raise FileNotFoundError(f"MCP server not found: {self.server_script_path}")

        server_env = os.environ.copy()
        server_env["MCP_ALLOWED_ROOT"] = self.allowed_root

        server_params = StdioServerParameters(
            command=sys.executable,
            args=[self.server_script_path],
            env=server_env,
        )

        read_stream, write_stream = await self.exit_stack.enter_async_context(stdio_client(server_params))
        self.session = await self.exit_stack.enter_async_context(ClientSession(read_stream, write_stream))
        await self.session.initialize()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self.exit_stack.aclose()

    async def call(self, tool_name: str, arguments: dict):
        result = await self.session.call_tool(tool_name, arguments)

        if not result.content:
            return ""

        parts = []
        for item in result.content:
            text = getattr(item, "text", None)
            parts.append(text if text is not None else str(item))

        return "\n".join(parts)

    async def read_file(self, path: str) -> str:
        return await self.call("read_file", {"path": path})

    async def read_file_range(self, path: str, start_line: int, end_line: int) -> str:
        return await self.call("read_file_range", {"path": path, "start_line": start_line, "end_line": end_line})

    async def write_file(self, path: str, content: str) -> str:
        return await self.call("write_file", {"path": path, "content": content})

    async def delete_file(self, path: str) -> str:
        return await self.call("delete_file", {"path": path})

    async def patch_file(self, path: str, old_text: str, new_text: str) -> str:
        return await self.call("patch_file", {"path": path, "old_text": old_text, "new_text": new_text})

    async def search_in_project(self, project_root: str, query: str, max_results: int = 100) -> str:
        return await self.call("search_in_project", {"project_root": project_root, "query": query, "max_results": max_results})
