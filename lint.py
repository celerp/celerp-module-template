#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Lint a Celerp module without installing the app.

Runs the same structural checks the loader runs at startup, so you catch
problems in seconds instead of on a failed boot:
  - the folder has an __init__.py with a PLUGIN_MANIFEST
  - the manifest has the required identity fields and at least one slot/route
  - the module name is not in the reserved `celerp-` namespace
  - every slot entry follows the rules the Celerp 2.5.4 loader enforces before it
    loads a module: entries are dicts; a permission or write_permission names a
    key from Celerp's permission registry; a requires_connector is a connector id;
    nav href and settings_href and bulk_action form_action (required) are paths
    inside Celerp; a callable slot names a function in the module's own files,
    async exactly where Celerp awaits it; a projection_handler names its prefix
  - search_provider is one descriptor with exactly handler, result_key and permission
  - pricing_action entries have the shape the loader accepts: known keys only,
    a link that stays inside Celerp, and braces only around a placeholder
  - item_action links stay inside Celerp, with braces only around {entity_id}
  - every top-level manifest field holds the one type Celerp reads it as
  - table_prefix has the shape Celerp requires, claims no table Celerp reserves,
    and a module with migrations sets one
  - no source file imports a protected celerp internal (revenue-gated; the
    loader rejects modules that do)
  - no fragment is rendered with str(); FT.__str__ returns the element id, so
    that sends the browser a word where its markup should be

Findings come in two kinds. A problem is something Celerp refuses, or breaks on,
or a rule this repo holds modules to. An ignored finding is a part of the manifest
Celerp reads straight past (an unknown key or slot name), which is usually a typo.

Usage:  python lint.py path/to/your-module-folder
Exit 0 = clean, 1 = findings (printed).
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

