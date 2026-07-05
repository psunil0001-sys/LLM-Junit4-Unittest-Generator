# Proprietary and confidential source code.
# Developer: Sunilkumar Pathipati
# Responsibility: Tests pipeline configuration and feature gates.
"""Pipeline config loading and feature-gate tests."""

from __future__ import annotations

import argparse
import os
import unittest
from dataclasses import replace
from unittest.mock import patch

from UnitTest_gen.core.pipeline_config import (
    apply_cli_args,
    get_config,
    load_config_from_env,
    set_active_config,
)
from UnitTest_gen.core import model_runtime
from UnitTest_gen.kotlin import incremental_coverage
from UnitTest_gen.kotlin.memory_context import retrieve_generation_lessons
from UnitTest_gen.kotlin.test_code_utils.validate import validate_generated_test_code


class TestPipelineConfig(unittest.TestCase):
    def setUp(self):
        self._original_config = get_config()

    def tearDown(self):
        set_active_config(self._original_config)
        model_runtime.apply_pipeline_config(self._original_config)

    def test_env_defaults_include_guardrails_and_memory(self):
        with patch.dict(os.environ, {}, clear=True):
            config = load_config_from_env()
            self.assertTrue(config.enable_guardrails)
            self.assertFalse(config.enable_memory_lessons)
            self.assertEqual(config.incremental_line_budget, 80)
            self.assertEqual(config.incremental_safe_cap, 2)

    def test_incremental_limits_load_from_env_and_active_config(self):
        with patch.dict(
            os.environ,
            {
                "TESTGEN_INCREMENTAL_LINE_BUDGET": "42",
                "TESTGEN_INCREMENTAL_SAFE_CAP": "4",
            },
            clear=True,
        ):
            config = load_config_from_env()
        self.assertEqual(config.incremental_line_budget, 42)
        self.assertEqual(config.incremental_safe_cap, 4)

        set_active_config(config)
        self.assertEqual(incremental_coverage.incremental_line_budget(), 42)
        self.assertEqual(incremental_coverage.incremental_safe_cap(), 4)

    def test_incremental_limits_keep_minimum_one(self):
        with patch.dict(
            os.environ,
            {
                "TESTGEN_INCREMENTAL_LINE_BUDGET": "0",
                "TESTGEN_INCREMENTAL_SAFE_CAP": "-3",
            },
            clear=True,
        ):
            config = load_config_from_env()
        self.assertEqual(config.incremental_line_budget, 1)
        self.assertEqual(config.incremental_safe_cap, 1)

    def test_cli_coverage_buckets_override_defaults(self):
        config = load_config_from_env()
        args = argparse.Namespace(coverage_buckets=["attemptable", "blocked"])
        updated = apply_cli_args(config, args)
        self.assertEqual(("attemptable", "blocked"), updated.coverage_buckets)

    def test_cli_overrides_stuck_detector_and_slot_cache(self):
        config = load_config_from_env()
        args = argparse.Namespace(
            enable_stuck_detector=True,
            enable_slot_bin_cache=True,
            disable_slot_bin_cache=False,
            auto_start_servers=False,
            no_server_terminal=False,
            gradle_offline=False,
            disable_incremental_coverage=False,
            incremental_coverage_rounds=5,
            server_startup_timeout=90,
            index_root="/tmp/index",
            coding_server_command="echo coding",
            embedding_server_command="echo embedding",
        )
        updated = apply_cli_args(config, args)
        self.assertTrue(updated.model_stuck_detector_enabled)
        self.assertTrue(updated.save_llama_slot_bin)
        self.assertEqual(updated.incremental_coverage_rounds, 5)

    def test_apply_pipeline_config_syncs_runtime(self):
        config = load_config_from_env()
        config = apply_cli_args(
            config,
            argparse.Namespace(
                enable_stuck_detector=False,
                enable_slot_bin_cache=True,
                disable_slot_bin_cache=False,
                auto_start_servers=False,
                no_server_terminal=False,
                gradle_offline=False,
                disable_incremental_coverage=False,
                incremental_coverage_rounds=3,
                server_startup_timeout=None,
                index_root=None,
                coding_server_command=None,
                embedding_server_command=None,
            ),
        )
        set_active_config(config)
        model_runtime.apply_pipeline_config(config)
        self.assertTrue(model_runtime.SAVE_LLAMA_SLOT_BIN)

    def test_memory_lessons_gate(self):
        config = apply_cli_args(get_config(), argparse.Namespace(
            enable_stuck_detector=False,
            enable_slot_bin_cache=False,
            disable_slot_bin_cache=False,
            auto_start_servers=False,
            no_server_terminal=False,
            gradle_offline=False,
            disable_incremental_coverage=False,
            incremental_coverage_rounds=3,
            server_startup_timeout=None,
            index_root=None,
            coding_server_command=None,
            embedding_server_command=None,
        ))
        config = replace(get_config(), enable_memory_lessons=False)
        set_active_config(config)
        self.assertEqual(retrieve_generation_lessons({"viewmodel"}), "")

    def test_guardrails_gate(self):
        config = replace(get_config(), enable_guardrails=False)
        set_active_config(config)
        issues = validate_generated_test_code(
            "class FooTest { @org.junit.Test fun t() {} }",
            "/tmp/FooTest.kt",
            "",
        )
        self.assertEqual(issues, [])


if __name__ == "__main__":
    unittest.main()
