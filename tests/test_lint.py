#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Tests for lint.py, the checker a module author runs before every restart.

Stdlib unittest only: these run from a bare clone with no Celerp installed,
which is the whole point of lint.py.
"""
from __future__ import annotations

import ast
import importlib.util
import os
import pathlib
import re
import shutil
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("celerp_module_lint", ROOT / "lint.py")
lint = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lint)

# The repository's one module, under whatever name it has been given.
MODULE = lint.find_module(ROOT)


MANIFEST = '''PLUGIN_MANIFEST = {
    "name": "%s",
    "version": "0.1.0",
    "display_name": "Thing",
    "license": "MIT",
    %s
    "ui_routes": "thing.ui_routes",
}
'''


# The temp folders this process made, removed when the module finishes. Only these:
# test workers running in parallel share one temp directory.
_TEMP_DIRS: list[pathlib.Path] = []


def _module(folder_name: str, manifest_name: str | None = None,
            extra: str = "", body: str | None = None) -> pathlib.Path:
    """A throwaway module folder with the given manifest."""
    tmp = pathlib.Path(tempfile.mkdtemp())
    _TEMP_DIRS.append(tmp)
    folder = tmp / folder_name
    folder.mkdir()
    text = body if body is not None else MANIFEST % (manifest_name or folder_name, extra)
    (folder / "__init__.py").write_text(text, encoding="utf-8")
    # The route modules the fixtures name.
    (folder / "thing").mkdir()
    (folder / "thing" / "__init__.py").write_text("", encoding="utf-8")
    for kind in ("api", "ui"):
        (folder / "thing" / f"{kind}_routes.py").write_text(
            f"def setup_{kind}_routes(app):\n    pass\n", encoding="utf-8")
    return folder


_RESERVED_NAMES = "'celerp-' or 'celerp_', in any letter case, are reserved for Marketplace modules"


class TestShippedModule(unittest.TestCase):
    def test_template_module_passes_clean(self):
        self.assertEqual(lint.lint(MODULE), [])

    def test_template_module_makes_no_network_calls_of_its_own(self):
        """The example reaches Celerp's API through api_request, never a client of
        its own: raw networking sends a module to review."""
        raw = {"httpx", "requests", "aiohttp", "urllib.request", "urllib3", "socket",
               "http.client", "ssl"}
        found = []
        for py_file in MODULE.rglob("*.py"):
            rel = py_file.relative_to(MODULE)
            if rel.parts[0] == "tests":
                continue
            for node in ast.walk(ast.parse(py_file.read_text(encoding="utf-8"))):
                names = []
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names = [node.module] + [f"{node.module}.{a.name}" for a in node.names]
                    names += [a.name for a in node.names]
                found += [f"{rel}: {n}" for n in names
                          if n == "API_BASE" or any(n == r or n.startswith(r + ".")
                                                    for r in raw)]
        self.assertEqual(found, [])


class TestFindModule(unittest.TestCase):
    """The repo holds one module under whatever name its author gave it. The tests
    and CI find it the way Celerp does, by the folder whose __init__.py holds
    PLUGIN_MANIFEST, so a renamed module needs no edit anywhere else."""

    def _root(self, *modules: str, plain: tuple[str, ...] = ()) -> pathlib.Path:
        root = pathlib.Path(tempfile.mkdtemp())
        _TEMP_DIRS.append(root)
        for name in modules:
            (root / name).mkdir()
            (root / name / "__init__.py").write_text(MANIFEST % (name, ""), encoding="utf-8")
        for name in plain + ("tests", ".github"):
            (root / name).mkdir()
            (root / name / "__init__.py").write_text("", encoding="utf-8")
        return root

    def test_a_renamed_module_is_found(self):
        root = self._root("visit-log", plain=("helpers",))
        self.assertEqual(lint.find_module(root), root / "visit-log")

    def test_a_manifest_with_a_syntax_error_is_still_found(self):
        # Lint then names the syntax error, instead of CI saying there is no module.
        root = self._root()
        (root / "visit-log").mkdir()
        (root / "visit-log" / "__init__.py").write_text("PLUGIN_MANIFEST = {\n", encoding="utf-8")
        self.assertEqual(lint.find_module(root), root / "visit-log")

    def test_no_module_is_named_plainly(self):
        with self.assertRaises(lint.ModuleFolderError) as caught:
            lint.find_module(self._root(plain=("helpers",)))
        self.assertIn("no module folder", str(caught.exception))

    def test_two_modules_are_named_plainly(self):
        with self.assertRaises(lint.ModuleFolderError) as caught:
            lint.find_module(self._root("acme-maintenance", "visit-log"))
        message = str(caught.exception)
        self.assertIn("acme-maintenance", message)
        self.assertIn("visit-log", message)
        self.assertIn("delete", message)

    def test_cli_find_prints_the_folder_name(self):
        import subprocess
        import sys
        root = self._root("visit-log")
        run = subprocess.run([sys.executable, str(ROOT / "lint.py"), "--find"], cwd=root,
                             capture_output=True, text=True)
        self.assertEqual((run.returncode, run.stdout), (0, "visit-log\n"))

    def test_cli_find_fails_with_the_reason(self):
        import subprocess
        import sys
        root = self._root("acme-maintenance", "visit-log")
        run = subprocess.run([sys.executable, str(ROOT / "lint.py"), "--find"], cwd=root,
                             capture_output=True, text=True)
        self.assertEqual((run.returncode, run.stdout), (1, ""))
        self.assertIn("visit-log", run.stderr)

    def test_ci_names_no_module_folder(self):
        ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        self.assertNotIn("acme", ci)
        self.assertIn("python lint.py --find", ci)


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

    def test_protected_internal_imported_as_a_submodule_flagged(self):
        folder = _module("acme-thing")
        (folder / "service.py").write_text("from celerp.ai import quota\n", encoding="utf-8")
        problems = lint.lint(folder)
        self.assertTrue(any("'celerp.ai'" in p for p in problems), problems)

    def test_every_celerp_ai_module_flagged(self):
        """celerp.ai is protected as a whole, not file by file."""
        for source in ("import celerp.ai\n", "import celerp.ai.llm\n", "from celerp.ai import llm\n",
                       "from celerp.ai.tools import run\n",
                       'import importlib\nimportlib.import_module("celerp.ai.anything")\n'):
            with self.subTest(source=source):
                folder = _module("acme-thing")
                (folder / "service.py").write_text(source, encoding="utf-8")
                problems = lint.check(folder)[0]
                self.assertTrue(any("'celerp.ai'" in p for p in problems), problems)

    def test_public_module_api_not_flagged(self):
        folder = _module("acme-thing")
        (folder / "service.py").write_text(
            "from celerp.modules.api import ai_query, api_request, read_resource\n",
            encoding="utf-8")
        self.assertEqual(lint.check(folder)[0], [])

    def test_protected_internal_imported_by_name_at_run_time_flagged(self):
        for call in ('importlib.import_module("celerp.gateway")', '__import__("celerp.gateway")'):
            with self.subTest(call=call):
                folder = _module("acme-thing")
                (folder / "service.py").write_text(f"import importlib\n{call}\n",
                                                   encoding="utf-8")
                problems = lint.lint(folder)
                self.assertTrue(any("celerp.gateway" in p for p in problems), problems)

    def test_reserved_prefix_flagged(self):
        for name in ("celerp-thing", "Celerp-thing", "CELERP-thing", "celerp_thing"):
            with self.subTest(name=name):
                problems = lint.lint(_module(name))
                self.assertTrue(any(_RESERVED_NAMES in p for p in problems), problems)


class TestRuntimeRequirements(unittest.TestCase):
    """Celerp installs no Python packages for a module, so a module ships no
    requirements.txt."""

    def test_requirements_file_flagged(self):
        for rel in ("requirements.txt", "thing/requirements.txt", "Requirements.TXT",
                    "tests/requirements.txt"):
            with self.subTest(rel=rel):
                folder = _module("acme-thing")
                (folder / rel).parent.mkdir(parents=True, exist_ok=True)
                (folder / rel).write_text("requests\n", encoding="utf-8")
                problems = lint.check(folder)[0]
                self.assertTrue(any(p.startswith(rel) and "Celerp's installed dependencies" in p
                                    for p in problems), problems)

    def test_other_text_files_not_flagged(self):
        folder = _module("acme-thing")
        (folder / "thing" / "notes.txt").write_text("requests\n", encoding="utf-8")
        self.assertEqual(lint.check(folder)[0], [])


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

    def test_locales_not_flagged(self):
        """Celerp loads a module's locales folder, so the key is real."""
        folder = _module("acme-thing", extra='"locales": "locales",')
        self.assertEqual([p for p in lint.lint(folder) if "unknown" in p.lower()], [])

    def test_unread_keys_flagged(self):
        """requires, soft_depends and first_party are read by nothing in core."""
        for key in ("requires", "soft_depends", "first_party"):
            folder = _module("acme-thing", extra=f'"{key}": [],')
            problems = lint.lint(folder)
            self.assertTrue(any(repr(key) in p for p in problems),
                            f"{key!r} does nothing at load time: {problems}")


