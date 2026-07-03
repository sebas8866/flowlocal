"""Unit tests for flowlocal.context.

get_app_context() talks to real win32 APIs (win32gui/win32process/win32api),
which cannot be exercised deterministically in a headless test run. We only
verify the documented failure-path contract: any internal exception results
in None, never a raised exception.

Run with: py -3.11 -m unittest discover -s tests
or:       py -3.11 -m unittest tests.test_context
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flowlocal.context import get_app_context


class TestGetAppContext(unittest.TestCase):
    def test_never_raises_and_returns_str_or_none(self):
        # On a machine without a usable foreground window (or without
        # pywin32 available), this must return None rather than raising.
        try:
            result = get_app_context()
        except Exception as exc:  # pragma: no cover - contract violation
            self.fail(f"get_app_context() raised: {exc}")
        self.assertTrue(result is None or isinstance(result, str))


if __name__ == "__main__":
    unittest.main()
