# Proprietary and confidential source code.
# Developer: Sunilkumar Pathipati
# Responsibility: Ensures generated-test validator failures are never silently swallowed.

import unittest
from unittest.mock import patch

from UnitTest_gen.kotlin.test_code_utils.validate import _append_validation_rule_issues


class ValidationDispatchTest(unittest.TestCase):
    def test_validator_type_error_propagates(self):
        def broken_validator(test_code, output_file_path, source_code, test_report):
            raise TypeError("validator implementation failed")

        with patch("UnitTest_gen.kotlin.validation_rules.VALIDATION_RULES", [broken_validator]):
            with self.assertRaisesRegex(TypeError, "validator implementation failed"):
                _append_validation_rule_issues([], "code", "Test.kt", "source", object())


if __name__ == "__main__":
    unittest.main()
