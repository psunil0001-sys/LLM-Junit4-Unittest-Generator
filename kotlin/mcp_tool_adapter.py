# Proprietary and confidential source code.
# Developer: Sunilkumar Pathipati
# Responsibility: Provides Kotlin/Gradle-specific MCP helper calls over the generic core MCP client.
"""Kotlin/Gradle MCP adapter helpers."""

import asyncio
import time

from UnitTest_gen.core.mcp_tools import LocalMcpTools
from UnitTest_gen.core.pipeline_config import get_config


async def run_gradle_with_heartbeat(
    mcp_tools: LocalMcpTools,
    project_root: str,
    offline: bool = False,
    tasks=None,
    rerun_tasks: bool = False,
) -> str:
    arguments = {
        "project_root": project_root,
        "offline": offline,
        "tasks": tasks,
        "rerun_tasks": rerun_tasks,
    }
    gradle_task = asyncio.create_task(mcp_tools.call("run_gradle", arguments))
    started_at = time.monotonic()
    heartbeat_interval = get_config().gradle_heartbeat_seconds

    selected_tasks = tasks or []
    task_text = " ".join(selected_tasks) if selected_tasks else "default Gradle tasks"

    while not gradle_task.done():
        done, _ = await asyncio.wait({gradle_task}, timeout=heartbeat_interval)
        if done:
            break
        elapsed = int(time.monotonic() - started_at)
        print(
            f"💓 Gradle heartbeat: still running after {elapsed}s "
            f"({task_text})",
            flush=True,
        )

    return await gradle_task


async def find_kotlin_declaration(
    mcp_tools: LocalMcpTools,
    project_root: str,
    symbol: str,
    max_results: int = 50,
) -> str:
    return await mcp_tools.call(
        "find_kotlin_declaration",
        {"project_root": project_root, "symbol": symbol, "max_results": max_results},
    )


async def read_kotlin_file_around_symbol(
    mcp_tools: LocalMcpTools,
    project_root: str,
    symbol: str,
    context_lines: int = 80,
    max_results: int = 5,
) -> str:
    return await mcp_tools.call(
        "read_kotlin_file_around_symbol",
        {
            "project_root": project_root,
            "symbol": symbol,
            "context_lines": context_lines,
            "max_results": max_results,
        },
    )