# Kept in sync with celerp/modules/loader.py _PROTECTED_BSL_INTERNALS.
PROTECTED = {
    "celerp.session_gate", "celerp.ai.service", "celerp.ai.quota",
    "celerp.gateway", "celerp.connectors",
}
REQUIRED_FIELDS = ("name", "version", "display_name", "license")
# Every top-level field Celerp reads out of a manifest, and the one type it reads
# it as (celerp/modules/loader.py, importer.py, migrations_runner.py). Any other
# type breaks in core rather than being refused: a depends_on string is iterated
# letter by letter, a migrations number has no .split(). None is the same as
# leaving the field out, which is how Celerp reads it, except for two fields:
# Celerp checks a declared table_prefix whatever its value, and its 2.5.4 loader
# breaks on a slots value that is empty but not a dict (None, [], "", 0).
# depends_on is a list of strings.
MANIFEST_FIELD_TYPES = {
    **dict.fromkeys(("name", "version", "display_name", "label", "description", "license",
                     "author", "min_celerp_version", "api_routes", "ui_routes", "migrations",
                     "table_prefix"), str),
    **dict.fromkeys(("slots", "company_backup", "locales"), dict),
    "depends_on": list,
}
# An unknown key is not an error to the loader, it is simply ignored, which is why
# it has to be an error here: a module whose gating key is misspelled ships wide
# open and nothing says a word.
MANIFEST_KEYS = set(MANIFEST_FIELD_TYPES)
TYPE_NAMES = {str: "a string", dict: "a dict", list: "a list of strings"}
# Every slot core consumes (celerp/modules/slots.py). Any module may fill any of
# them. A slot core does not read is still checked by the generic entry rules
# at load time, then ignored, so a misspelled slot name ships an entry that never
# appears.
SLOT_NAMES = {
    "nav", "search_provider", "bulk_action", "item_action", "doc_detail_actions",
    "doc_detail_badges", "category_schema", "on_company_created", "on_modules_ready",
    "send_to_targets", "catalog_channel", "projection_handler", "pricing_action",
    "doc_finalize_hook", "on_doc_payment",
}
# How each table a module owns travels with a company backup.
COMPANY_BACKUP_VALUES = {"include", "exclude"}
# The keys a nav slot entry may carry (ui/components/shell.py builds the sidebar).
NAV_ITEM_KEYS = {
    "group", "key", "href", "label", "label_key", "order", "settings_href",
    "permission",
}
# The keys a search_provider slot descriptor may carry, and all three are
# required: the loader calls "handler", returns rows under "result_key", and
# gates the provider by "permission". A missing or misspelled key ships a
# provider the aggregator cannot call or a gate it cannot read, in silence.
SEARCH_PROVIDER_KEYS = {"handler", "result_key", "permission"}
# result_key names the list field the provider returns its rows under. The
# aggregator reads exactly these two; any other value returns rows it never sees.
SEARCH_PROVIDER_RESULT_KEYS = {"items", "entries"}
# The pricing_action slot arrives in Celerp 2.5.4; 2.5.3 and earlier ignore it.
# Kept in sync with the 2.5.4 loader's _validate_pricing_action
# (celerp/modules/loader.py, not present in 2.5.3 and earlier), which refuses the
# whole module when an entry breaks one of these rules.
PRICING_ACTION_KEYS = {"label", "label_key", "href_template", "permission", "show_on", "presentation"}
PRICING_ACTION_PLACEHOLDERS = {"entity_id", "price_list", "field_name"}
PLACEHOLDER_RE = re.compile(r"\{([^{}]*)\}")
# item_action links follow the same link rules, with {entity_id} their only
# placeholder: the 2.5.4 loader's _validate_item_action. Releases up to 2.5.3
# load item_action without checking the link, so lint.py is stricter than they are.
ITEM_ACTION_PLACEHOLDERS = {"entity_id"}
PRICING_ROW_TRAIT_PAIRS = (("editable", "readonly"), ("sell", "cost"), ("manual", "derived"))
# min_celerp_version is optional, but when set it must be a dotted version so
# the loader's comparison means something.
MIN_VERSION_RE = re.compile(r"^\d+(\.\d+){0,2}$")
# Celerp refuses a shorter prefix or one without the trailing underscore
# (celerp/modules/importer.py table_prefix_problem).
MIN_TABLE_PREFIX_LEN = 3
# Every table a table_prefix may not claim: Celerp's own tables, the tables of its
# first-party modules (installed or not), alembic's schema stamp and the instance's
# upgrade markers (celerp/modules/importer.py reserved_tables, Celerp 2.5.4). A prefix
# any of these starts with is refused at install. Other installed modules' prefixes
# and tables are refused too, which only the installation can tell.
RESERVED_TABLES = frozenset({
    "accounts", "ai_batch_jobs", "ai_conversations", "ai_messages", "alembic_version",
    "bank_accounts", "bank_statement_lines", "companies", "connector_configs",
    "connector_sources", "doc_share_tokens", "import_batches", "instance_meta",
    "label_templates", "ledger", "locations", "marketplace_configs",
    "migration_cleanup_tasks", "migration_entity_maps", "migration_runs", "notifications",
    "outbound_queue", "payment_closures", "payment_recoveries", "projections",
    "reconciliation_rules", "reconciliation_sessions", "session_registry",
    "supporter_badges", "sync_runs", "system_runtime_state", "unmatched_payments",
    "unmatched_refunds", "user_auth_state", "user_companies", "users", "work_centers",
})
# Celerp's permission registry (celerp/services/permissions.py). A slot entry's
# permission or write_permission must be one of these; anything else, including an
# empty or false value, is refused rather than read as "no permission needed".
PERMISSION_KEYS = {
    "adjust_inventory", "delete_documents", "edit_contacts", "edit_documents",
    "edit_inventory", "edit_inventory_amounts", "finalize_documents", "fulfill_documents",
    "import_export_data", "manage_accounting", "manage_billing", "manage_company_lifecycle",
    "manage_company_settings", "manage_integrations", "manage_labels",
    "manage_manufacturing", "manage_module_settings", "manage_permissions", "manage_users",
    "record_payments", "revert_items_to_draft", "run_backups", "set_inventory_prices",
    "set_sales_doc_prices", "use_ai_assistant", "view_contacts", "view_dashboards",
    "view_documents", "view_financial_reports", "view_inventory", "view_inventory_costs",
    "view_payments", "view_subscriptions",
}
# The rest mirror celerp/modules/loader.py in Celerp 2.5.4, name for name.
PERMISSION_ENTRY_KEYS = ("permission", "write_permission")
# Entry keys naming where a click goes, and whether the entry must carry the key.
DESTINATION_KEYS = {
    "nav": {"href": False, "settings_href": False},
    "bulk_action": {"form_action": True},
}
# Slots whose entries name code Celerp imports and calls: the entry key holding the
# "module.path:function", and whether Celerp awaits the call (so it must be async)
# or calls it plainly (so it must not be).
CALLABLE_SLOTS = {
    "search_provider": ("handler", True),
    "doc_detail_actions": ("render", False),
    "doc_detail_badges": ("render", False),
    "on_company_created": ("handler", True),
    "on_modules_ready": ("handler", True),
    "doc_finalize_hook": ("handler", True),
    "on_doc_payment": ("handler", True),
    "projection_handler": ("handler", False),
}
# Package names a module cannot use for its code: Python's own modules and Celerp's.
# Celerp imports a callable by its dotted name, and a name Python or Celerp already
# uses resolves to theirs, not to the file in the module.
TAKEN_PACKAGE_NAMES = frozenset(sys.stdlib_module_names) | {"celerp", "ui"}


