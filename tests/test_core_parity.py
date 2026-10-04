#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""lint.py against Celerp's own manifest checks, over one shared set of manifests.

lint.py copies the rules the loader and the importer enforce, because it runs
without Celerp installed. This test runs where Celerp is importable and holds
the copy to the original: every manifest below must be accepted by both or
refused by both. Celerp 2.5.3 and earlier do not have these checks, so against
them the test is skipped.

table_prefix is compared only on the rules that need nothing but the manifest.
Celerp also refuses a prefix that one of its own tables or another installed
module's prefix starts with; the prefixes below start with none, and no other
module is installed.
"""
from __future__ import annotations

import importlib.util
import itertools
import os
import pathlib
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("celerp_module_lint", ROOT / "lint.py")
lint = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lint)

try:
    from celerp.modules import importer, loader
except ImportError:
    importer = loader = None

SLOT_VALIDATORS = getattr(loader, "_LINK_SLOT_VALIDATORS", None)
PREFIX_PROBLEM = getattr(importer, "table_prefix_problem", None)

# Every shape a literal can take, as in test_lint's crash sweep.
SHAPES = ({}, [], [{}], [[]], [1, None], 1, 1.5, None, True, "x", {1: "x", "y": []}, ("x",))
HREFS = (
    "/q", "/q/{entity_id}", "/q?l={price_list}&f={field_name}&e={entity_id}", "/q/{entity_id}/{entity_id}",
    "", "q", "//evil.example", "/\\evil.example", "/a\x01", "/a\x7f", "/a\n", "https://x.example",
    "javascript:alert(1)", "/a/{x}", "/a/{", "/a/}", "/a/{{entity_id}}", "/a/{}", "/a/{ entity_id }",
    "/a/{entity_id}{field_name}", "/a/{entity_id", "/a/entity_id}", "/café/{entity_id}",
) + SHAPES
SHOW_ON = (
    [], ["sell"], ["cost"], ["sell", "cost"], ["editable", "readonly"], ["manual", "derived"],
    ["editable", "sell", "manual"], ["readonly", "cost", "derived"], ["sell", "sell"], ["bogus"],
    ["Sell"], "sell", ("sell",), {"sell": 1}, [1], [None], [{}], [[]], [True], [b"sell"],
) + SHAPES
PRESENTATION = ("page", "modal", "Page", "") + SHAPES


def _entries(slot: str) -> list:
    """item_action / pricing_action contributions: valid and broken, one rule at a time."""
    placeholder = "{entity_id}"
    base = {"href_template": f"/q/{placeholder}", "label": "Open"}
    entries: list = [dict(base, href_template=href) for href in HREFS]
    entries += [{key: value for key, value in base.items() if key != "href_template"}]
    entries += [dict(base, **{key: shape}) for key in ("label", "label_key", "permission")
                for shape in SHAPES]
    entries += [dict(base, **{key: "x"}) for key in ("extra", "show_on", "presentation")]
    entries += [{**base, 1: "x", "y": "z"}, {**base, (1, 2): "x"}]
    if slot == "pricing_action":
        entries += [dict(base, show_on=show_on) for show_on in SHOW_ON]
        entries += [dict(base, presentation=p) for p in PRESENTATION]
        entries += [dict(base, show_on=s, presentation=p)
                    for s, p in itertools.product((["sell"], ["sell", "cost"]), ("page", "modal"))]
    contributions: list = list(entries)                       # a single dict entry
    contributions += [[entry] for entry in entries]           # a one-entry list
    contributions += [[base, entry] for entry in entries]     # a broken entry after a good one
    contributions += list(SHAPES) + [[base, base], [base, 1], [base, None]]
    return contributions


def _lint_refuses(slot: str, contribution) -> bool:
    check = {"item_action": lint._item_action_problems,
             "pricing_action": lint._pricing_action_problems}[slot]
    return bool(check({"slots": {slot: contribution}}))


def _core_refuses(slot: str, contribution) -> bool:
    """The loader skips a slot given as None and validates anything else. Any
    exception, not only ModuleLoadError, means the module does not load."""
    if contribution is None:
        return False
    try:
        SLOT_VALIDATORS[slot](contribution)
    except Exception:
        return True
    return False


@unittest.skipIf(SLOT_VALIDATORS is None, "needs Celerp 2.5.4 or later importable")
class TestLinkSlotParity(unittest.TestCase):
    def test_lint_checks_the_slots_celerp_checks(self):
        self.assertEqual(set(SLOT_VALIDATORS), {"item_action", "pricing_action"})

    def test_lint_and_celerp_agree_on_every_entry(self):
        for slot in sorted(SLOT_VALIDATORS):
            contributions = _entries(slot)
            with self.subTest(slot=slot):
                disagree = [(c, _lint_refuses(slot, c), _core_refuses(slot, c))
                            for c in contributions
                            if _lint_refuses(slot, c) != _core_refuses(slot, c)]
                self.assertEqual(disagree, [], f"{len(disagree)} of {len(contributions)} "
                                 "(contribution, lint refuses, celerp refuses)")
                refused = sum(_core_refuses(slot, c) for c in contributions)
                self.assertTrue(0 < refused < len(contributions))

    def test_shared_constants_match(self):
        self.assertEqual(lint.PRICING_ACTION_KEYS, set(loader._PRICING_ACTION_KEYS))
        self.assertEqual(lint.PRICING_ACTION_PLACEHOLDERS, set(loader._PRICING_ACTION_PLACEHOLDERS))
        self.assertEqual(lint.ITEM_ACTION_PLACEHOLDERS, set(loader._ITEM_ACTION_PLACEHOLDERS))
        self.assertEqual(lint.PRICING_ROW_TRAIT_PAIRS, loader._PRICING_ROW_TRAIT_PAIRS)
        self.assertEqual(lint.PLACEHOLDER_RE.pattern, loader._PLACEHOLDER_RE.pattern)
        self.assertEqual(lint.PROTECTED, set(loader._PROTECTED_BSL_INTERNALS))


PREFIXES = (
    "acme_", "acme_x_", "abc_", "acme__", "ACME_", "zq9_", "___", "acmé_",
    "", "_", "__", "a_", "ab", "abc", "acme", "acme-", "acme_ ", " acme",
    None, 1, 1.5, True, ["acme_"], {"acme_": 1}, ("acme_",),
)
ABSENT = object()
MIGRATIONS = (ABSENT, "thing.migrations", "", None)
MANIFEST = """PLUGIN_MANIFEST = {
    "name": "acme-thing", "version": "0.1.0", "display_name": "Thing", "license": "MIT",
    "ui_routes": "thing.ui_routes",
    %s
}
"""


def _prefix_manifests() -> list[dict]:
    manifests = []
    for prefix, migrations in itertools.product((ABSENT,) + PREFIXES, MIGRATIONS):
        manifest = {}
        if prefix is not ABSENT:
            manifest["table_prefix"] = prefix
        if migrations is not ABSENT:
            manifest["migrations"] = migrations
        manifests.append(manifest)
    return manifests


@unittest.skipIf(PREFIX_PROBLEM is None, "needs Celerp 2.5.4 or later importable")
class TestTablePrefixParity(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def _lint_refuses(self, manifest: dict) -> bool:
        folder = pathlib.Path(tempfile.mkdtemp(dir=self.tmp.name)) / "acme-thing"
        folder.mkdir()
        extra = "".join(f"{key!r}: {value!r}, " for key, value in manifest.items())
        (folder / "__init__.py").write_text(MANIFEST % extra, encoding="utf-8")
        return bool(lint.lint(folder))

    def _core_refuses(self, manifest: dict) -> bool:
        empty = pathlib.Path(self.tmp.name) / "modules"
        empty.mkdir(exist_ok=True)
        with mock.patch.dict(os.environ, {"MODULE_DIR": str(empty)}):
            try:
                importer._validate_table_prefix("acme-thing", manifest)
            except importer.ModuleImportError:
                return True
        return False

    def test_lint_and_celerp_agree_on_every_prefix(self):
        manifests = _prefix_manifests()
        disagree = [(m, self._lint_refuses(m), self._core_refuses(m)) for m in manifests
                    if self._lint_refuses(m) != self._core_refuses(m)]
        self.assertEqual(disagree, [], f"{len(disagree)} of {len(manifests)} "
                         "(manifest, lint refuses, celerp refuses)")
        refused = sum(self._core_refuses(m) for m in manifests)
        self.assertTrue(0 < refused < len(manifests))

    def test_shared_constants_match(self):
        self.assertEqual(lint.MIN_TABLE_PREFIX_LEN, importer.MIN_TABLE_PREFIX_LEN)


if __name__ == "__main__":
    unittest.main()
