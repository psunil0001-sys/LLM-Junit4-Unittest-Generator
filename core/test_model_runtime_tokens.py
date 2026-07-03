# Proprietary and confidential source code.
# Developer: Sunilkumar Pathipati
# Responsibility: Smoke tests for prompt/output token estimation helpers.
"""Smoke tests for model_runtime token estimation."""

from __future__ import annotations

import unittest

from UnitTest_gen.core.model_runtime import estimate_messages_tokens, estimate_stream_tokens


class ModelRuntimeTokenEstimateTest(unittest.TestCase):
    def test_estimate_messages_tokens_empty(self):
        self.assertEqual((0, 0), estimate_messages_tokens([]))

    def test_estimate_messages_tokens_matches_joined_text(self):
        messages = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Generate a Kotlin test."},
        ]
        joined = "system\nYou are helpful.\n\nuser\nGenerate a Kotlin test."
        expected_tokens = estimate_stream_tokens(joined)
        tokens, chars = estimate_messages_tokens(messages)
        self.assertEqual(expected_tokens, tokens)
        self.assertEqual(len(joined), chars)


if __name__ == "__main__":
    unittest.main()
