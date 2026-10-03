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
        return _module("acme-thing", extra=f'"slots": {{"search_provider": {entry}}},')

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
        # whitespace (celerp/modules/loader.py _prepare_search_provider). A
        # handler that is a non-empty string but not that shape passes the old
        # emptiness check yet fails to resolve at load, so lint.py must reject
        # every malformed shape here, not just the empty one.
        for handler in ("run_query", "a:b:c", ":go", "thing.search:",
                        "thing search:go", "thing.search: go"):
            with self.subTest(handler=handler):
                folder = self._provider(
                    '{"handler": "%s", "result_key": "items", '
                    '"permission": "view_inventory"}' % handler)
                problems = lint.lint(folder)
                self.assertTrue(
                    any("search_provider" in p and "handler" in p for p in problems),
                    f"malformed handler {handler!r} not reported: {problems}")



class TestPricingActionSlot(unittest.TestCase):
    """The pricing_action slot puts a link on rows of an item's Pricing tab. The
    loader refuses a malformed entry, which stops the whole module loading, so
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
        # character (Celerp's is_app_local_path, which the loader applies).
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
        for key in ("href", "show", "presentaton", "requires_connector"):
            with self.subTest(key=key):
                problems = self._action(repr({"label": "Quote", "href_template": "/q", key: "x"}))
                self.assertTrue(any("unknown key" in p and repr(key) in p for p in problems),
                                f"{key!r} not reported: {problems}")

    def test_every_accepted_key_clean(self):
        self.assertEqual(self._action(repr({
            "label": "Quote", "label_key": "acme.quote", "permission": "set_inventory_prices",
            "href_template": "/q/{entity_id}/{price_list}/{field_name}#top",
            "show_on": ["sell"], "presentation": "page"})), [])


def tearDownModule():
    for path in pathlib.Path(tempfile.gettempdir()).glob("tmp*"):
        if (path / "acme-thing").exists() or (path / "renamed-maintenance").exists():
            shutil.rmtree(path, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()


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