class TestSlotNames(unittest.TestCase):
    """Core ignores a slot it does not consume, so a misspelled slot name ships a
    module whose entry never appears, with nothing logged."""

    def test_unknown_slot_flagged(self):
        for slot in ("nav_items", "settings_tab"):
            folder = _module("acme-thing", extra=f'"slots": {{"{slot}": []}},')
            problems = lint.lint(folder)
            self.assertTrue(any(repr(slot) in p and "slot" in p for p in problems),
                            f"slot {slot!r} is never read: {problems}")

    def test_document_lifecycle_hooks_not_flagged(self):
        # Celerp fires these from its documents module, so a module may fill them.
        for slot in ("doc_finalize_hook", "on_doc_payment"):
            folder = _module("acme-thing", extra=f'"slots": {{"{slot}": []}},')
            self.assertEqual([p for p in lint.lint(folder) if "unknown slot" in p], [], slot)

    def test_consumed_slots_not_flagged(self):
        entries = ", ".join(f'"{slot}": []' for slot in sorted(lint.SLOT_NAMES))
        folder = _module("acme-thing", extra=f'"slots": {{{entries}}},')
        self.assertEqual([p for p in lint.lint(folder) if "unknown slot" in p], [])


class TestSearchProviderSlot(unittest.TestCase):
    """The search_provider slot descriptor: exactly one dict (never a list),
    three required keys, a fixed result_key vocabulary, and no invented keys. A
    misspelled, missing, or duplicated descriptor ships a provider the aggregator
    cannot call or a gate it cannot read, so lint.py has to name it before a
    restart."""

    def _provider(self, entry: str) -> pathlib.Path:
        folder = _module("acme-thing", extra=f'"slots": {{"search_provider": {entry}}},')
        (folder / "thing" / "search.py").write_text("async def go(q):\n    return []\n")
        return folder

    def test_template_sample_search_provider_clean(self):
        self.assertEqual(lint.lint(MODULE), [])

    def test_valid_provider_not_flagged(self):
        folder = self._provider('{"handler": "thing.search:go", '
                                '"result_key": "items", "permission": "view_inventory"}')
        self.assertEqual([p for p in lint.lint(folder) if "search_provider" in p], [])

    def test_missing_handler_flagged(self):
        folder = self._provider('{"result_key": "items", "permission": "view_inventory"}')
        problems = lint.lint(folder)
        self.assertTrue(any("search_provider" in p and "handler" in p for p in problems),
                        problems)

    def test_missing_result_key_flagged(self):
        folder = self._provider('{"handler": "thing.search:go", '
                                '"permission": "view_inventory"}')
        problems = lint.lint(folder)
        self.assertTrue(any("search_provider" in p and "result_key" in p for p in problems),
                        problems)

    def test_missing_permission_flagged(self):
        folder = self._provider('{"handler": "thing.search:go", "result_key": "items"}')
        problems = lint.lint(folder)
        self.assertTrue(any("search_provider" in p and "permission" in p for p in problems),
                        problems)

    def test_unknown_provider_key_flagged(self):
        folder = self._provider('{"handler": "thing.search:go", "result_key": "items", '
                                '"permission": "view_inventory", "public": True}')
        problems = lint.lint(folder)
        self.assertTrue(any("search_provider" in p and "public" in p for p in problems),
                        problems)

    def test_bad_result_key_flagged(self):
        folder = self._provider('{"handler": "thing.search:go", '
                                '"result_key": "widgets", "permission": "view_inventory"}')
        problems = lint.lint(folder)
        self.assertTrue(any("search_provider" in p and "result_key" in p for p in problems),
                        problems)

    def test_list_form_rejected(self):
        # A module contributes exactly one search provider, expressed as a single
        # dict. A list (even a well-formed one) is the old shape the core loader no
        # longer accepts, so the linter must reject it and name the one-descriptor
        # rule rather than silently validating the first entry.
        folder = self._provider('[{"handler": "thing.search:go", '
                                '"result_key": "items", "permission": "view_inventory"}]')
        problems = lint.lint(folder)
        self.assertTrue(any("search_provider" in p for p in problems),
                        f"a list descriptor must be reported: {problems}")

    def test_non_object_descriptor_rejected(self):
        # A scalar where the dict belongs is neither callable nor readable.
        folder = self._provider('"thing.search:go"')
        problems = lint.lint(folder)
        self.assertTrue(any("search_provider" in p for p in problems),
                        f"a non-object descriptor must be reported: {problems}")

    def test_malformed_handler_syntax_flagged(self):
        # The core loader resolves the handler as exactly module:function: one
        # colon, a non-empty module path and a non-empty function name, no
        # whitespace (celerp/modules/loader.py _check_slot_callable), naming a
        # function the module defines. A handler that is a non-empty string but
        # not that shape fails to resolve at load, so lint.py must reject every
        # malformed shape here, not just the empty one.
        for handler in ("run_query", "a:b:c", ":go", "thing.search:",
                        "thing search:go", "thing.search: go", "thing.search:nothing",
                        "thing..search:go"):
            with self.subTest(handler=handler):
                folder = self._provider(
                    '{"handler": "%s", "result_key": "items", '
                    '"permission": "view_inventory"}' % handler)
                problems = lint.lint(folder)
                self.assertTrue(
                    any("search_provider" in p and "handler" in p for p in problems),
                    f"malformed handler {handler!r} not reported: {problems}")



class TestItemActionSlot(unittest.TestCase):
    """item_action buttons link with the same rules as Pricing-row links: inside
    Celerp, braces only around {entity_id}. From Celerp 2.5.4 the loader refuses the
    module otherwise; these tests need no Celerp, so they hold on any release."""

    def _action(self, entry: str) -> list[str]:
        folder = _module("acme-thing", extra=f'"slots": {{"item_action": [{entry}]}},')
        return [p for p in lint.lint(folder) if "item_action" in p]

    def test_well_formed_action_clean(self):
        self.assertEqual(self._action(
            '{"label": "Ship", "href_template": "/ship/{entity_id}?from=item", "icon": "x"}'), [])

    def test_malformed_action_flagged(self):
        for entry, word in (
            ('{"label": "Ship"}', "needs an href_template"),
            ('{"href_template": "https://evil.example/{entity_id}"}', "inside Celerp"),
            ('{"href_template": "//evil.example/{entity_id}"}', "inside Celerp"),
            ('{"href_template": "/ship/{entity_id"}', "stray brace"),
            ('{"href_template": "/ship/{price_list}"}', "{price_list}"),
            ('"Ship"', "dict"),
        ):
            with self.subTest(entry=entry):
                problems = self._action(entry)
                self.assertTrue(any(word in p for p in problems), f"{word!r} not in {problems}")


class TestPricingActionSlot(unittest.TestCase):
    """The pricing_action slot puts a link on rows of an item's Pricing tab. The
    2.5.4 loader refuses a malformed entry, which stops the whole module loading, so
    lint.py reports the same shapes first."""

    def _action(self, entry: str) -> list[str]:
        folder = _module("acme-thing", extra=f'"slots": {{"pricing_action": [{entry}]}},')
        return [p for p in lint.lint(folder) if "pricing_action" in p]

    def test_well_formed_action_clean(self):
        self.assertEqual(self._action(
            '{"label": "Quote", "show_on": ["sell", "manual"], "presentation": "page", '
            '"href_template": "/q/{entity_id}?list={price_list}&f={field_name}"}'), [])

    def test_malformed_action_flagged(self):
        for entry, word in (
            ('{"label": "Quote"}', "href_template"),
            ('{"href_template": "/q/{item}"}', "{item}"),
            ('{"href_template": "/q", "show_on": ["sold"]}', "show_on"),
            ('{"href_template": "/q", "show_on": "sell"}', "show_on"),
            ('{"href_template": "/q", "show_on": ["sell", "cost"]}', "never"),
            ('{"href_template": "/q", "presentation": "modal"}', "presentation"),
            ('"Quote"', "dict"),
        ):
            with self.subTest(entry=entry):
                problems = self._action(entry)
                self.assertTrue(any(word in p for p in problems),
                                f"{entry} not reported with {word!r}: {problems}")

    def test_href_template_outside_celerp_flagged(self):
        # Core puts the item id, list name and field name into this link, so it must
        # stay inside Celerp: one leading /, never //, no backslash, no control
        # character (Celerp's is_app_local_path, which the 2.5.4 loader applies).
        for href in ("https://evil.example/q/{entity_id}", "javascript:alert(1)",
                     "//evil.example/q", "/\\evil.example/q", "/q/\x01", "/q/\x7f",
                     "q/{entity_id}", "{entity_id}"):
            with self.subTest(href=href):
                problems = self._action(repr({"label": "Quote", "href_template": href}))
                self.assertTrue(any("inside Celerp" in p for p in problems),
                                f"{href!r} not reported: {problems}")

    def test_stray_brace_flagged(self):
        for href in ("/q/{entity_id", "/q/entity_id}", "/q/{entity_id}}", "/q/{{entity_id}}",
                     "/q/{entity_id}?x={", "/q/{price_list{entity_id}"):
            with self.subTest(href=href):
                problems = self._action(repr({"label": "Quote", "href_template": href}))
                self.assertTrue(any("brace" in p for p in problems),
                                f"{href!r} not reported: {problems}")

    def test_unknown_key_flagged(self):
        for key in ("href", "show", "presentaton", "requires_connectr"):
            with self.subTest(key=key):
                problems = self._action(repr({"label": "Quote", "href_template": "/q", key: "x"}))
                self.assertTrue(any("unknown key" in p and repr(key) in p for p in problems),
                                f"{key!r} not reported: {problems}")

    def test_requires_connector_is_a_connector_id(self):
        # Celerp shows a pricing action naming a connector only while the company is
        # connected to it, as it does for the other module actions.
        for value in (None, "", "shopify"):
            with self.subTest(value=value):
                self.assertEqual(self._action(repr(
                    {"label": "Quote", "href_template": "/q", "requires_connector": value})), [])
        for value in (1, True, ["shopify"]):
            with self.subTest(value=value):
                problems = self._action(repr(
                    {"label": "Quote", "href_template": "/q", "requires_connector": value}))
                self.assertTrue(any("requires_connector must be a connector id" in p
                                    for p in problems), problems)

    def test_every_accepted_key_clean(self):
        self.assertEqual(self._action(repr({
            "label": "Quote", "label_key": "acme.quote", "permission": "set_inventory_prices",
            "href_template": "/q/{entity_id}/{price_list}/{field_name}#top",
            "show_on": ["sell"], "presentation": "page"})), [])



