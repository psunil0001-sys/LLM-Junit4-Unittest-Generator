# Proprietary and confidential source code.
# Developer: Sunilkumar Pathipati
# Responsibility: Regression coverage for structure-aware supplemental Kotlin test merging.

import unittest

from UnitTest_gen.kotlin.test_code_utils.merge import merge_supplemental_test_code


class SemanticSupplementalMergeTest(unittest.TestCase):
    def test_preserves_supporting_fields_and_merges_setup_at_source_order(self):
        existing = """package sample
import org.junit.Before
import org.junit.Test

class ExampleTest {
    private lateinit var toolbar: Toolbar
    private lateinit var fragment: Fragment

    @Before
    fun setUp() {
        toolbar = mock()
        mockStatic(CarUi::class.java)
        launchFragment()
    }

    @Test
    fun existingTest() = Unit
}
"""
        supplemental = """package sample
import org.junit.Before
import org.junit.Test
import org.mockito.kotlin.argumentCaptor

class ExampleCoverageSupplementTest {
    private lateinit var toolbar: Toolbar
    private val menuCaptor = argumentCaptor<List<MenuItem>>()
    private var capturedMenuItems: List<MenuItem>? = null

    @Before
    fun setUp() {
        toolbar = mock()
        doAnswer { capturedMenuItems = menuCaptor.lastValue }.whenever(toolbar).setMenuItems(menuCaptor.capture())
        mockStatic(com.example.CarUi::class.java)
        launchFragment()
        attachFragment(fragment)
    }

    @Test
    fun menuCallbackTest() {
        capturedMenuItems?.first()?.performClick()
    }
}
"""

        merged = merge_supplemental_test_code(existing, supplemental)

        self.assertIn("private val menuCaptor", merged)
        self.assertIn("private var capturedMenuItems", merged)
        self.assertIn("fun menuCallbackTest()", merged)
        self.assertEqual(merged.count("fun setUp()"), 1)
        self.assertLess(merged.index("doAnswer"), merged.index("launchFragment()"))
        self.assertEqual(merged.count("toolbar = mock()"), 1)
        self.assertEqual(merged.count("mockStatic("), 1)
        self.assertNotIn("attachFragment(fragment)", merged)
        self.assertIn("import org.mockito.kotlin.argumentCaptor", merged)


if __name__ == "__main__":
    unittest.main()