def _load_manifest(init_file: Path) -> tuple[dict | None, str | None]:
    """Return (manifest, error). A syntax error in `__init__.py` is the most
    common first mistake, so it is reported by name instead of raised as a
    traceback - the point of this script is to name the problem."""
    try:
        tree = ast.parse(init_file.read_text())
    except SyntaxError as exc:
        return None, f"could not parse the file (line {exc.lineno}: {exc.msg})"
    except (OSError, UnicodeDecodeError) as exc:
        return None, f"could not be read ({exc})"
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == "PLUGIN_MANIFEST":
                    try:
                        manifest = ast.literal_eval(node.value)
                    except Exception:
                        return None, None
                    if not isinstance(manifest, dict):
                        return None, (f"PLUGIN_MANIFEST must be a dict, not "
                                      f"{type(manifest).__name__}")
                    return manifest, None
    return None, None


def _protected_imports(py_file: Path) -> set[str]:
    hits: set[str] = set()
    try:
        tree = ast.parse(py_file.read_text())
    except Exception:
        return hits
    for node in ast.walk(tree):
        names = []
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            names = [node.module]
        for name in names:
            for p in PROTECTED:
                if name == p or name.startswith(p + "."):
                    hits.add(p)
    return hits


def _type_problem(field, value) -> str | None:
    """Why `value` cannot be manifest field `field`, or None when it can (or when
    `field` is not one Celerp reads; _ignored_parts names those)."""
    expected = MANIFEST_FIELD_TYPES.get(field) if isinstance(field, str) else None
    if expected is None or (value is None and field != "slots"):
        return None
    if value is None:
        return "slots is None; leave it out, or give it a dict of slot entries"
    if not isinstance(value, expected):
        return f"{field} must be {TYPE_NAMES[expected]}, not {type(value).__name__}"
    if expected is list:
        for member in value:
            if not isinstance(member, str):
                return f"{field} must be {TYPE_NAMES[list]}; {member!r} is not one"
    return None


def _unknown(mapping: dict, known: set[str]) -> list:
    """The keys of `mapping` outside `known`, in a stable order. A literal's keys can
    mix types (1 and "x"), which plain sorted() cannot order."""
    return sorted((k for k in mapping if k not in known), key=repr)


def _slots(manifest: dict) -> dict:
    """The slots dict (lint() has already dropped a slots value of the wrong type)."""
    return manifest.get("slots", {})


def _ignored_parts(manifest: dict) -> list[str]:
    """Manifest parts Celerp reads straight past."""
    problems = []
    for key in _unknown(manifest, MANIFEST_KEYS):
        problems.append(f"manifest has unknown key {key!r} - Celerp reads none of it, "
                        f"so it does nothing at load time")
    for slot in _unknown(_slots(manifest), SLOT_NAMES):
        problems.append(f"manifest has unknown slot {slot!r} - no part of Celerp reads it, "
                        f"so its entries never appear")
    for index, item in enumerate(_nav_items(manifest)):
        for key in _unknown(item, NAV_ITEM_KEYS):
            hint = (" - core hides a nav entry by the role's \"permission\", so this "
                    "entry is visible to everyone" if key == "min_role" else "")
            problems.append(f"nav slot entry {index} has unknown key {key!r}{hint}")
    return problems


