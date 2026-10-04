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
                                 ["table_prefix is None; set it to the prefix of the tables the "
                                  "module owns, such as 'acme_', or leave it out"])

    def test_migrations_without_prefix_flagged(self):
        problems = self._problems('"migrations": "thing.migrations",')
        self.assertTrue(any("migrations" in p for p in problems), problems)


def tearDownModule():
    for path in pathlib.Path(tempfile.gettempdir()).glob("tmp*"):
        if (path / "acme-thing").exists() or (path / "renamed-maintenance").exists():
            shutil.rmtree(path, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
