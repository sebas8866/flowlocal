"""Import smoke test for flowlocal.ui (the CustomTkinter app window
package). Guarded with try/except around the customtkinter import in case
of headless CI environments where it (or its Tk dependency) can't load —
in that case the test is skipped rather than failing the whole suite.

Run with: py -3.11 -m unittest discover -s tests
or:       py -3.11 -m unittest tests.test_ui_import
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import customtkinter  # noqa: F401

    _CTK_AVAILABLE = True
except Exception:
    _CTK_AVAILABLE = False


@unittest.skipUnless(_CTK_AVAILABLE, "customtkinter not available in this environment")
class TestUiImport(unittest.TestCase):
    def test_ui_package_imports(self):
        import flowlocal.ui as ui

        self.assertTrue(hasattr(ui, "open_window"))
        self.assertTrue(callable(ui.open_window))

    def test_ui_submodules_import(self):
        from flowlocal.ui import theme, widgets, window  # noqa: F401
        from flowlocal.ui.pages import dictionary, history, home, settings  # noqa: F401

    def test_app_module_imports_with_ui(self):
        import flowlocal.app  # noqa: F401


if __name__ == "__main__":
    unittest.main()