def _nav_items(manifest: dict) -> list[dict]:
    """The nav slot's entries, whichever shape the author wrote it in."""
    nav = _slots(manifest).get("nav")
    if isinstance(nav, dict):
        return [nav]
    if isinstance(nav, list):
        return [item for item in nav if isinstance(item, dict)]
    return []


def _is_app_local_path(path) -> bool:
    """Celerp's app-local rule, copied because this script runs without Celerp
    installed: one leading /, never //, no backslash, no ASCII control character.
    From 2.5.4 it lives in celerp/services/app_paths.py is_app_local_path; 2.5.3 and
    earlier have the same rule in ui/security.py."""
    return (
        isinstance(path, str)
        and path.startswith("/")
        and not path.startswith("//")
        and "\\" not in path
        and not any(ord(c) < 0x20 or ord(c) == 0x7F for c in path)
    )


def _href_template_problems(where: str, item: dict, placeholders: set[str]) -> list[str]:
    """The 2.5.4 loader's link rules (loader.py _validate_href_template): an
    href_template inside Celerp whose braces only wrap one of `placeholders`."""
    href = item.get("href_template")
    if not (isinstance(href, str) and href):
        return [f"{where} needs an href_template"]
    if not _is_app_local_path(href):
        return [f"{where} href_template must be a path inside Celerp: one leading /, "
                "never //, no backslash and no control character"]
    problems = []
    for name in sorted(set(PLACEHOLDER_RE.findall(href)) - placeholders):
        problems.append(f"{where} href_template uses {{{name}}} - the placeholders are "
                        + ", ".join(f"{{{p}}}" for p in sorted(placeholders)))
    if set("{}") & set(PLACEHOLDER_RE.sub("", href)):
        problems.append(f"{where} href_template has a stray brace - "
                        "braces may only wrap a placeholder")
    return problems


def _item_action_problems(where: str, item: dict) -> list[str]:
    """The 2.5.4 loader's _validate_item_action, for one entry."""
    return _href_template_problems(where, item, ITEM_ACTION_PLACEHOLDERS)


def _pricing_action_problems(where: str, item: dict) -> list[str]:
    """The 2.5.4 loader's _validate_pricing_action, for one entry."""
    traits = {trait for pair in PRICING_ROW_TRAIT_PAIRS for trait in pair}
    problems = []
    for key in _unknown(item, PRICING_ACTION_KEYS):
        problems.append(f"{where} has unknown key {key!r} - the keys are "
                        + ", ".join(sorted(PRICING_ACTION_KEYS)))
    problems.extend(_href_template_problems(where, item, PRICING_ACTION_PLACEHOLDERS))
    show_on = item.get("show_on", [])
    # Members are type-checked before set(): a dict or list member is unhashable.
    if not (isinstance(show_on, list) and all(isinstance(t, str) for t in show_on)
            and set(show_on) <= traits):
        problems.append(f"{where} show_on must be a list of {sorted(traits)}")
    else:
        for pair in PRICING_ROW_TRAIT_PAIRS:
            if set(pair) <= set(show_on):
                problems.append(f"{where} show_on lists both {pair[0]!r} and {pair[1]!r}, "
                                "so the action would never show")
    if item.get("presentation", "page") != "page":
        problems.append(f'{where} presentation must be "page"')
    return problems


def _projection_handler_problems(where: str, item: dict) -> list[str]:
    """The 2.5.4 loader's _validate_projection_handler, for one entry."""
    prefix = item.get("prefix")
    if not (isinstance(prefix, str) and prefix):
        return [f"{where} needs a prefix: the event-type prefix it handles"]
    return []


# Per-slot checks beyond the generic entry rules (the loader's _SLOT_VALIDATORS).
SLOT_CHECKS = {
    "item_action": _item_action_problems,
    "pricing_action": _pricing_action_problems,
    "projection_handler": _projection_handler_problems,
}


