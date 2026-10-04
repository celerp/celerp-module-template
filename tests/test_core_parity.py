#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""lint.py against Celerp itself, rule by rule, over one shared set of modules.

lint.py copies the rules Celerp enforces when it loads and installs a module,
because it runs without Celerp installed. This test runs where Celerp 2.5.4 is
importable and holds the copy to the original. Every case is a real module folder
on disk. Celerp takes it through the path it boots with: admission before any of
the module's code or migrations run (identity, route sources, the migrations
package, protected imports), then load_all (import, every slot rule, callable
resolution and provenance), then API and UI route registration (the setup
functions' provenance), or, for table_prefix, through its install check. lint.py
checks the same folder.
For every rule, both must accept the same modules and refuse the same modules,
and each rule's cases include both kinds.

lint.py's "ignored" findings (manifest parts Celerp reads straight past) are not
refusals, so only its problems are compared.

Set CELERP_PARITY_REQUIRED=1 to fail instead of skip when Celerp is missing; CI
does, so a checkout without Celerp 2.5.4 can never pass by skipping.
"""
from __future__ import annotations

import importlib.util
import itertools
import os
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("celerp_module_lint", ROOT / "lint.py")
lint = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lint)

try:
    from celerp.modules import importer, loader, slots
    from celerp.services import permissions
    from fastapi import FastAPI
    CORE = hasattr(loader, "admit_modules") and hasattr(importer, "reserved_tables")
except ImportError:
    CORE = False
if not CORE and os.environ.get("CELERP_PARITY_REQUIRED"):
    raise RuntimeError("CELERP_PARITY_REQUIRED is set but Celerp 2.5.4 is not importable")

ABSENT = object()
# Every shape a manifest literal can take.
SHAPES = ({}, [], [{}], [[]], [1, None], 1, 1.5, None, True, "x", {1: "x", "y": []}, ("x",))
PATHS = (
    "/q", "/q/a?b=c", "/café", "", "q", "//evil.example", "/\\evil.example", "/a\x01", "/a\x7f",
    "/a\n", "https://x.example", "javascript:alert(1)",
) + SHAPES
ROUTES = {"{pkg}/__init__.py": "",
          "{pkg}/ui_routes.py": "def setup_ui_routes(app):\n    pass\n"}
HOOKS = (
    "def sync_fn(*args, **kwargs):\n    return None\n\n\n"
    "async def async_fn(*args, **kwargs):\n    return None\n\n\n"
    "class Klass:\n    pass\n\n\n"
    "lam = lambda *args, **kwargs: None\n"
    "VALUE = 5\n"
)


class Case:
    """One module: a slots manifest, extra manifest fields and source files. Strings
    in all three may say {pkg}, the module's inner package name. `links` maps a path
    in the module to a link to a file (text) or folder (dict of files) outside it;
    `name` replaces the generated module name, folder included."""

    def __init__(self, slots_value=ABSENT, files=None, links=None, name=None, **fields):
        self.slots_value, self.files, self.fields = slots_value, files or {}, fields
        self.links, self.name = links or {}, name

    def __repr__(self):
        return (f"Case(slots={self.slots_value!r}, files={sorted(self.files)}, "
                f"links={sorted(self.links)}, name={self.name!r}, {self.fields!r})")


def _fill(value, pkg: str):
    """value with {pkg} filled in every string, keys included."""
    if isinstance(value, str):
        return value.replace("{pkg}", pkg)
    if isinstance(value, dict):
        return {_fill(k, pkg): _fill(v, pkg) for k, v in value.items()}
    if isinstance(value, list):
        return [_fill(v, pkg) for v in value]
    if isinstance(value, tuple):
        return tuple(_fill(v, pkg) for v in value)
    return value


def _base(slot: str) -> dict:
    """An entry Celerp accepts for `slot`."""
    entry = {
        "nav": {"key": "k", "label": "L", "href": "/x"},
        "bulk_action": {"label": "L", "form_action": "/x"},
        "item_action": {"label": "L", "href_template": "/q/{entity_id}"},
        "pricing_action": {"label": "L", "href_template": "/q/{entity_id}"},
        "category_schema": {"category": "c", "fields": []},
        "projection_handler": {"prefix": "acme."},
        "search_provider": {"result_key": "items", "permission": "view_inventory"},
    }.get(slot, {"label": "L"})
    if slot in lint.CALLABLE_SLOTS:
        key, awaited = lint.CALLABLE_SLOTS[slot]
        entry = {**entry, key: "{pkg}.hooks:" + ("async_fn" if awaited else "sync_fn")}
    return entry


def _slot_case(slot: str, entry, files=None, links=None) -> Case:
    """`entry` in `slot`, shaped as that slot takes it (search_provider: one dict)."""
    return Case({slot: entry if slot == "search_provider" else [entry]},
                {"{pkg}/__init__.py": "", "{pkg}/hooks.py": HOOKS, **(files or {})}, links)


@unittest.skipUnless(CORE, "needs Celerp 2.5.4 or later importable")
class TestCoreParity(unittest.TestCase):
    counter = itertools.count()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self._clean_core)
        self._clean_core()

    def _clean_core(self):
        slots.clear()
        loader._loaded.clear()
        loader._load_errors.clear()
        loader._admitted.clear()

    def _write(self, case: Case, flat: bool) -> tuple[pathlib.Path, str]:
        n = next(self.counter)
        name = case.name or (f"acmeflat{n}" if flat else f"acme-p{n}")
        pkg = name if flat else f"acme_p{n}"
        folder = pathlib.Path(self.tmp.name) / f"case{n}" / name
        folder.mkdir(parents=True)
        outside = folder.parent / "outside"

        def place(rel):
            target = folder / _fill(rel, pkg)
            if flat and target.parent.name == pkg:
                target = folder / target.name
            target.parent.mkdir(parents=True, exist_ok=True)
            return target

        for rel, text in case.files.items():
            place(rel).write_text(_fill(text, pkg), encoding="utf-8")
        for index, (rel, content) in enumerate(case.links.items()):
            real = outside / str(index)
            for sub, text in (content if isinstance(content, dict) else {"": content}).items():
                file = real / sub if sub else real.with_suffix(".py")
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_text(_fill(text, pkg), encoding="utf-8")
            place(rel).symlink_to(real if isinstance(content, dict) else real.with_suffix(".py"))
        manifest = {"name": name, "version": "0.1.0", "display_name": "Thing", "license": "MIT"}
        manifest.update(_fill(case.fields, pkg))
        if case.slots_value is not ABSENT:
            manifest["slots"] = _fill(case.slots_value, pkg)
        (folder / "__init__.py").write_text(f"PLUGIN_MANIFEST = {manifest!r}\n", encoding="utf-8")
        return folder, pkg

    def _core_refuses(self, folder: pathlib.Path, pkg: str) -> bool:
        path_before, modules_before = list(sys.path), set(sys.modules)
        try:
            with mock.patch.object(loader, "_first_party_lock", lambda: {}):
                try:
                    # The order main.py boots in: admit (migrations run only for an
                    # admitted module), load, then each process's routes.
                    admission = loader.admit_modules(folder.parent, {folder.name})
                    loaded = loader.load_all(folder.parent, {folder.name}, admission=admission)
                    loader.register_api_routes(FastAPI(), loaded)
                    loader.register_ui_routes(FastAPI(), loaded)
                except Exception:
                    return True  # the load pass itself failed: the module did not load
            return folder.name in loader.load_errors()
        finally:
            sys.path[:] = path_before
            for key in set(sys.modules) - modules_before:
                origin = getattr(sys.modules[key], "__file__", None) or ""
                if key.startswith((pkg, folder.name)) or origin.startswith(self.tmp.name):
                    sys.modules.pop(key, None)
            self._clean_core()

    def _disagreements(self, cases, flat: bool = False) -> tuple[list, int]:
        disagree, refused = [], 0
        for case in cases:
            folder, pkg = self._write(case, flat)
            lint_problems = lint.check(folder)[0]
            core = self._core_refuses(folder, pkg)
            refused += core
            if bool(lint_problems) != core:
                disagree.append((case, lint_problems, "celerp refuses" if core else "celerp loads"))
        return disagree, refused

    def assertParity(self, cases, flat: bool = False):
        cases = list(cases)
        disagree, refused = self._disagreements(cases, flat)
        self.assertEqual(disagree, [], f"{len(disagree)} of {len(cases)} disagree")
        self.assertTrue(0 < refused < len(cases),
                        f"Celerp refused {refused} of {len(cases)}; a rule needs both kinds")

    # ── the rules, one test each ──────────────────────────────────────────────

    def test_every_slot_accepts_its_base_entry(self):
        """Any module may fill any slot Celerp reads (there is no internal list)."""
        cases = [_slot_case(slot, _base(slot)) for slot in sorted(lint.SLOT_NAMES)]
        cases.append(_slot_case("nav", {"href": "//off.example"}))
        self.assertParity(cases)

    def test_slots_value(self):
        self.assertParity(Case(value) for value in SHAPES + ({"nav": [_base("nav")]},))

    def test_entry_shape(self):
        cases = []
        for slot in sorted(lint.SLOT_NAMES - {"search_provider"}):
            base = _base(slot)
            for contribution in SHAPES + ([base], base, [base, base], [base, 1], [base, None]):
                cases.append(Case({slot: contribution}, {"{pkg}/hooks.py": HOOKS}))
        self.assertParity(cases)

    def test_slot_permission(self):
        self._permission_sweep("permission")

    def test_slot_write_permission(self):
        self._permission_sweep("write_permission")

    def _permission_sweep(self, key: str):
        values = ("view_inventory", "manage_labels", "", None, 0, False, True, [], {}, 1.5,
                  ["view_inventory"], "not_a_permission", " view_inventory", "VIEW_INVENTORY")
        self.assertParity(_slot_case(slot, {**_base(slot), key: value})
                          for slot in sorted(lint.SLOT_NAMES) for value in values)

    def test_requires_connector(self):
        values = (None, "", False, 0, [], {}, "shopify", " ", 1, True, 1.5, ["shopify"], {"a": 1})
        self.assertParity(_slot_case(slot, {**_base(slot), "requires_connector": value})
                          for slot in sorted(lint.SLOT_NAMES - {"search_provider"})
                          for value in values)

    def test_app_local_destinations(self):
        cases = []
        for slot, keys in lint.DESTINATION_KEYS.items():
            for key in keys:
                cases += [_slot_case(slot, {**_base(slot), key: path}) for path in PATHS]
                cases.append(_slot_case(slot, {k: v for k, v in _base(slot).items() if k != key}))
        self.assertParity(cases)

    def test_link_templates(self):
        """item_action and pricing_action href_template, and pricing_action's own keys."""
        hrefs = PATHS + (
            "/q/{entity_id}", "/q?l={price_list}&f={field_name}&e={entity_id}", "/a/{x}", "/a/{",
            "/a/}", "/a/{{entity_id}}", "/a/{}", "/a/{ entity_id }", "/a/{entity_id",
            "/a/entity_id}", "/a/{entity_id}{field_name}", "/q/{entity_id}/{entity_id}",
        )
        show_on = ([], ["sell"], ["sell", "cost"], ["editable", "readonly"], ["manual", "derived"],
                   ["editable", "sell", "manual"], ["sell", "sell"], ["bogus"], ["Sell"], "sell",
                   [1], [None], [[]], [{}]) + SHAPES
        cases = []
        for slot in ("item_action", "pricing_action"):
            base = _base(slot)
            cases += [_slot_case(slot, {**base, "href_template": h}) for h in hrefs]
            cases.append(_slot_case(slot, {"label": "L"}))
            cases += [_slot_case(slot, {**base, key: "x"})
                      for key in ("extra", "icon", "show_on", "presentation", "label_key",
                                  "requires_connector")]
            cases += [_slot_case(slot, {**base, 1: "x"})]
        base = _base("pricing_action")
        cases += [_slot_case("pricing_action", {**base, "show_on": s}) for s in show_on]
        cases += [_slot_case("pricing_action", {**base, "presentation": p})
                  for p in ("page", "modal", "Page", "") + SHAPES]
        self.assertParity(cases)

    def test_projection_handler_prefix(self):
        base = _base("projection_handler")
        values = (ABSENT, "acme.", "a", "", None, 0, 1, True, ["acme."], {"acme.": 1})
        self.assertParity(
            _slot_case("projection_handler",
                       {k: v for k, v in base.items() if k != "prefix"} if value is ABSENT
                       else {**base, "prefix": value})
            for value in values)

    def test_search_provider_descriptor(self):
        good = _base("search_provider")
        variants = [good, {**good, "extra": 1}, {**good, 1: "x"}, [good]] + list(SHAPES)
        variants += [{k: v for k, v in good.items() if k != key} for key in good]
        variants += [{**good, "result_key": value} for value in ("entries", "widgets", "") + SHAPES]
        variants += [{**good, "requires_connector": "shopify"}]
        self.assertParity(Case({"search_provider": v}, {"{pkg}/hooks.py": HOOKS}) for v in variants)

    def test_callable_ownership_and_provenance(self):
        """Who owns the code a callable slot names: shape, in-module source, protected
        imports (also through a local import), core and stdlib decoys, re-exports."""
        files = {
            "{pkg}/hooks.py": HOOKS,
            "{pkg}/reexport.py": "from .hooks import sync_fn, async_fn\n",
            "{pkg}/reexport_abs.py": "from {pkg}.hooks import sync_fn, async_fn\n",
            "{pkg}/foreign.py": ("from celerp.services.app_paths import is_app_local_path as sync_fn\n"
                                 "from celerp.modules.slots import fire_lifecycle as async_fn\n"),
            "{pkg}/stdlib.py": "from os.path import join as sync_fn\nfrom asyncio import sleep as async_fn\n",
            "{pkg}/broken.py": "def sync_fn(:\n",
        }
        # Decoys at the folder root, where Celerp's import path puts them first: a
        # name Python or Celerp has already imported still resolves to the original.
        decoy = {"celerp/services/app_paths.py": "def is_app_local_path(*a):\n    return True\n",
                 "celerp/__init__.py": "", "celerp/services/__init__.py": "",
                 "json.py": "def dumps(*a):\n    return ''\n\n\nasync def loads(*a):\n    return ''\n"}
        protected = {"{pkg}/bad.py": "import celerp.ai.service\n" + HOOKS}
        transitive = {"{pkg}/via.py": "from . import helper\n" + HOOKS,
                      "{pkg}/helper.py": "from celerp.gateway import client\n"}
        cases = []
        for slot in sorted(lint.CALLABLE_SLOTS):
            key, awaited = lint.CALLABLE_SLOTS[slot]
            fn = "async_fn" if awaited else "sync_fn"
            base = _base(slot)

            def case(dotted, extra=None):
                return _slot_case(slot, {**base, key: dotted}, {**files, **(extra or {})})

            cases += [case(d) for d in (
                f"{{pkg}}.hooks:{fn}", f"{{pkg}}.reexport:{fn}", f"{{pkg}}.reexport_abs:{fn}",
                f"{{pkg}}.foreign:{fn}", f"{{pkg}}.stdlib:{fn}", f"{{pkg}}.broken:{fn}",
                f"{{pkg}}.hooks:missing", f"{{pkg}}.nope:{fn}", f"{{pkg}}.hooks:VALUE",
                f"{{pkg}}.hooks.{fn}", f"{{pkg}}.hooks:{fn}:x", f":{fn}", "{pkg}.hooks:",
                "", None, 5, [f"{{pkg}}.hooks:{fn}"], "celerp.services.app_paths:is_app_local_path",
                "celerp.modules.slots:fire_lifecycle",
            )]
            cases.append(case("celerp.services.app_paths:is_app_local_path", decoy))
            cases.append(case("json:" + ("loads" if awaited else "dumps"), decoy))
            cases.append(case(f"{{pkg}}.bad:{fn}", protected))
            cases.append(case(f"{{pkg}}.sub:{fn}",
                              {"{pkg}/sub.py": "from celerp.ai import quota\n" + HOOKS}))
            cases.append(case(f"{{pkg}}.dyn:{fn}",
                              {"{pkg}/dyn.py": "import importlib\nimportlib.import_module('celerp.gateway')\n" + HOOKS}))
            cases.append(_slot_case(slot, {**base, key: f"{{pkg}}.linked:{fn}"}, files,
                                    links={"{pkg}/linked.py": HOOKS}))
            cases.append(case(f"{{pkg}}.via:{fn}", transitive))
            cases.append(case(f"{{pkg}}:{fn}", {"{pkg}/__init__.py": f"from .hooks import {fn}\n"}))
        self.assertParity(cases)

    def test_module_name(self):
        """The name Celerp admits a module under: its characters, its length, and the
        reserved celerp- prefix."""
        names = ("acme-x", "acme_x", "Acme9", "9acme", "a" * 64, "a" * 65, "-acme", "_acme",
                 "acme.x", "acme x", "acmé", "celerp-x")
        self.assertParity(Case({"nav": [_base("nav")]}, name=name) for name in names)

    def test_route_entrypoints(self):
        """api_routes and ui_routes: a file inside the module that defines setup or
        imports it from the module's own code (checked before any code runs), and a
        setup that is the module's own plain function (checked before it is called)."""
        files = {
            "{pkg}/__init__.py": "",
            "{pkg}/api.py": "def setup_api_routes(app):\n    pass\n",
            "{pkg}/ui.py": "def setup_ui_routes(app):\n    pass\n",
            "{pkg}/both.py": "from .impl import setup_api_routes, setup_ui_routes\n",
            "{pkg}/impl.py": ("def setup_api_routes(app):\n    pass\n\n\n"
                              "def setup_ui_routes(app):\n    pass\n"),
            "{pkg}/absolute.py": "from {pkg}.impl import setup_api_routes, setup_ui_routes\n",
            "{pkg}/is_async.py": ("async def setup_api_routes(app):\n    pass\n\n\n"
                                  "async def setup_ui_routes(app):\n    pass\n"),
            "{pkg}/none.py": "def other(app):\n    pass\n",
            "{pkg}/foreign.py": ("from os.path import join as setup_api_routes\n"
                                 "from os.path import join as setup_ui_routes\n"),
            "{pkg}/klass.py": "class setup_api_routes:\n    pass\n\n\nclass setup_ui_routes:\n    pass\n",
            "{pkg}/broken.py": "def setup_api_routes(:\n",
            "{pkg}/via.py": "from .impl_bad import setup_api_routes, setup_ui_routes\n",
            "{pkg}/impl_bad.py": ("from os.path import join as setup_api_routes\n"
                                  "from os.path import join as setup_ui_routes\n"),
            "json.py": "def setup_api_routes(app):\n    pass\n\n\ndef setup_ui_routes(app):\n    pass\n",
        }
        links = {"{pkg}/linked.py": files["{pkg}/impl.py"]}
        cases = []
        for key in ("api_routes", "ui_routes"):
            for module in ("{pkg}.api", "{pkg}.ui", "{pkg}.both", "{pkg}.absolute", "{pkg}.is_async",
                           "{pkg}.none", "{pkg}.foreign", "{pkg}.klass", "{pkg}.broken",
                           "{pkg}.via", "{pkg}.nope", "{pkg}.linked", "json",
                           "ui.routes.reports", "celerp.main", "{pkg}..api"):
                cases.append(Case(files=files, links=links, **{key: module}))
            protected = {"{pkg}/protected.py": "import celerp.ai.service\n" + files["{pkg}/impl.py"]}
            cases.append(Case(files={**files, **protected}, **{key: "{pkg}.protected"}))
        self.assertParity(cases)
        self.assertParity([Case(files=files, api_routes="{pkg}.impl", ui_routes="{pkg}.impl"),
                           Case(files=files, api_routes="{pkg}.impl", ui_routes="{pkg}.is_async")],
                          flat=True)

    def test_migrations_package(self):
        """The migrations package admission resolves before any migration runs: a
        dotted path of identifiers whose folder, and every file in it not starting
        with _, stay inside the module."""
        migration = "def upgrade():\n    pass\n"
        files = {**ROUTES, "{pkg}/migrations/__init__.py": "", "{pkg}/migrations/m001.py": migration}
        cases = [Case(files=files, ui_routes="{pkg}.ui_routes", table_prefix="acme_", migrations=m)
                 for m in ("{pkg}.migrations", "{pkg}.absent", "../outside", "/tmp", "{pkg}/migrations",
                           "{pkg}..migrations", "{pkg}.migrations.", "1x.migrations", "{pkg}.mig-rations",
                           "{pkg}")]
        for package, link in (("{pkg}.linked", {"{pkg}/linked": {"m001.py": migration}}),
                              ("{pkg}.migrations", {"{pkg}/migrations/m002.py": migration}),
                              ("{pkg}.migrations", {"{pkg}/migrations/_helper.py": migration})):
            cases.append(Case(files=files, links=link, ui_routes="{pkg}.ui_routes",
                              table_prefix="acme_", migrations=package))
        self.assertParity(cases)

    def test_where_lint_is_stricter(self):
        """What lint.py refuses although Celerp may load it. Callables only running the
        code could follow (a decorated function, a name a star import brings in, a
        value built by a call), a package named like a Python module Celerp has not
        imported yet, and a protected import in a file nothing imports. lint.py
        refuses every one; Celerp loads some."""
        wrap = ("import functools\n\n\ndef wrap(f):\n    @functools.wraps(f)\n"
                "    def inner(*a, **k):\n        return f(*a, **k)\n    return inner\n\n\n")
        cases = []
        for slot in sorted(lint.CALLABLE_SLOTS):
            key, awaited = lint.CALLABLE_SLOTS[slot]
            fn = "async_fn" if awaited else "sync_fn"
            for module, text in (
                    ("deco", wrap + f"@wrap\n{'async ' if awaited else ''}def {fn}(*a, **k):\n    return None\n"),
                    ("star", "from .hooks import *\n"),
                    ("built", f"import functools\nfrom .hooks import {fn} as _f\n{fn} = functools.partial(_f)\n")):
                cases.append(_slot_case(slot, {**_base(slot), key: f"{{pkg}}.{module}:{fn}"},
                                        {f"{{pkg}}/{module}.py": text}))
            cases.append(_slot_case(slot, {**_base(slot), key: f"tabnanny:{fn}"},
                                    {"tabnanny.py": HOOKS}))
            cases.append(_slot_case(slot, _base(slot),
                                    {"{pkg}/unused.py": "import celerp.gateway\n"}))
        loaded = 0
        for case in cases:
            folder, pkg = self._write(case, flat=False)
            self.assertTrue(lint.check(folder)[0], case)
            loaded += not self._core_refuses(folder, pkg)
        self.assertGreater(loaded, 0)

    def test_callable_flat_layout(self):
        """A module whose folder is itself the package names its callables from it."""
        cases = []
        for slot in sorted(lint.CALLABLE_SLOTS):
            key, awaited = lint.CALLABLE_SLOTS[slot]
            for fn in ("sync_fn", "async_fn"):
                cases.append(_slot_case(slot, {**_base(slot), key: f"{{pkg}}.hooks:{fn}"}))
        self.assertParity(cases, flat=True)

    def test_callable_sync_or_async(self):
        """Async exactly where Celerp awaits the call."""
        self.assertParity(
            _slot_case(slot, {**_base(slot), lint.CALLABLE_SLOTS[slot][0]: f"{{pkg}}.hooks:{name}"})
            for slot in sorted(lint.CALLABLE_SLOTS)
            for name in ("sync_fn", "async_fn", "Klass", "lam"))

    def test_table_prefix(self):
        """table_prefix at install: shape, migrations, and every table Celerp reserves,
        including the tables of first-party modules that are not turned on."""
        prefixes = (
            "acme_", "acme_x_", "abc_", "acme__", "ACME_", "zq9_", "___", "acmé_",
            "", "_", "__", "a_", "ab", "abc", "acme", "acme-", "acme_ ", " acme",
            None, 1, 1.5, True, ["acme_"], {"acme_": 1}, ("acme_",),
            "label_", "marketplace_", "bank_", "alembic_", "instance_", "user_", "ai_", "sync_",
            "labels_", "bank_x_", "accounts_",
        )
        disagree, refused, total = [], 0, 0
        modules = pathlib.Path(self.tmp.name) / "modules"
        modules.mkdir()
        for prefix, migrations in itertools.product((ABSENT,) + prefixes,
                                                   (ABSENT, "thing.migrations", "", None)):
            manifest = {}
            if prefix is not ABSENT:
                manifest["table_prefix"] = prefix
            if migrations is not ABSENT:
                manifest["migrations"] = migrations
            folder, _ = self._write(Case(files=ROUTES, ui_routes="{pkg}.ui_routes", **manifest),
                                    flat=False)
            lint_refuses = bool(lint.check(folder)[0])
            with mock.patch.dict(os.environ, {"MODULE_DIR": str(modules)}):
                try:
                    importer._validate_table_prefix(folder.name, manifest)
                    core = False
                except importer.ModuleImportError:
                    core = True
            total += 1
            refused += core
            if lint_refuses != core:
                disagree.append((manifest, lint_refuses, core))
        self.assertEqual(disagree, [], "(manifest, lint refuses, celerp refuses)")
        self.assertTrue(0 < refused < total)

    def test_shared_constants_match(self):
        self.assertEqual(lint.PERMISSION_KEYS, set(permissions._PERMISSIONS_BY_KEY))
        self.assertEqual(lint.PERMISSION_ENTRY_KEYS, loader._PERMISSION_ENTRY_KEYS)
        self.assertEqual(lint.DESTINATION_KEYS, loader._DESTINATION_KEYS)
        self.assertEqual(lint.CALLABLE_SLOTS, loader._CALLABLE_SLOTS)
        self.assertEqual(lint.RESERVED_TABLES, importer.reserved_tables("acme-thing"))
        self.assertEqual(lint.MIN_TABLE_PREFIX_LEN, importer.MIN_TABLE_PREFIX_LEN)
        self.assertEqual(lint.NAME_MAX, importer._NAME_MAX)
        self.assertEqual(lint.PRICING_ACTION_KEYS, set(loader._PRICING_ACTION_KEYS))
        self.assertEqual(lint.PRICING_ACTION_PLACEHOLDERS, set(loader._PRICING_ACTION_PLACEHOLDERS))
        self.assertEqual(lint.ITEM_ACTION_PLACEHOLDERS, set(loader._ITEM_ACTION_PLACEHOLDERS))
        self.assertEqual(lint.PRICING_ROW_TRAIT_PAIRS, loader._PRICING_ROW_TRAIT_PAIRS)
        self.assertEqual(lint.PLACEHOLDER_RE.pattern, loader._PLACEHOLDER_RE.pattern)
        self.assertEqual(lint.PROTECTED, set(loader._PROTECTED_BSL_INTERNALS))
        self.assertEqual(lint.SEARCH_PROVIDER_KEYS, set(loader._SEARCH_PROVIDER_KEYS))
        self.assertEqual(lint.SEARCH_PROVIDER_RESULT_KEYS, set(loader._SEARCH_RESULT_KEYS))
        # Every slot a core rule names is a slot lint knows.
        named = set(loader._CALLABLE_SLOTS) | set(loader._DESTINATION_KEYS) | set(loader._SLOT_VALIDATORS)
        self.assertLessEqual(named, lint.SLOT_NAMES)


if __name__ == "__main__":
    unittest.main()