class TestCompanyBackup(unittest.TestCase):
    """Every table the module owns says whether it travels with a company backup."""

    def _folder(self, declared: str) -> pathlib.Path:
        folder = _module("acme-thing", extra=f'"table_prefix": "acme_", {declared}')
        (folder / "models.py").write_text('class Thing:\n    __tablename__ = "acme_things"\n', encoding="utf-8")
        return folder

    def _problems(self, folder: pathlib.Path) -> list[str]:
        return [p for p in lint.lint(folder) if "company_backup" in p]

    def test_declared_table_clean(self):
        self.assertEqual(self._problems(self._folder('"company_backup": {"acme_things": "exclude"},')), [])

    def test_undeclared_table_flagged(self):
        problems = self._problems(self._folder(""))
        self.assertTrue(any("acme_things" in p and "refuse" in p for p in problems), problems)

    def test_unknown_value_flagged(self):
        problems = self._problems(self._folder('"company_backup": {"acme_things": "yes"},'))
        self.assertTrue(any("'yes'" in p for p in problems), problems)

    def test_table_outside_prefix_flagged(self):
        problems = self._problems(self._folder('"company_backup": {"acme_things": "include", "items": "include"},'))
        self.assertTrue(any("'items'" in p for p in problems), problems)


class TestMalformedManifestValues(unittest.TestCase):
    """A manifest is whatever literal the author wrote. lint.py names every shape it
    cannot use as a problem and never stops on a traceback, because a traceback names
    nothing."""

    def _problems(self, extra: str = "", body: str | None = None) -> list[str]:
        folder = _module("acme-thing", extra=extra, body=body)
        (folder / "models.py").write_text('class Thing:\n    __tablename__ = "acme_things"\n',
                                          encoding="utf-8")
        return lint.lint(folder)

    def _assert_reported(self, word: str, extra: str = "", body: str | None = None):
        problems = self._problems(extra, body)
        self.assertTrue(any(word in p for p in problems), f"{word!r} not in {problems}")

    def test_show_on_member_that_is_not_a_trait_name(self):
        for member in ("{}", "[]", '["sell"]', "1", "None", "1.5", "True"):
            with self.subTest(member=member):
                self._assert_reported("show_on", f'"slots": {{"pricing_action": '
                                      f'[{{"href_template": "/q", "show_on": ["sell", {member}]}}]}},')

    def test_show_on_that_is_not_a_list(self):
        for value in ('"sell"', '{"sell": 1}', "1", "None", '("sell",)'):
            with self.subTest(value=value):
                self._assert_reported("show_on", f'"slots": {{"pricing_action": '
                                      f'[{{"href_template": "/q", "show_on": {value}}}]}},')

    def test_manifest_that_is_not_a_dict(self):
        for value in ('["acme-thing"]', '"acme-thing"', "None", '{"name", "version"}'):
            with self.subTest(value=value):
                self._assert_reported("PLUGIN_MANIFEST must be a dict",
                                      body=f"PLUGIN_MANIFEST = {value}\n")

    def test_slots_that_is_not_a_dict(self):
        for value in ('["nav"]', '"nav"', "1"):
            with self.subTest(value=value):
                self._assert_reported("slots must be a dict", f'"slots": {value},')

    def test_keys_of_mixed_types(self):
        # sorted() cannot order 1 against "x"; every unknown key is still named.
        for extra, word in (
            ('1: "x", (1, 2): "y",', "unknown key 1"),
            ('"slots": {1: [], "nav": []},', "unknown slot 1"),
            ('"slots": {"nav": [{"key": "t", "href": "/t", 1: "x", "icn": "y"}]},', "unknown key 1"),
            ('"slots": {"search_provider": {"handler": "a:b", "result_key": "items", '
             '"permission": "view_inventory", 1: "x", "extra": "y"}},', "unknown key 1"),
            ('"slots": {"pricing_action": [{"href_template": "/q", 1: "x", "extra": "y"}]},',
             "unknown key 1"),
            ('"table_prefix": "acme_", "company_backup": {"acme_things": "include", 1: "include"},',
             "names 1"),
        ):
            with self.subTest(extra=extra):
                self._assert_reported(word, extra)

    def test_search_provider_result_key_that_is_not_a_string(self):
        for value in ('["items"]', '{"items": 1}', "None", "1"):
            with self.subTest(value=value):
                self._assert_reported("result_key", '"slots": {"search_provider": {"handler": "a:b", '
                                      f'"permission": "view_inventory", "result_key": {value}}}}},')

    def test_company_backup_value_that_is_not_a_string(self):
        for value in ('["include"]', '{"include": 1}', "None", "1"):
            with self.subTest(value=value):
                self._assert_reported("company_backup says",
                                      f'"table_prefix": "acme_", "company_backup": {{"acme_things": {value}}},')

    def test_table_prefix_that_is_not_a_string(self):
        for value in ('["acme_"]', "1", '{"acme_": 1}'):
            with self.subTest(value=value):
                self._assert_reported("table_prefix",
                                      f'"table_prefix": {value}, "company_backup": {{"acme_things": "include"}},')

    def test_no_value_in_any_key_lint_reads_stops_it(self):
        # The crash class, swept: every key lint.py reads, holding each shape a literal
        # can take. Each must come back as a list of problems, never a traceback.
        shapes = ("{}", "[]", "[{}]", "[[]]", "[1, None]", "1", "1.5", "None", "True",
                  '"x"', '{1: "x", "y": []}', '("x",)')
        entry_keys = {
            "nav": ("key", "href", "label", "permission"),
            "search_provider": ("handler", "result_key", "permission"),
            "item_action": ("href_template", "label"),
            "pricing_action": ("href_template", "show_on", "presentation", "label"),
        }
        cases = [f'"{key}": {shape},' for key in sorted(lint.MANIFEST_KEYS - {"name"})
                 for shape in shapes]
        cases += [f'"slots": {{"{slot}": {shape}}},' for slot in sorted(lint.SLOT_NAMES)
                  for shape in shapes]
        cases += [f'"slots": {{"{slot}": [{{"href_template": "/q", "{key}": {shape}}}]}},'
                  for slot, keys in entry_keys.items() for key in keys for shape in shapes]
        cases += [f'"table_prefix": "acme_", "company_backup": {{"acme_things": {shape}}},'
                  for shape in shapes]
        for extra in cases:
            with self.subTest(extra=extra):
                self.assertIsInstance(self._problems(extra), list)
        for shape in shapes:
            with self.subTest(name=shape):
                self.assertIsInstance(self._problems(body=MANIFEST.replace('"%s"', shape)
                                                     % ("",)), list)


_NOT_STR = (1, 1.5, True, ["x"], {"x": 1}, ("x",))
_NOT_DICT = ("x", 1, True, ["x"], ("x",))


