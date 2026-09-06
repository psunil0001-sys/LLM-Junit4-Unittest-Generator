"""Build planner / coder / fixer ADK LlmAgents."""

from __future__ import annotations

from google.adk.agents import LlmAgent

from UnitTest_gen.core.adk_agents.model import OpenAIChatLlm
from UnitTest_gen.core.adk_agents.tools import build_agent_tools

_COMMON = (
    "Work in Kotlin, JUnit4 (org.junit only); every @Test asserts real CUT behavior — "
    "never a bare println or an assertion-free method. The appended SYSTEM block is the "
    "single authority for the pipeline contract (test layer, tool access, write restriction, "
    "harness scope, Kover/verify) — follow it exactly. Keep production src/main untouched."
)

_PLAN_INSTRUCTION = (
    "You are the UnitTest_gen planner agent. Classify every DELTA line as testable or "
    "not_testable and persist an execute-ready *.plan.md (frontmatter + Markdown), setting "
    "test_layer=unit vs instrumented per item so the coder targets the right root. "
    "Use Read/Bash/Glob/Grep for discovery; Write/Edit only *.plan.md, test-root harness files, "
    "or build.gradle(.kts) — never src/main. Prefer absolute paths from the prompt.\n" + _COMMON
)

_CODER_INSTRUCTION = (
    "You are the UnitTest_gen coder agent. Implement THIS SLICE's @Test methods in the PIPELINE "
    "TARGET — a JUnit4 Kotlin class for the layer the prompt names (unit under src/test or "
    "instrumented under src/androidTest) — and invent only the harness this SOURCE needs. "
    "If PIPELINE TARGET does not exist yet, your next tool call after minimal discovery must be "
    "Write of that exact file_path with a complete JUnit4 class — do not end the turn with "
    "analysis prose and no Write. Edit/Write only under src/test, src/androidTest, "
    "build.gradle*, or UnitTest_gen. Never mutate src/main.\n" + _COMMON
)

_FIXER_INSTRUCTION = (
    "You are the UnitTest_gen fixer agent. Repair compile/test failures for THIS TARGET only, "
    "keeping the tests as valid JUnit4 Kotlin for the layer the prompt names (unit vs instrumented) "
    "and inventing/repairing harness when a failure is a missing host, manifest, nav, or dep. "
    "Edit/Write only under src/test, src/androidTest, build.gradle*, or UnitTest_gen. "
    "Never mutate src/main.\n" + _COMMON
)


def make_planner(
    *,
    temperature: float | None = None,
    presence_penalty: float | None = None,
    enable_thinking: bool = False,
    reasoning_budget: int = 0,
) -> LlmAgent:
    return LlmAgent(
        name="planner",
        model=OpenAIChatLlm(
            temperature=temperature,
            presence_penalty=presence_penalty,
            enable_thinking=enable_thinking,
            reasoning_budget=reasoning_budget,
        ),
        instruction=_PLAN_INSTRUCTION,
        tools=build_agent_tools(),
    )


def make_coder(
    *,
    temperature: float | None = None,
    presence_penalty: float | None = None,
    enable_thinking: bool = False,
    reasoning_budget: int = 0,
) -> LlmAgent:
    return LlmAgent(
        name="coder",
        model=OpenAIChatLlm(
            temperature=temperature,
            presence_penalty=presence_penalty,
            enable_thinking=enable_thinking,
            reasoning_budget=reasoning_budget,
        ),
        instruction=_CODER_INSTRUCTION,
        tools=build_agent_tools(),
    )


def make_fixer(
    *,
    temperature: float | None = None,
    presence_penalty: float | None = None,
    enable_thinking: bool = False,
    reasoning_budget: int = 0,
) -> LlmAgent:
    return LlmAgent(
        name="fixer",
        model=OpenAIChatLlm(
            temperature=temperature,
            presence_penalty=presence_penalty,
            enable_thinking=enable_thinking,
            reasoning_budget=reasoning_budget,
        ),
        instruction=_FIXER_INSTRUCTION,
        tools=build_agent_tools(),
    )
