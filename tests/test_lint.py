#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Tests for lint.py, the checker a module author runs before every restart.

Stdlib unittest only: these run from a bare clone with no Celerp installed,
which is the whole point of lint.py.
"""
from __future__ import annotations

import importlib.util
import pathlib
import shutil
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
MODULE = ROOT / "acme-maintenance"

_spec = importlib.util.spec_from_file_location("celerp_module_lint", ROOT / "lint.py")
lint = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lint)


MANIFEST = '''PLUGIN_MANIFEST = {
    "name": "%s",
    "version": "0.1.0",
    "display_name": "Thing",
    "license": "MIT",
    %s
    "ui_routes": "thing.ui_routes",
}
'''


def _module(folder_name: str, manifest_name: str | None = None,
            extra: str = "", body: str | None = None) -> pathlib.Path:
    """A throwaway module folder with the given manifest."""
    tmp = pathlib.Path(tempfile.mkdtemp())
    folder = tmp / folder_name
    folder.mkdir()
    text = body if body is not None else MANIFEST % (manifest_name or folder_name, extra)
    (folder / "__init__.py").write_text(text, encoding="utf-8")
    return folder


class TestShippedModule(unittest.TestCase):
    def test_template_module_passes_clean(self):
        self.assertEqual(lint.lint(MODULE), [])


class TestFolderNameMatchesManifest(unittest.TestCase):
    def test_folder_manifest_name_mismatch_flagged(self):
        folder = _module("renamed-maintenance", manifest_name="acme-maintenance")
        problems = lint.lint(folder)
        self.assertTrue(problems, "a folder/manifest name mismatch must be reported")
        joined = " ".join(problems)
        self.assertIn("renamed-maintenance", joined)
        self.assertIn("acme-maintenance", joined)

    def test_matching_names_not_flagged(self):
        folder = _module("acme-thing")
        self.assertEqual([p for p in lint.lint(folder) if "does not match" in p], [])

    def test_real_module_folder_matches_its_manifest(self):
        # Guards the shipped example: the folder is named for its manifest.
        self.assertEqual([p for p in lint.lint(MODULE) if "does not match" in p], [])


class TestMinCelerpVersionFormat(unittest.TestCase):
    def test_bad_min_celerp_version_format_flagged(self):
        folder = _module("acme-thing", extra='"min_celerp_version": "latest",')
        problems = lint.lint(folder)
        self.assertTrue(any("min_celerp_version" in p for p in problems), problems)

    def test_dotted_version_accepted(self):
        folder = _module("acme-thing", extra='"min_celerp_version": "1.4.2",')
        self.assertEqual([p for p in lint.lint(folder) if "min_celerp_version" in p], [])

    def test_absent_version_is_not_required(self):
        folder = _module("acme-thing")
        self.assertEqual([p for p in lint.lint(folder) if "min_celerp_version" in p], [])


class TestUnparseableInit(unittest.TestCase):
    def test_unparseable_init_reported_not_crash(self):
        folder = _module("acme-thing", body="PLUGIN_MANIFEST = {\n    'name': 'oops'\n")
        problems = lint.lint(folder)          # must not raise SyntaxError
        self.assertTrue(problems)
        self.assertTrue(any("parse" in p.lower() or "syntax" in p.lower() for p in problems),
                        problems)

    def test_missing_init_reported(self):
        empty = pathlib.Path(tempfile.mkdtemp())
        problems = lint.lint(empty)
        self.assertTrue(any("__init__.py" in p for p in problems), problems)


class TestProtectedImports(unittest.TestCase):
    def test_protected_import_flagged(self):
        folder = _module("acme-thing")
        (folder / "service.py").write_text("from celerp.gateway import thing\n",
                                           encoding="utf-8")
        problems = lint.lint(folder)
        self.assertTrue(any("celerp.gateway" in p for p in problems), problems)

    def test_reserved_prefix_flagged(self):
        folder = _module("celerp-thing")
        problems = lint.lint(folder)
        self.assertTrue(any("celerp-" in p for p in problems), problems)


class TestStrRenderedFragments(unittest.TestCase):
    """The single most expensive mistake a module author can make.

    `FT.__str__` returns the element's id, so `str(Div(..., id="content"))` is the
    string "content". Every HTMX swap then replaces the page region with that word.
    It raises nothing and logs nothing, so lint.py is the only place it can be caught
    early.
    """

    def test_flags_str_rendered_ft(self):
        folder = _module("acme-thing")
        (folder / "ui_routes.py").write_text(
            "from fasthtml.common import Div\n"
            "from starlette.responses import HTMLResponse\n"
            "def content():\n"
            "    return HTMLResponse(str(Div('rows', id='thing-content')))\n",
            encoding="utf-8")
        problems = lint.lint(folder)
        self.assertTrue(any("to_xml" in p for p in problems),
                        f"str()-rendered fragment not reported: {problems}")
        self.assertTrue(any("ui_routes.py" in p for p in problems), problems)

    def test_to_xml_not_flagged(self):
        folder = _module("acme-thing")
        (folder / "ui_routes.py").write_text(
            "from fasthtml.common import Div, to_xml\n"
            "from starlette.responses import HTMLResponse\n"
            "def content():\n"
            "    return HTMLResponse(to_xml(Div('rows', id='thing-content')))\n",
            encoding="utf-8")
        self.assertEqual([p for p in lint.lint(folder) if "to_xml" in p], [])

    def test_str_of_a_plain_value_not_flagged(self):
        folder = _module("acme-thing")
        (folder / "service.py").write_text(
            "def label(count):\n    return str(count) + ' due'\n", encoding="utf-8")
        self.assertEqual([p for p in lint.lint(folder) if "to_xml" in p], [])


class TestManifestKeys(unittest.TestCase):
    def test_flags_unknown_manifest_keys(self):
        folder = _module("acme-thing", extra='"colour": "blue",')
        problems = lint.lint(folder)
        self.assertTrue(any("colour" in p for p in problems),
                        f"an unknown manifest key is ignored at load time: {problems}")

    def test_flags_min_role_in_nav_slot(self):
        """min_role is banned vocabulary: core nav gating reads "permission"."""
        folder = _module("acme-thing", extra=(
            '"slots": {"nav": [{"label": "Thing", "href": "/thing", '
            '"min_role": "operator"}]},'))
        problems = lint.lint(folder)
        self.assertTrue(any("min_role" in p for p in problems), problems)
        self.assertTrue(any("permission" in p for p in problems),
                        f"the report does not name the key to use instead: {problems}")

    def test_permission_in_nav_slot_not_flagged(self):
        folder = _module("acme-thing", extra=(
            '"slots": {"nav": [{"label": "Thing", "href": "/thing", '
            '"permission": "view_inventory"}]},'))
        self.assertEqual([p for p in lint.lint(folder)
                          if "min_role" in p or "unknown" in p.lower()], [])

    def test_known_keys_not_flagged(self):
        folder = _module("acme-thing", extra='"min_celerp_version": "1.4.2",')
        self.assertEqual([p for p in lint.lint(folder) if "unknown" in p.lower()], [])


def tearDownModule():
    for path in pathlib.Path(tempfile.gettempdir()).glob("tmp*"):
        if (path / "acme-thing").exists() or (path / "renamed-maintenance").exists():
            shutil.rmtree(path, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