class TestManifestFieldTypes(unittest.TestCase):
    """Every top-level manifest field Celerp reads has one type. A value of any other
    type is named as a problem on its own: the fixture is clean apart from that one
    field, so the problem cannot hide behind, or be stood in for by, another one."""

    BASE = {"name": "acme-thing", "version": "0.1.0", "display_name": "Thing",
            "license": "MIT", "api_routes": "thing.api_routes",
            "ui_routes": "thing.ui_routes"}
    WRONG = {
        **{field: _NOT_STR for field in ("name", "version", "display_name", "label",
                                        "description", "license", "author",
                                        "min_celerp_version", "api_routes", "ui_routes",
                                        "migrations", "table_prefix")},
        **{field: _NOT_DICT for field in ("slots", "company_backup", "locales")},
        "depends_on": ("acme-base", 1, True, {"acme-base": 1}, ("acme-base",),
                       ["acme-base", 1], [None], [["acme-base"]]),
    }

    def _problems(self, **fields) -> list[str]:
        manifest = {**self.BASE, **fields}
        return lint.lint(_module("acme-thing", body=f"PLUGIN_MANIFEST = {manifest!r}\n"))

    def test_base_fixture_is_clean(self):
        self.assertEqual(self._problems(), [])

    def test_every_field_lint_knows_has_a_type(self):
        self.assertEqual(set(lint.MANIFEST_FIELD_TYPES), lint.MANIFEST_KEYS)
        self.assertEqual(set(self.WRONG), lint.MANIFEST_KEYS)

    def test_a_value_of_the_wrong_type_is_named(self):
        for field, values in sorted(self.WRONG.items()):
            for value in values:
                with self.subTest(field=field, value=value):
                    problems = self._problems(**{field: value})
                    self.assertEqual(len(problems), 1, problems)
                    self.assertTrue(problems[0].startswith(f"{field} must be "), problems)

    def test_min_celerp_version_number_is_not_a_version_string(self):
        # str(1) is "1", which the dotted-version check would accept.
        self.assertEqual(self._problems(min_celerp_version=1),
                         ["min_celerp_version must be a string, not int"])

    def test_depends_on_string_is_not_a_list_of_names(self):
        # Celerp would iterate "acme-base" letter by letter.
        self.assertEqual(self._problems(depends_on="acme-base"),
                         ["depends_on must be a list of strings, not str"])

    def test_values_of_the_right_type_are_clean(self):
        self.assertEqual(self._problems(
            label="Thing", description="Does things", author="Acme", min_celerp_version="2.5.4",
            depends_on=["acme-base"], locales={}, slots={}, migrations="thing.migrations",
            table_prefix="acme_", company_backup={}), [])

    def test_none_is_an_absent_optional_field(self):
        # Celerp reads None the same as a missing key, except for table_prefix.
        for field in sorted(lint.MANIFEST_KEYS - set(lint.REQUIRED_FIELDS) - {"table_prefix"}):
            with self.subTest(field=field):
                self.assertEqual(self._problems(**{field: None}), [])


class TestTablePrefixShape(unittest.TestCase):
    """The table_prefix rules Celerp checks without looking at any other module."""

    def _problems(self, extra: str) -> list[str]:
        return [p for p in lint.lint(_module("acme-thing", extra=extra)) if "table_prefix" in p]

    def test_well_formed_prefix_clean(self):
        for extra in ('"table_prefix": "acme_",', '"table_prefix": "acme_", "migrations": "thing.migrations",'):
            with self.subTest(extra=extra):
                self.assertEqual(self._problems(extra), [])

    def test_malformed_prefix_flagged_with_or_without_migrations(self):
        for prefix in ("", "a_", "acme", "_"):
            for migrations in ("", '"migrations": "thing.migrations",'):
                with self.subTest(prefix=prefix, migrations=migrations):
                    self.assertTrue(self._problems(f'"table_prefix": {prefix!r}, {migrations}'))

    def test_declared_none_prefix_flagged(self):
        # Celerp refuses a table_prefix key whose value is not a prefix, None included.
        for migrations in ("", '"migrations": "thing.migrations",'):
            with self.subTest(migrations=migrations):
                self.assertEqual(self._problems(f'"table_prefix": None, {migrations}'),
                                 ["table_prefix must name the tables the module owns, such as "
                                  "'acme_', or be left out"])

    def test_migrations_without_prefix_flagged(self):
        problems = self._problems('"migrations": "thing.migrations",')
        self.assertTrue(any("migrations" in p for p in problems), problems)


HOOKS = (
    "def sync_fn(*a):\n    return None\n\n\n"
    "async def async_fn(*a):\n    return None\n\n\n"
    "class Klass:\n    pass\n\n\n"
    "lam = lambda *a: None\n"
    "VALUE = 5\n"
    "alias = async_fn\n"
    "async def lineage_guard(*, session, entry, transition):\n    return None\n\n\n"
    "async def in_production(session, company_id):\n    return 0\n"
)


def _slots_module(slots: dict, files: dict | None = None) -> pathlib.Path:
    """acme-thing with `slots`, a thing/hooks.py of sample callables, and `files`."""
    folder = _module("acme-thing", extra=f'"slots": {slots!r},')
    for rel, text in {"thing/__init__.py": "", "thing/hooks.py": HOOKS, **(files or {})}.items():
        (folder / rel).parent.mkdir(parents=True, exist_ok=True)
        (folder / rel).write_text(text, encoding="utf-8")
    return folder


def _problems(slots: dict, files: dict | None = None) -> list[str]:
    return lint.check(_slots_module(slots, files))[0]


class TestSlotEntryRules(unittest.TestCase):
    """The rules the Celerp 2.5.4 loader applies to every slot entry, any slot."""

    def test_permission_must_be_a_registry_key(self):
        for key in ("permission", "write_permission"):
            for value in ("view_inventory", "manage_labels"):
                with self.subTest(key=key, value=value):
                    self.assertEqual(_problems({"category_schema": [
                        {"category": "c", "fields": [], key: value}]}), [])
            for value in ("", None, False, 0, [], "admin", "View_Inventory", ["view_inventory"]):
                with self.subTest(key=key, value=value):
                    problems = _problems({"category_schema": [
                        {"category": "c", "fields": [], key: value}]})
                    self.assertTrue(any(key in p and "permission key" in p for p in problems),
                                    problems)

    def test_an_unknown_slot_is_named_once_and_its_entries_not_read(self):
        problems = _problems({"settings_tab": [{"permission": "admin"}]})
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("unknown slot 'settings_tab'", problems[0])

    def test_requires_connector_is_a_connector_id_when_set(self):
        for value in (None, "", "shopify"):
            with self.subTest(value=value):
                self.assertEqual(_problems({"nav": [{"href": "/x", "requires_connector": value}]}),
                                 [])
        for value in (0, False, 1, True, ["shopify"], {"a": 1}):
            with self.subTest(value=value):
                problems = _problems({"nav": [{"href": "/x", "requires_connector": value}]})
                self.assertTrue(any("requires_connector" in p for p in problems), problems)

    def test_destinations_stay_inside_celerp(self):
        for slot, key in (("nav", "href"), ("nav", "settings_href"), ("bulk_action", "form_action")):
            base = {"form_action": "/x"} if slot == "bulk_action" else {}
            self.assertEqual(_problems({slot: [{**base, key: "/a/b?c=d"}]}), [], key)
            for value in ("//evil.example", "https://x.example", "/\\evil", "x", "", None, 1):
                with self.subTest(slot=slot, key=key, value=value):
                    problems = _problems({slot: [{**base, key: value}]})
                    self.assertTrue(any(key in p and "inside Celerp" in p for p in problems),
                                    problems)

    def test_bulk_action_needs_a_form_action(self):
        problems = _problems({"bulk_action": [{"label": "Ship"}]})
        self.assertTrue(any("needs a form_action" in p for p in problems), problems)

    def test_entries_are_dicts(self):
        for contribution in (None, "x", 1, [None], [{}, 1]):
            with self.subTest(contribution=contribution):
                problems = _problems({"nav": contribution})
                self.assertTrue(any("must be a dict" in p for p in problems), problems)
        self.assertEqual(_problems({"nav": []}), [])

    def test_projection_handler_needs_a_prefix(self):
        for prefix in (None, "", 1, ["acme."]):
            with self.subTest(prefix=prefix):
                problems = _problems({"projection_handler": [
                    {"handler": "thing.hooks:sync_fn", "prefix": prefix}]})
                self.assertTrue(any("entry 0 prefix must" in p for p in problems), problems)
        problems = _problems({"projection_handler": [{"handler": "thing.hooks:sync_fn"}]})
        self.assertTrue(any("needs a prefix" in p for p in problems), problems)
        self.assertEqual(_problems({"projection_handler": [
            {"handler": "thing.hooks:sync_fn", "prefix": "acme."}]}), [])

    def test_projection_handler_prefixes_may_not_overlap(self):
        for first, second in (("acme.", "acme."), ("acme.", "acme.order."), ("acme.order.", "acme.")):
            with self.subTest(prefixes=(first, second)):
                problems = _problems({"projection_handler": [
                    {"handler": "thing.hooks:sync_fn", "prefix": first},
                    {"handler": "thing.hooks:sync_fn", "prefix": second}]})
                self.assertIn(f"Slot 'projection_handler' prefixes {first!r} and {second!r} "
                              f"overlap; each event type may have one handler only.", problems)
        self.assertEqual(_problems({"projection_handler": [
            {"handler": "thing.hooks:sync_fn", "prefix": "acme.order."},
            {"handler": "thing.hooks:sync_fn", "prefix": "acme.item."}]}), [])

    def test_projection_handler_prefix_may_not_overlap_celerps_own(self):
        for prefix, core in (("sys.", "sys."), ("s", "shop.sync."), ("mp.order.", "mp.")):
            with self.subTest(prefix=prefix):
                problems = _problems({"projection_handler": [
                    {"handler": "thing.hooks:sync_fn", "prefix": prefix}]})
                self.assertIn(f"Projection prefix {prefix!r} overlaps {core!r}, which 'Celerp' "
                              f"already handles; each event type may have one handler only.",
                              problems)