def _entry_problems(where: str, slot: str, item) -> list[str]:
    """The rules every slot entry follows, whatever its slot (the loader's
    _validate_slot_entry)."""
    if not isinstance(item, dict):
        return [f"{where} must be a dict, not {type(item).__name__}"]
    problems = []
    for key in PERMISSION_ENTRY_KEYS:
        if key in item and not (isinstance(item[key], str) and item[key] in PERMISSION_KEYS):
            problems.append(f"{where} {key} {item[key]!r} is not a Celerp permission key - "
                            f"pick the closest existing key, or leave {key} out")
    connector = item.get("requires_connector")
    if connector and not isinstance(connector, str):
        problems.append(f"{where} requires_connector must be a connector id, not {connector!r}")
    for key, required in DESTINATION_KEYS.get(slot, {}).items():
        if key not in item:
            if required:
                problems.append(f"{where} needs a {key}")
        elif not _is_app_local_path(item[key]):
            problems.append(f"{where} {key} {item[key]!r} must be a path inside Celerp: "
                            "one leading /, never //, no backslash and no control character")
    return problems


def _search_provider_problems(folder: Path, item) -> list[str]:
    """The 2.5.4 loader's _prepare_search_provider: one descriptor dict with exactly
    handler, result_key and permission, then the generic entry rules and the
    callable rules."""
    if not isinstance(item, dict):
        return [f"search_provider must be exactly one descriptor dict, not "
                f"{type(item).__name__} - a module contributes a single search provider"]
    problems = [f"search_provider missing required key {key!r}"
                for key in sorted(SEARCH_PROVIDER_KEYS - set(item))]
    problems += [f"search_provider has unknown key {key!r} - the keys are exactly "
                 + ", ".join(sorted(SEARCH_PROVIDER_KEYS))
                 for key in _unknown(item, SEARCH_PROVIDER_KEYS)]
    if problems:
        return problems
    result_key = item["result_key"]
    if not (isinstance(result_key, str) and result_key in SEARCH_PROVIDER_RESULT_KEYS):
        return [f"search_provider result_key {result_key!r} must be one of "
                f"{sorted(SEARCH_PROVIDER_RESULT_KEYS)}"]
    problems = _entry_problems("search_provider", "search_provider", item)
    key, awaited = CALLABLE_SLOTS["search_provider"]
    problems += _callable_problems(f"search_provider {key}", folder, item[key], awaited)
    return problems


def _slot_problems(manifest: dict, folder: Path) -> list[str]:
    """Every slot entry the 2.5.4 loader would refuse (_validate_slots). Every slot is
    checked, a slot name Celerp does not read included."""
    problems = []
    for slot, contribution in _slots(manifest).items():
        if slot == "search_provider":
            problems += _search_provider_problems(folder, contribution)
            continue
        items = contribution if isinstance(contribution, list) else [contribution]
        for index, item in enumerate(items):
            where = f"{slot} entry {index}"
            entry = _entry_problems(where, slot, item)
            problems += entry
            if entry:
                continue
            if slot in CALLABLE_SLOTS:
                key, awaited = CALLABLE_SLOTS[slot]
                problems += _callable_problems(f"{where} {key}", folder, item.get(key), awaited)
            if slot in SLOT_CHECKS:
                problems += SLOT_CHECKS[slot](where, item)
    return problems


# ── callables ────────────────────────────────────────────────────────────────
# Celerp imports a callable slot's "module.path:function" when the module loads and
# refuses the module unless it resolves to code inside the module's own folder and
# is async exactly where Celerp awaits it. lint.py proves the same from the source,
# without running it: it follows each name to the def, async def, class or lambda
# that binds it, through imports between the module's own files. A name it cannot
# follow that way (a decorated function, a star import, a value a call builds) is
# refused, since only running the code would tell.

def _module_source_file(folder: Path, module_path: str) -> Path | None:
    """The file in `folder` a dotted module path names, as the loader finds it
    (loader._module_source_file): the folder is the package itself when it carries
    the package's name, otherwise the package sits one level in."""
    parts = module_path.split(".")
    top = parts[0]
    pkg_dir = folder if folder.name == top else folder / top
    for entry in (pkg_dir.joinpath(*parts[1:]).with_suffix(".py"),
                  pkg_dir.joinpath(*parts[1:], "__init__.py")):
        if entry.exists():
            return entry
    return None


