# Proprietary and confidential source code.
# Developer: Sunilkumar Pathipati
# Responsibility: Registers Kotlin prompt stream hooks.
"""Prompting package hooks."""

from UnitTest_gen.kotlin.prompting.generation import _kotlin_stream_retry_instruction
from UnitTest_gen.core.model_runtime import (
    set_reasoning_fallback_detector,
    set_stream_retry_instruction_builder,
)
from UnitTest_gen.kotlin.test_code_utils.extract import looks_like_kotlin_test_output

_hooks_registered = False


def register_kotlin_stream_hooks() -> None:
    global _hooks_registered
    if _hooks_registered:
        return
    set_reasoning_fallback_detector(looks_like_kotlin_test_output)
    set_stream_retry_instruction_builder(_kotlin_stream_retry_instruction)
    _hooks_registered = True

__all__ = ["register_kotlin_stream_hooks"]