class TestCallableSlots(unittest.TestCase):
    """A callable slot names a function in the module's own files, async exactly
    where Celerp awaits it."""

    def _one(self, slot: str, dotted, files: dict | None = None) -> list[str]:
        key, _ = lint.CALLABLE_SLOTS[slot]
        entry = {key: dotted, "result_key": "items", "permission": "view_inventory"} \
            if slot == "search_provider" else {key: dotted, "prefix": "acme."}
        return _problems({slot: entry if slot == "search_provider" else [entry]}, files)

    def test_async_exactly_where_awaited(self):
        # The keyword slots take exact arguments as well (TestHandlerKeywords).
        for slot, (key, awaited) in sorted(lint.CALLABLE_SLOTS.items()):
            if slot in lint.HANDLER_KEYWORDS:
                continue
            for name, is_async in (("sync_fn", False), ("async_fn", True), ("Klass", False),
                                   ("lam", False), ("alias", True)):
                with self.subTest(slot=slot, name=name):
                    problems = self._one(slot, f"thing.hooks:{name}")
                    if is_async == awaited:
                        self.assertEqual(problems, [])
                    else:
                        self.assertTrue(any("async" in p for p in problems), problems)

    def test_the_code_must_be_the_modules_own(self):
        files = {
            "thing/reexport.py": "from .hooks import sync_fn\nfrom thing.hooks import async_fn\n",
            "thing/foreign.py": "from os.path import join as sync_fn\n",
            "thing/broken.py": "def sync_fn(:\n",
            "thing/deco.py": "import functools\n\n\n@functools.lru_cache\ndef sync_fn():\n    pass\n",
            "thing/star.py": "from .hooks import *\n",
            "thing/nested.py": "if True:\n    def sync_fn():\n        pass\n",
        }
        self.assertEqual(self._one("doc_detail_actions", "thing.reexport:sync_fn", files), [])
        self.assertEqual(self._one("on_modules_ready", "thing.reexport:async_fn", files), [])
        for dotted in ("thing.foreign:sync_fn", "thing.broken:sync_fn", "thing.deco:sync_fn",
                       "thing.star:sync_fn", "thing.nested:sync_fn", "thing.hooks:missing",
                       "thing.hooks:VALUE", "thing.nope:sync_fn", "json:dumps",
                       "celerp.services.app_paths:is_app_local_path", "thing.hooks.sync_fn",
                       "thing.hooks:sync_fn:x", "", None, 5):
            with self.subTest(dotted=dotted):
                self.assertTrue(self._one("doc_detail_actions", dotted, files))

    def test_search_provider_handler_is_a_callable_slot(self):
        problems = self._one("search_provider", "thing.hooks:sync_fn")
        self.assertTrue(any("must be async" in p for p in problems), problems)


class TestHandlerKeywords(unittest.TestCase):
    """item_lineage_guard: Celerp awaits the handler with exactly its keyword
    arguments, so the handler takes exactly those."""

    FILES = {"thing/lineage.py": (
        "async def guard(*, session, entry, transition):\n    return None\n\n\n"
        "async def guard_plain(session, entry, transition):\n    return None\n\n\n"
        "async def guard_default(*, session, entry, transition=None):\n    return None\n\n\n"
        "async def in_production(*, session, company_id):\n    return 0\n\n\n"
        "alias_guard = guard\n\n\n"
        "def sync_guard(*, session, entry, transition):\n    return None\n\n\n"
        "async def short_guard(*, session, entry):\n    return None\n\n\n"
        "async def loose_guard(*, session, entry, transition, **more):\n    return None\n\n\n"
        "async def star_guard(*args, session, entry, transition):\n    return None\n\n\n"
        "async def positional_guard(session, entry, transition, /):\n    return None\n\n\n"
        "async def extra_guard(*, session, entry, transition, when=None):\n    return None\n\n\n"
        "def keep(fn):\n    return fn\n\n\n"
        "built_guard = keep(guard)\n"
        "lam_guard = lambda *, session, entry, transition: None\n"
    ), "thing/reexport.py": "from .lineage import guard\n"}

    def _one(self, slot: str, name: str) -> list[str]:
        return _problems({slot: [{"handler": name}]}, self.FILES)

    def test_contracts(self):
        self.assertEqual(lint.HANDLER_KEYWORDS, {
            "inventory_in_production": ("session", "company_id"),
            "item_lineage_guard": ("session", "entry", "transition"),
        })
        for slot in lint.HANDLER_KEYWORDS:
            self.assertEqual(lint.CALLABLE_SLOTS[slot], ("handler", True))
            self.assertIn(slot, lint.SLOT_CHECKS)

    def test_valid_fills_accepted(self):
        for slot, name in (
                ("item_lineage_guard", "thing.lineage:guard"),
                ("item_lineage_guard", "thing.lineage:guard_plain"),
                ("item_lineage_guard", "thing.lineage:guard_default"),
                ("item_lineage_guard", "thing.lineage:alias_guard"),
                ("item_lineage_guard", "thing.reexport:guard")):
            with self.subTest(slot=slot, name=name):
                self.assertEqual(self._one(slot, name), [])

    def test_wrong_shape_refused(self):
        for slot, name, reason in (
                ("item_lineage_guard", "thing.lineage:sync_guard", "must be async"),
                ("item_lineage_guard", "thing.lineage:lam_guard", "must be async"),
                ("item_lineage_guard", "thing.lineage:short_guard", "session, entry, transition"),
                ("item_lineage_guard", "thing.lineage:loose_guard", "session, entry, transition"),
                ("item_lineage_guard", "thing.lineage:star_guard", "session, entry, transition"),
                ("item_lineage_guard", "thing.lineage:positional_guard",
                 "session, entry, transition"),
                ("item_lineage_guard", "thing.lineage:extra_guard", "session, entry, transition"),
                ("item_lineage_guard", "thing.lineage:in_production", "session, entry, transition"),
                ("item_lineage_guard", "thing.lineage:built_guard", "cannot be followed")):
            with self.subTest(slot=slot, name=name):
                problems = self._one(slot, name)
                self.assertTrue(any(reason in p for p in problems), problems)

    def test_chart_of_accounts_names_are_unknown(self):
        """The chart of accounts is the bundled accounting module's alone, not a slot:
        the loader refuses a module that fills these names, and lint.py says so."""
        hooks = {"handler": "thing.hooks:async_fn"}
        folder = _slots_module({name: [hooks] for name in
                                ("journal_accounts", "chart_accounts", "add_chart_account")})
        problems = lint.check(folder)[0]
        for name in ("journal_accounts", "chart_accounts", "add_chart_account"):
            self.assertNotIn(name, lint.SLOT_NAMES)
            self.assertTrue(any(f"unknown slot {name!r}" in p for p in problems), problems)


class TestReservedTables(unittest.TestCase):
    """table_prefix may not claim a table Celerp reserves, a first-party module's
    included, whether or not that module is turned on."""

    def test_reserved_prefixes_refused(self):
        for prefix in ("label_", "marketplace_", "bank_", "alembic_", "instance_", "user_",
                       "sync_", "ai_", "work_"):
            with self.subTest(prefix=prefix):
                problems = lint.check(_module("acme-thing", extra=f'"table_prefix": {prefix!r},'))[0]
                self.assertTrue(any("claims Celerp's table" in p for p in problems), problems)

    def test_own_prefix_clean(self):
        for prefix in ("acme_", "labelz_", "banking_", "accounts_"):
            with self.subTest(prefix=prefix):
                self.assertEqual(
                    lint.check(_module("acme-thing", extra=f'"table_prefix": {prefix!r},'))[0], [])


class TestModuleName(unittest.TestCase):
    """Celerp refuses a module whose name it would not install (importer._validate_name)."""

    def test_name_characters(self):
        for name in ("acme-thing", "acme_thing", "Acme9", "9acme", "a" * 64):
            with self.subTest(name=name):
                self.assertEqual(lint.lint(_module(name)), [])
        for name in ("-acme", "_acme", "acme.thing", "acme thing", "acmé", "a" * 65):
            with self.subTest(name=name):
                problems = lint.lint(_module(name))
                self.assertTrue(any("letters, digits" in p for p in problems), problems)


def _routes_module(routes: dict, files: dict) -> list[str]:
    """Problems for acme-thing with `routes` in its manifest and `files` in it."""
    extra = "".join(f'"{key}": {value!r},' for key, value in routes.items())
    folder = _module("acme-thing", body=MANIFEST.replace('    "ui_routes": "thing.ui_routes",\n', "")
                     % ("acme-thing", extra))
    for rel, text in files.items():
        (folder / rel).parent.mkdir(parents=True, exist_ok=True)
        (folder / rel).write_text(text, encoding="utf-8")
    return lint.check(folder)[0]