def _parsed(source: Path) -> ast.Module | None:
    try:
        return ast.parse(source.read_text())
    except Exception:
        return None


def _binds(node: ast.AST, name: str) -> bool:
    """Whether module-level statement `node` binds `name` (or might: a star import,
    a del). A def or class binds only its own name; what its body binds is its own."""
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return node.name == name
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        return any(a.name == "*" or (a.asname or a.name.split(".")[0]) == name
                   for a in node.names)
    if isinstance(node, ast.Name):
        return node.id == name and not isinstance(node.ctx, ast.Load)
    if isinstance(node, (ast.Lambda, ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
        return any(isinstance(sub, ast.NamedExpr) and sub.target.id == name for sub in ast.walk(node))
    return any(_binds(child, name) for child in ast.iter_child_nodes(node))


def _callable_kind(folder: Path, top: str, source: Path, name: str,
                   seen: set) -> tuple[str | None, str | None]:
    """("async" or "sync", None) for the callable `name` in `source`, or (None, why)."""
    if (source, name) in seen:
        return None, "is defined in a circle of imports"
    seen.add((source, name))
    tree = _parsed(source)
    if tree is None:
        return None, f"is in {source.name}, which does not parse"
    binding = None
    for node in tree.body:
        if _binds(node, name):
            binding = node
    if binding is None:
        return None, "is not defined there"
    if isinstance(binding, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        if binding.name == name and not binding.decorator_list:
            return ("async" if isinstance(binding, ast.AsyncFunctionDef) else "sync"), None
    elif isinstance(binding, (ast.Assign, ast.AnnAssign)):
        targets = binding.targets if isinstance(binding, ast.Assign) else [binding.target]
        if (len(targets) == 1 and isinstance(targets[0], ast.Name)
                and targets[0].id == name):
            if isinstance(binding.value, ast.Lambda):
                return "sync", None
            if isinstance(binding.value, ast.Name):
                return _callable_kind(folder, top, source, binding.value.id, seen)
    elif isinstance(binding, ast.ImportFrom):
        alias = next((a for a in binding.names if (a.asname or a.name) == name), None)
        if alias is not None:
            pkg_dir = folder if folder.name == top else folder / top
            if binding.level:
                package = source.parent
                for _ in range(binding.level - 1):
                    package = package.parent
                if not package.is_relative_to(pkg_dir):
                    return None, "is imported from outside the module"
                base = package.joinpath(*binding.module.split(".")) if binding.module else package
                target = next((c for c in (base.with_suffix(".py"), base / "__init__.py")
                               if binding.module and c.exists()), None)
                if not binding.module and (package / "__init__.py").exists():
                    target = package / "__init__.py"
            elif (binding.module or "").split(".")[0] == top:
                target = _module_source_file(folder, binding.module)
            else:
                return None, f"is imported from {binding.module!r}, outside the module"
            if target is None:
                return None, "is imported from a file the module does not have"
            return _callable_kind(folder, top, target, alias.name, seen)
    return None, ("cannot be followed to a def, async def, class or lambda in the module's "
                  "own files without running it")


def _callable_problems(where: str, folder: Path, dotted, awaited: bool) -> list[str]:
    """Why Celerp would refuse `dotted` as the callable of a slot whose call it
    awaits (`awaited`) or makes plainly (the loader's _check_slot_callable)."""
    if not (isinstance(dotted, str) and dotted.count(":") == 1 and all(dotted.split(":"))):
        return [f"{where} {dotted!r} must be 'module.path:function'"]
    module_path, name = dotted.split(":")
    top = module_path.split(".")[0]
    if not all(module_path.split(".")):
        return [f"{where} {dotted!r} has an empty part in its module path"]
    if top in TAKEN_PACKAGE_NAMES or top.startswith("celerp_"):
        return [f"{where} {dotted!r}: the package name {top!r} belongs to Python or Celerp, "
                "so Celerp would import theirs, not this module's code"]
    source = _module_source_file(folder, module_path)
    if source is None:
        return [f"{where} {dotted!r} does not name a file inside this module"]
    pkg_dir = folder if folder.name == top else folder / top
    for init in [source, *(p / "__init__.py" for p in source.parents if p.is_relative_to(pkg_dir))]:
        if init.exists() and _parsed(init) is None:
            return [f"{where} {dotted!r}: {init.relative_to(folder)} does not parse"]
    kind, why = _callable_kind(folder, top, source, name, set())
    if kind is None:
        return [f"{where} {dotted!r} {why}"]
    if awaited and kind != "async":
        return [f"{where} {dotted!r} must be async; Celerp awaits it"]
    if not awaited and kind == "async":
        return [f"{where} {dotted!r} must not be async; Celerp calls it without awaiting"]
    return []


def _str_rendered_fragments(py_file: Path) -> list[str]:
    """Lines that hand a fragment to str() instead of to_xml().

    `FT.__str__` returns `self.id`, so `str(Div(..., id="rows"))` is the five
    characters "rows". It raises nothing, logs nothing, and every HTMX swap
    replaces the page region with that word, which is why this check exists.
    """
    try:
        tree = ast.parse(py_file.read_text())
    except Exception:
        return []
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        called = _called_name(node)
        for inner in [node] + list(node.args):
            if not (isinstance(inner, ast.Call) and _called_name(inner) == "str"):
                continue
            arg = inner.args[0] if inner.args else None
            # An FT constructor is capitalised (Div, Table); so is any response
            # class, so a str() inside one is suspect whatever its argument is.
            builds_element = (isinstance(arg, ast.Call)
                              and (_called_name(arg) or "")[:1].isupper())
            if builds_element or (called or "").endswith("Response"):
                found.append(str(inner.lineno))
    return sorted(set(found), key=int)


def _called_name(node: ast.Call) -> str | None:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _owned_tables(folder: Path) -> set[str]:
    """Tables the module's models or migrations create, by literal name."""
    tables: set[str] = set()
    for py_file in folder.rglob("*.py"):
        try:
            tree = ast.parse(py_file.read_text())
        except Exception:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and any(
                    isinstance(t, ast.Name) and t.id == "__tablename__" for t in node.targets):
                value = node.value
            elif isinstance(node, ast.Call) and _called_name(node) == "create_table" and node.args:
                value = node.args[0]
            else:
                continue
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                tables.add(value.value)
    return tables


def _company_backup_problems(manifest: dict, folder: Path) -> list[str]:
    """Every table the module owns must say whether it belongs to the company, and so
    travels with a company backup, or to this installation, like credentials or caches.
    Celerp refuses to back up a company while one of its module tables is not named."""
    declared = manifest.get("company_backup", {})
    prefix = manifest.get("table_prefix", "")
    problems = []
    for table, how in sorted(declared.items(), key=lambda pair: repr(pair[0])):
        if not (isinstance(how, str) and how in COMPANY_BACKUP_VALUES):
            problems.append(f"company_backup says {how!r} for {table!r} - it must be \"include\" "
                            "(company data) or \"exclude\" (installation state such as credentials)")
        if not (prefix and str(table).startswith(prefix)):
            problems.append(f"company_backup names {table!r}, which is not one of this module's "
                            f"tables (they start with table_prefix {prefix!r})")
    for table in sorted(_owned_tables(folder) - set(declared)):
        problems.append(f"table {table!r} is not in company_backup - Celerp will refuse to back up "
                        "a company until it says \"include\" or \"exclude\"")
    return problems


def _table_prefix_problems(manifest: dict) -> list[str]:
    """The table_prefix rules Celerp checks at install (importer._validate_table_prefix)
    that need nothing but the manifest. Celerp also refuses a prefix that overlaps
    another installed module's prefix or tables, which only the installation can tell."""
    if "table_prefix" not in manifest:
        if manifest.get("migrations"):
            return ["migrations needs a table_prefix naming the tables the module owns, "
                    "such as 'acme_' - Celerp refuses a module with migrations and no prefix"]
        return []
    prefix = manifest["table_prefix"]
    if not (isinstance(prefix, str) and prefix):
        return ["table_prefix must name the tables the module owns, such as 'acme_', "
                "or be left out"]
    if len(prefix) < MIN_TABLE_PREFIX_LEN or not prefix.endswith("_"):
        return [f"table_prefix {prefix!r} must be at least {MIN_TABLE_PREFIX_LEN} characters "
                "and end with an underscore, such as 'acme_'"]
    taken = sorted(table for table in RESERVED_TABLES if table.startswith(prefix))
    if taken:
        return [f"table_prefix {prefix!r} claims Celerp's table {taken[0]!r} - choose a "
                "prefix no Celerp table begins with"]
    return []


def check(folder: Path) -> tuple[list[str], list[str]]:
    """(problems, ignored) for the module in `folder`: what Celerp refuses or breaks
    on, and the manifest parts it reads straight past."""
    problems: list[str] = []
    init_file = folder / "__init__.py"
    if not init_file.exists():
        return [f"{folder}: no __init__.py (a module folder must have one)"], []

    manifest, error = _load_manifest(init_file)
    if error is not None:
        return [f"{init_file}: {error}"], []
    if manifest is None:
        return [f"{init_file}: no parseable PLUGIN_MANIFEST dict"], []

    # Types first: every check below reads a field only once it has the right type,
    # so a value of the wrong type is named once, as itself, and never trips them.
    wrong = {field: problem for field, value in manifest.items()
             if (problem := _type_problem(field, value))}
    problems.extend(wrong.values())
    manifest = {field: value for field, value in manifest.items()
                if field not in wrong
                and not (value is None and field in MANIFEST_KEYS - {"table_prefix"})}

    for field in REQUIRED_FIELDS:
        if field not in wrong and not manifest.get(field):
            problems.append(f"manifest missing required field: {field!r}")
    name = manifest.get("name", "")
    if name.startswith("celerp-"):
        problems.append(f"name {name!r} uses the reserved `celerp-` namespace - "
                        "prefix with your own vendor name")
    # Celerp installs a module under its manifest name, whatever the folder is
    # called, so renaming only one of the two lands the module somewhere the
    # author is not looking (or on top of the module they copied).
    if name and folder.name != name:
        problems.append(f"folder name {folder.name!r} does not match "
                        f"PLUGIN_MANIFEST['name'] {name!r} - Celerp installs modules "
                        f"under the manifest name, so this would install as {name!r}")
    min_version = manifest.get("min_celerp_version")
    if min_version is not None and not MIN_VERSION_RE.match(min_version):
        problems.append(f"min_celerp_version {min_version!r} is not a dotted version "
                        "number like '1.4.2' - the version check would not work")
    ignored = _ignored_parts(manifest)
    if not (manifest.get("slots") or manifest.get("api_routes") or manifest.get("ui_routes")):
        ignored.append("manifest declares no slots and no routes - the module does nothing")
    problems.extend(_table_prefix_problems(manifest))
    problems.extend(_slot_problems(manifest, folder))
    problems.extend(_company_backup_problems(manifest, folder))

    for py_file in folder.rglob("*.py"):
        rel = py_file.relative_to(folder)
        hits = _protected_imports(py_file)
        for h in sorted(hits):
            problems.append(f"{rel}: imports protected internal {h!r} "
                            "- the loader will reject this module")
        lines = _str_rendered_fragments(py_file)
        if lines:
            problems.append(f"{rel}: renders a fragment with str() at line(s) "
                            f"{', '.join(lines)} - FT.__str__ returns the element id, so "
                            f"the browser gets that word instead of markup; use to_xml(...)")
    return problems, ignored


def lint(folder: Path) -> list[str]:
    """Every finding for the module in `folder`, problems first."""
    problems, ignored = check(folder)
    return problems + ignored


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: python lint.py path/to/your-module-folder")
        return 2
    folder = Path(sys.argv[1]).resolve()
    problems, ignored = check(folder)
    if problems:
        print(f"✗ {folder.name}: {len(problems)} problem(s)")
        for p in problems:
            print(f"  - {p}")
    if ignored:
        print(f"✗ {folder.name}: {len(ignored)} part(s) Celerp ignores")
        for p in ignored:
            print(f"  - {p}")
    if problems or ignored:
        return 1
    print(f"✓ {folder.name}: looks good")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