class TestRouteModules(unittest.TestCase):
    """api_routes and ui_routes name the module's own file and its own plain setup
    function, as Celerp checks before running any of the module's code and again
    before calling the setup."""

    FILES = {
        "thing/__init__.py": "",
        "thing/api.py": "def setup_api_routes(app):\n    pass\n",
        "thing/ui.py": "from .impl import setup_ui_routes\n",
        "thing/impl.py": "def setup_ui_routes(app):\n    pass\n",
        "thing/is_async.py": "async def setup_api_routes(app):\n    pass\n",
        "thing/missing.py": "def something_else(app):\n    pass\n",
        "thing/foreign.py": "from os.path import join as setup_api_routes\n",
        "thing/klass.py": "class setup_api_routes:\n    pass\n",
        "thing/lam.py": "setup_api_routes = lambda app: None\n",
        "thing/broken.py": "def setup_api_routes(:\n",
    }

    def test_the_modules_own_plain_setup(self):
        self.assertEqual(_routes_module({"api_routes": "thing.api", "ui_routes": "thing.ui"},
                                        self.FILES), [])

    def test_refused(self):
        for dotted in ("thing.is_async", "thing.missing", "thing.foreign", "thing.klass",
                       "thing.lam", "thing.broken", "thing.nope", "json", "ui.routes.reports",
                       "thing..api", "thing.api:x"):
            with self.subTest(dotted=dotted):
                problems = _routes_module({"api_routes": dotted}, self.FILES)
                self.assertTrue(any("api_routes" in p for p in problems), problems)

    def test_a_link_to_a_file_outside_the_module_is_refused(self):
        outside = pathlib.Path(tempfile.mkdtemp())
        _TEMP_DIRS.append(outside)
        (outside / "api.py").write_text("def setup_api_routes(app):\n    pass\n", encoding="utf-8")
        folder = _module("acme-thing")
        (folder / "thing" / "api.py").symlink_to(outside / "api.py")
        manifest = (folder / "__init__.py").read_text(encoding="utf-8")
        (folder / "__init__.py").write_text(
            manifest.replace('"ui_routes"', '"api_routes": "thing.api",\n    "ui_routes"'),
            encoding="utf-8")
        problems = lint.check(folder)[0]
        self.assertTrue(any("outside the module" in p for p in problems), problems)


class TestMigrationsPackage(unittest.TestCase):
    """The migrations package and every migration file in it stay inside the module
    (loader.module_migration_files)."""

    def _check(self, package, setup=None) -> list[str]:
        folder = _module("acme-thing", extra=f'"table_prefix": "acme_", "migrations": {package!r},')
        (folder / "thing" / "migrations").mkdir()
        (folder / "thing" / "migrations" / "m001.py").write_text("def upgrade():\n    pass\n",
                                                                 encoding="utf-8")
        if setup:
            setup(folder)
        return lint.check(folder)[0]

    def test_inside_the_module(self):
        self.assertEqual(self._check("thing.migrations"), [])
        self.assertEqual(self._check("thing.not_there"), [])

    def test_not_a_dotted_package_path(self):
        for package in ("../outside", "/tmp", "thing/migrations", "thing..migrations",
                        "thing.migrations.", "1thing.migrations", "thing.mig-rations"):
            with self.subTest(package=package):
                problems = self._check(package)
                self.assertTrue(any("dotted package path" in p for p in problems), problems)

    def test_links_outside_the_module(self):
        outside = pathlib.Path(tempfile.mkdtemp())
        _TEMP_DIRS.append(outside)
        (outside / "evil.py").write_text("def upgrade():\n    pass\n", encoding="utf-8")
        problems = self._check("thing.linked", lambda f: (f / "thing" / "linked").symlink_to(outside))
        self.assertTrue(any("folder outside the module" in p for p in problems), problems)
        problems = self._check("thing.migrations", lambda f: (
            f / "thing" / "migrations" / "m002.py").symlink_to(outside / "evil.py"))
        self.assertTrue(any("'m002.py'" in p and "outside the module" in p for p in problems),
                        problems)
        # Celerp skips files starting with _ when it runs migrations, but a link there
        # is still a link in the module (TestModuleTree).
        problems = self._check("thing.migrations", lambda f: (
            f / "thing" / "migrations" / "_helper.py").symlink_to(outside / "evil.py"))
        self.assertTrue(any("_helper.py" in p and "link" in p for p in problems), problems)



class TestModuleTree(unittest.TestCase):
    """Celerp reads every file of a module before it runs any of it, links never
    followed, so a link anywhere in the module or a file it cannot read refuses
    the module. The names it never reads (__pycache__, *.pyc and the like) are
    the exception."""

    def _outside(self) -> pathlib.Path:
        outside = pathlib.Path(tempfile.mkdtemp())
        _TEMP_DIRS.append(outside)
        (outside / "notes.py").write_text("x = 1\n", encoding="utf-8")
        return outside

    def test_a_plain_tree_is_clean(self):
        folder = _module("acme-thing")
        (folder / "thing" / "assets").mkdir()
        (folder / "thing" / "assets" / "logo.txt").write_text("x", encoding="utf-8")
        self.assertEqual(lint.check(folder)[0], [])

    def test_a_link_to_a_file_is_refused(self):
        for target in ("notes.py", "missing.py"):
            with self.subTest(target=target):
                folder = _module("acme-thing")
                (folder / "thing" / "notes.py").symlink_to(self._outside() / target)
                problems = lint.check(folder)[0]
                self.assertTrue(any("notes.py" in p and "link" in p for p in problems), problems)

    def test_a_link_to_a_folder_is_refused(self):
        for rel in ("thing/assets", "docs"):
            with self.subTest(rel=rel):
                folder = _module("acme-thing")
                (folder / rel).symlink_to(self._outside(), target_is_directory=True)
                problems = lint.check(folder)[0]
                self.assertTrue(any(rel in p and "link" in p for p in problems), problems)

    def test_names_celerp_never_reads_may_be_links(self):
        folder = _module("acme-thing")
        (folder / "thing" / "__pycache__").symlink_to(self._outside(), target_is_directory=True)
        (folder / "thing" / "old.pyc").symlink_to(self._outside() / "notes.py")
        self.assertEqual(lint.check(folder)[0], [])

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "root reads every file")
    def test_an_unreadable_file_or_folder_is_refused(self):
        for rel, make in (("thing/notes.txt", lambda p: p.write_text("x", encoding="utf-8")),
                          ("thing/private", lambda p: p.mkdir())):
            with self.subTest(rel=rel):
                folder = _module("acme-thing")
                path = folder / rel
                make(path)
                path.chmod(0)
                self.addCleanup(path.chmod, 0o700)
                problems = lint.check(folder)[0]
                self.assertTrue(any(rel in p and "read" in p for p in problems), problems)

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "root reads every file")
    def test_an_unreadable_file_celerp_never_reads_is_clean(self):
        folder = _module("acme-thing")
        (folder / "thing" / "__pycache__").mkdir()
        stale = folder / "thing" / "__pycache__" / "x.pyc"
        stale.write_bytes(b"x")
        stale.chmod(0)
        self.addCleanup(stale.chmod, 0o600)
        self.assertEqual(lint.check(folder)[0], [])


class TestManifestDeclaration(unittest.TestCase):
    """PLUGIN_MANIFEST is bound once, as one top-level literal, and never changed,
    read or replaced anywhere else in __init__.py: Python runs the whole file, so
    only then is the literal what the module declares (importer._manifest_node)."""

    @staticmethod
    def _init(template: str) -> list[str]:
        manifest = MANIFEST % ("acme-thing", "")
        return lint.check(_module("acme-thing", body=template.replace("{manifest}", manifest)))[0]

    def test_one_literal(self):
        for template in ("{manifest}", "from os.path import *\n{manifest}",
                         "{manifest}import os\nVERSION = '0.1.0'\n",
                         "{manifest}def f():\n    manifest = {}\n    return manifest\n"):
            with self.subTest(template=template):
                self.assertEqual(self._init(template), [])

    def test_bound_changed_or_read_elsewhere(self):
        for template in ("{manifest}{manifest}", "OTHER = {manifest}",
                         "{manifest}PLUGIN_MANIFEST['version'] = '0.2.0'\n",
                         "{manifest}PLUGIN_MANIFEST |= {}\n",
                         "{manifest}def f():\n    global PLUGIN_MANIFEST\n    PLUGIN_MANIFEST = {}\n",
                         "{manifest}NAME = PLUGIN_MANIFEST['name']\n",
                         "{manifest}del PLUGIN_MANIFEST\n",
                         "if True:\n    {manifest}",
                         "{manifest}from os.path import *\n"):
            with self.subTest(template=template):
                problems = self._init(template)
                self.assertTrue(any("PLUGIN_MANIFEST must be bound once" in p for p in problems),
                                problems)


class TestReachableSources(unittest.TestCase):
    """Every file Celerp runs for the module, and every file of the module's own
    those files import, transitively, must read and parse before any of it runs."""

    def test_an_imported_file_that_does_not_parse(self):
        for files in ({"thing/hooks.py": "from . import helper\n" + HOOKS,
                       "thing/helper.py": "def x(:\n"},
                      {"thing/hooks.py": "from .sub.deep import x\n" + HOOKS,
                       "thing/sub/__init__.py": "def x(:\n", "thing/sub/deep.py": "x = 1\n"},
                      {"thing/hooks.py": "import thing.helper\n" + HOOKS,
                       "thing/helper.py": "from .more import y\n", "thing/more.py": "y = (\n"},
                      {"thing/hooks.py": "def later():\n    from . import helper\n\n\n" + HOOKS,
                       "thing/helper.py": "def x(:\n"}):
            with self.subTest(files=sorted(files)):
                problems = _problems({"doc_detail_actions": [{"render": "thing.hooks:sync_fn"}]},
                                     files)
                self.assertTrue(any("does not parse" in p for p in problems), problems)

    def test_a_route_or_migration_import_that_does_not_parse(self):
        folder = _module("acme-thing", extra='"table_prefix": "acme_", "migrations": "thing.migrations",')
        (folder / "thing" / "migrations").mkdir()
        (folder / "thing" / "migrations" / "m001.py").write_text(
            "from ..helper import x\n", encoding="utf-8")
        (folder / "thing" / "helper.py").write_text("x = (\n", encoding="utf-8")
        problems = lint.check(folder)[0]
        self.assertTrue(any("helper.py" in p and "does not parse" in p for p in problems), problems)
        folder = _module("acme-thing")
        (folder / "thing" / "ui_routes.py").write_text(
            "from . import helper\n\n\ndef setup_ui_routes(app):\n    pass\n", encoding="utf-8")
        (folder / "thing" / "helper.py").write_bytes(b"x = '\xff'\n")
        problems = lint.check(folder)[0]
        self.assertTrue(any("helper.py" in p for p in problems), problems)

    def test_a_file_nothing_imports_may_not_parse(self):
        # Celerp never runs it; the import check alone keeps an eye on it.
        files = {"thing/hooks.py": "from . import helper\n" + HOOKS, "thing/helper.py": "x = 1\n",
                 "thing/scratch.py": "def x(:\n"}
        self.assertEqual(_problems({"doc_detail_actions": [{"render": "thing.hooks:sync_fn"}]},
                                   files), [])


class TestDynamicWrites(unittest.TestCase):
    """The code Celerp runs for a module must not write names its source does not
    show: through globals(), vars(), a module's __dict__, setattr, exec or eval,
    or by changing a function's code (loader._dynamic_write)."""

    SLOTS = {"doc_detail_actions": [{"render": "thing.hooks:sync_fn"}]}
    WRITES = ("globals()['sync_fn'] = sync_fn\n", "vars()['sync_fn'] = sync_fn\n",
              "import sys\nsetattr(sys.modules[__name__], 'sync_fn', sync_fn)\n",
              "exec('def sync_fn(*a):\\n    return None')\n",
              "eval('1')\n",
              "sync_fn.__code__ = (lambda *a: None).__code__\n",
              "import sys\nsys.modules[__name__].__dict__['sync_fn'] = sync_fn\n",
              "import sys\nname = 'sync' + '_fn'\nsetattr(sys.modules[__name__], name, sync_fn)\n",
              "from thing import PLUGIN_MANIFEST\n")
    FINE = ("x = getattr(Klass, 'mro')\n", "setattr(Klass, 'flag', 1)\n",
            "def f():\n    return locals()\n", "")

    def test_in_the_callable_module(self):
        for text in self.WRITES:
            with self.subTest(text=text):
                problems = _problems(self.SLOTS, {"thing/hooks.py": HOOKS + text})
                self.assertTrue(any("writes names dynamically" in p for p in problems), problems)
        for text in self.FINE:
            with self.subTest(text=text):
                self.assertEqual(_problems(self.SLOTS, {"thing/hooks.py": HOOKS + text}), [])

    def test_anywhere_celerp_runs(self):
        write = "globals()['x'] = 1\n"
        for files in ({"thing/hooks.py": "from . import helper\n" + HOOKS, "thing/helper.py": write},
                      {"thing/__init__.py": write},
                      {"thing/ui_routes.py": "def setup_ui_routes(app):\n    pass\n" + write}):
            with self.subTest(files=sorted(files)):
                problems = _problems(self.SLOTS, files)
                self.assertTrue(any("writes names dynamically" in p for p in problems), problems)
        manifest = MANIFEST % ("acme-thing", "")
        problems = lint.check(_module("acme-thing", body=manifest + write))[0]
        self.assertTrue(any("writes names dynamically" in p for p in problems), problems)

    def test_not_in_a_file_nothing_imports(self):
        self.assertEqual(_problems(self.SLOTS, {"thing/scratch.py": "globals()['x'] = 1\n"}), [])


class TestFindingKinds(unittest.TestCase):
    """A problem is something Celerp refuses or breaks on; an ignored finding is a
    part it reads straight past. Both fail the command line."""

    def test_split(self):
        problems, ignored = lint.check(_module(
            "acme-thing", extra='"icon": "x", "slots": {"nav": [{"href": "//x"}]},'))
        self.assertTrue(any("'icon'" in p for p in ignored), ignored)
        self.assertFalse(any("'icon'" in p for p in problems), problems)
        self.assertTrue(any("href" in p for p in problems), problems)

    def test_empty_slots_value_that_is_not_a_dict_is_a_problem(self):
        for value in ("[]", "0", '""'):
            with self.subTest(value=value):
                problems = lint.check(_module("acme-thing", extra=f'"slots": {value},'))[0]
                self.assertTrue(any(p.startswith("slots ") for p in problems), problems)

    def test_cli_exits_1_on_ignored_findings_alone(self):
        import subprocess
        import sys
        folder = _module("acme-thing", extra='"icon": "x",')
        run = subprocess.run([sys.executable, str(ROOT / "lint.py"), str(folder)],
                             capture_output=True, text=True)
        self.assertEqual(run.returncode, 1, run.stdout)
        self.assertIn("Celerp ignores", run.stdout)


class TestSlotNames(unittest.TestCase):
    """The loader refuses a module that fills a slot Celerp does not read, and a slot
    filled by Celerp's own modules only. The README lists the slots a module may fill."""

    def test_an_unknown_slot_is_a_problem_the_loader_refuses(self):
        problems, ignored = lint.check(_module("acme-thing", extra='"slots": {"nav_items": []},'))
        self.assertTrue(any("unknown slot 'nav_items'" in p and "refuses" in p for p in problems),
                        problems)
        self.assertFalse(any("nav_items" in p for p in ignored), ignored)

    def test_a_first_party_slot_is_refused(self):
        problems = _problems({"inventory_in_production": [{"handler": "thing.hooks:async_fn"}]})
        self.assertIn("Slot 'inventory_in_production' is filled by Celerp's own modules only.",
                      problems)

    def test_the_readme_lists_exactly_the_slots_a_module_may_fill(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        listing = readme.split("A module may fill these slots:", 1)[1].split(".", 1)[0]
        self.assertEqual(set(re.findall(r"`(\w+)`", listing)), lint.PUBLIC_SLOTS)
        self.assertEqual(lint.PUBLIC_SLOTS, lint.SLOT_NAMES - lint.FIRST_PARTY_SLOTS)
        self.assertIn("inventory_in_production", lint.FIRST_PARTY_SLOTS)

# One correct entry for every slot Celerp reads.
VALID_ENTRIES = {
    "nav": [{"key": "acme", "label": "Acme", "href": "/acme", "order": 40.5, "group": None}],
    "search_provider": {"handler": "thing.hooks:async_fn", "result_key": "items",
                        "permission": "view_inventory"},
    "bulk_action": [{"label": "Go", "form_action": "/acme/go", "action_type": "navigate",
                     "requires_connector": ""}],
    "item_action": [{"label": "Open", "href_template": "/acme/{entity_id}"}],
    "doc_detail_actions": [{"render": "thing.hooks:sync_fn"}],
    "doc_detail_badges": [{"render": "thing.hooks:sync_fn"}],
    "category_schema": [{"category": "Rings", "fields": [
        {"key": "size", "label": "Size", "type": "select", "options": ["5", "6"]}]}],
    "on_company_created": [{"handler": "thing.hooks:async_fn"}],
    "on_modules_ready": [{"handler": "thing.hooks:async_fn"}],
    "send_to_targets": [{"label": "Quote", "doc_type": "quotation"}],
    "catalog_channel": [{"id": "shop", "label": "Shop", "marker": "S", "can_create": True,
                         "write_permission": "edit_inventory", "requires_connector": "shop"}],
    "projection_handler": [{"prefix": "acme.", "handler": "thing.hooks:sync_fn"}],
    "pricing_action": [{"label": "Set", "href_template": "/acme/{entity_id}/{price_list}",
                        "show_on": ["sell"]}],
    "doc_finalize_hook": [{"handler": "thing.hooks:async_fn"}],
    "on_doc_payment": [{"handler": "thing.hooks:async_fn"}],
    "item_lineage_guard": [{"handler": "thing.hooks:lineage_guard"}],
    "inventory_in_production": [{"handler": "thing.hooks:in_production"}],
}

# Literals of every type a manifest can hold.
SHAPES = ({}, [], [{}], [[]], [1, None], 0, 1, 1.5, None, True, False, "", "x",
          {1: "x", "y": []}, ("x",), {"x"})


def _with(slot: str, **changes) -> dict:
    entry = VALID_ENTRIES[slot]
    if isinstance(entry, dict):
        return {slot: {**entry, **changes}}
    return {slot: [{**entry[0], **changes}]}


class TestEntryTypes(unittest.TestCase):
    """Each entry carries what the code reading its slot needs, in the type it reads
    it as (the loader's _SLOT_ENTRY_KEYS)."""

    def test_every_slot_has_its_entry_rules(self):
        self.assertEqual(set(lint.SLOT_ENTRY_KEYS), lint.SLOT_NAMES)
        self.assertEqual(set(VALID_ENTRIES), lint.SLOT_NAMES)

    def test_correct_entries_are_clean(self):
        self.assertEqual(_problems({slot: VALID_ENTRIES[slot] for slot in lint.PUBLIC_SLOTS}), [])

    def test_wrong_types_refused(self):
        for slot, key, value in (
                ("nav", "order", "1"), ("nav", "order", True), ("nav", "order", None),
                ("nav", "key", ["acme"]), ("nav", "group", 3), ("nav", "label", 5),
                ("nav", "label_key", ["nav.acme"]), ("bulk_action", "label", {"en": "Go"}),
                ("send_to_targets", "doc_type", None), ("send_to_targets", "doc_type", 3),
                ("catalog_channel", "id", 7), ("catalog_channel", "marker", 1),
                ("catalog_channel", "can_create", "yes"), ("category_schema", "category", ["R"]),
                ("category_schema", "fields", {"key": "size"}), ("item_action", "label", 3),
                ("projection_handler", "prefix", 1)):
            with self.subTest(slot=slot, key=key, value=value):
                problems = _problems(_with(slot, **{key: value}))
                self.assertTrue(any(f"{key} must be" in p for p in problems), problems)

    def test_required_keys(self):
        for slot, key in (("send_to_targets", "doc_type"), ("catalog_channel", "id"),
                          ("category_schema", "category"), ("category_schema", "fields"),
                          ("projection_handler", "prefix")):
            with self.subTest(slot=slot, key=key):
                entry = dict(VALID_ENTRIES[slot][0])
                del entry[key]
                self.assertTrue(any(f"needs a {key}" in p for p in _problems({slot: [entry]})))
                if key != "fields":
                    problems = _problems(_with(slot, **{key: ""}))
                    self.assertTrue(any(f"{key} must not be empty" in p for p in problems),
                                    problems)


class TestBulkActionType(unittest.TestCase):
    """The inventory toolbar runs a bulk action as htmx or navigate, nothing else."""

    def test_action_type(self):
        for value in ("htmx", "navigate"):
            with self.subTest(value=value):
                self.assertEqual(_problems(_with("bulk_action", action_type=value)), [])
        entry = dict(_with("bulk_action")["bulk_action"][0])
        del entry["action_type"]
        self.assertEqual(_problems({"bulk_action": [entry]}), [])
        for value in ("navgate", "", "HTMX"):
            with self.subTest(value=value):
                problems = _problems(_with("bulk_action", action_type=value))
                self.assertTrue(any("action_type must be one of" in p for p in problems), problems)


class TestCategorySchema(unittest.TestCase):
    """category_schema fields are field definitions the item form reads."""

    def test_fields(self):
        for fields in ([], [{"key": "size"}], [{"key": "s", "label": "S", "type": "text"}],
                       [{"key": "s", "options": []}]):
            with self.subTest(fields=fields):
                self.assertEqual(_problems(_with("category_schema", fields=fields)), [])
        for fields in (["size"], [{"label": "Size"}], [{"key": 3}], [{"key": ""}],
                       [{"key": "s", "options": "5,6"}], [{"key": "s", "label": 1}],
                       [{"key": "s", "type": None}], [None]):
            with self.subTest(fields=fields):
                problems = _problems(_with("category_schema", fields=fields))
                self.assertTrue(any("field definitions" in p for p in problems), problems)


class TestImportNames(unittest.TestCase):
    """A module's package names must be its own: not Python's and not Celerp's."""

    def test_own_names_clean(self):
        self.assertEqual(lint.lint(_module("acme-thing")), [])
        folder = _module("acme-thing")
        (folder / "acme_helpers.py").write_text("", encoding="utf-8")
        self.assertEqual(lint.check(folder)[0], [])

    def test_module_named_after_a_taken_package(self):
        for name in ("json", "celerp", "ui", "default_modules", "celerp_x"):
            with self.subTest(name=name):
                problems = lint.check(_module(name))[0]
                self.assertTrue(any(f"package name {name!r}" in p for p in problems), problems)

    def test_package_or_file_named_after_a_taken_package(self):
        for rel in ("json.py", "ui/__init__.py", "celerp/__init__.py", "celerp_inventory.py",
                    "premium_modules/__init__.py"):
            with self.subTest(rel=rel):
                folder = _module("acme-thing")
                (folder / rel).parent.mkdir(parents=True, exist_ok=True)
                (folder / rel).write_text("", encoding="utf-8")
                root = rel.split("/")[0].removesuffix(".py")
                problems = lint.check(folder)[0]
                self.assertTrue(any(f"package name {root!r}" in p for p in problems), problems)

    def test_a_plain_folder_is_not_a_package(self):
        folder = _module("acme-thing")
        (folder / "json").mkdir()
        (folder / "json" / "data.txt").write_text("", encoding="utf-8")
        self.assertEqual(lint.check(folder)[0], [])


class TestTablesOutsideThePrefix(unittest.TestCase):
    """Every table the module defines starts with its table_prefix."""

    def _problems(self, prefix: str | None, table: str) -> list[str]:
        extra = (f'"table_prefix": {prefix!r},' if prefix else "") + \
            f'"company_backup": {{{table!r}: "include"}},'
        folder = _module("acme-thing", extra=extra)
        (folder / "thing" / "models.py").write_text(
            "import sqlalchemy as sa\n"
            f"items = sa.Table({table!r}, sa.MetaData())\n", encoding="utf-8")
        return [p for p in lint.check(folder)[0] if "company_backup" not in p]

    def test_inside_the_prefix(self):
        self.assertEqual(self._problems("acme_", "acme_items"), [])

    def test_outside_the_prefix(self):
        problems = self._problems("acme_", "other_items")
        self.assertTrue(any("does not start with table_prefix" in p for p in problems), problems)

    def test_no_prefix(self):
        problems = self._problems(None, "acme_items")
        self.assertTrue(any("needs a table_prefix" in p for p in problems), problems)

    def test_tablename_counts_too(self):
        folder = _module("acme-thing", extra='"company_backup": {"acme_items": "include"},')
        (folder / "models.py").write_text('class Thing:\n    __tablename__ = "acme_items"\n',
                                          encoding="utf-8")
        problems = lint.check(folder)[0]
        self.assertTrue(any("needs a table_prefix" in p for p in problems), problems)


class TestWrongTypedLiterals(unittest.TestCase):
    """lint.py names a problem for any literal a manifest can hold, in any slot, key or
    field, and never stops on a traceback."""

    def _check(self, folder: pathlib.Path) -> list[str]:
        problems, ignored = lint.check(folder)
        self.assertIsInstance(problems, list)
        self.assertIsInstance(ignored, list)
        return problems

    def test_every_slot_key(self):
        keys = {"permission", "write_permission", "requires_connector", "href", "settings_href",
                "form_action", "href_template", "show_on", "presentation", "fields", "handler",
                "render", "action_type", "result_key"}
        for slot in sorted(VALID_ENTRIES):
            entry = VALID_ENTRIES[slot]
            entry = entry if isinstance(entry, dict) else entry[0]
            for key in sorted(keys | set(entry) | set(lint.SLOT_ENTRY_KEYS[slot])):
                for value in SHAPES + tuple([shape] for shape in SHAPES):
                    with self.subTest(slot=slot, key=key, value=value):
                        self._check(_slots_module(_with(slot, **{key: value})))

    def test_every_category_field_key(self):
        for key in ("key", *lint.CATEGORY_FIELD_KEYS):
            for value in SHAPES:
                with self.subTest(key=key, value=value):
                    self._check(_slots_module(_with("category_schema", fields=[
                        {"key": "size", key: value}])))

    def test_every_whole_contribution(self):
        for slot in sorted(VALID_ENTRIES) + ["unknown_slot"]:
            for value in SHAPES:
                with self.subTest(slot=slot, value=value):
                    self._check(_slots_module({slot: value}))

    def test_every_manifest_field(self):
        for field in sorted(lint.MANIFEST_KEYS):
            for value in SHAPES:
                with self.subTest(field=field, value=value):
                    self._check(_module("acme-thing", extra=f'"{field}": {value!r},'))

    def test_every_company_backup_entry(self):
        for value in SHAPES:
            for key in ("acme_items", 1, None):
                with self.subTest(key=key, value=value):
                    self._check(_module("acme-thing", extra=f'"table_prefix": "acme_", '
                                        f'"company_backup": {{{key!r}: {value!r}}},'))


def tearDownModule():
    for path in _TEMP_DIRS:
        shutil.rmtree(path, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
