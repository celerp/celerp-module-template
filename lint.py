#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Lint a Celerp module without installing the app.

Checks the rules the Celerp 2.5.4 loader enforces that need only the module's own
files, so you catch problems in seconds instead of on a failed boot. Where it cannot
tell without running the module's code it refuses rather than guesses, and it cannot
see other installed modules:
  - the folder has an __init__.py with a PLUGIN_MANIFEST
  - the manifest has the required identity fields and at least one slot/route
  - the module name is letters, digits, '-' and '_' (64 at most), matches its
    folder, and is not in the reserved `celerp-` namespace
  - the module's name, and each package or file directly in its folder, is a
    package name Python and Celerp do not already use (Celerp also refuses the name
    of a package installed beside it, which only the installation can tell)
  - api_routes and ui_routes name a file inside the module that defines (or imports
    from the module's own files) a plain, not async, setup_api_routes or
    setup_ui_routes
  - migrations names a package inside the module, and no migration file in it is a
    link to a file outside the module
  - every slot entry follows the rules the Celerp 2.5.4 loader enforces before it
    loads a module: entries are dicts; each key the slot's page or service reads
    holds the type it reads it as, and the keys it needs are there; a permission
    or write_permission names a key from Celerp's permission registry; a
    requires_connector is a connector id or None; a bulk_action names an
    action_type the toolbar knows; category_schema fields are field definitions;
    nav href and settings_href and bulk_action form_action (required) are paths
    inside Celerp; a callable slot names a function in the module's own files,
    async exactly where Celerp awaits it; a projection_handler names its prefix,
    and no two of its prefixes, or one of them and one of Celerp's own, overlap
  - search_provider is one descriptor with exactly handler, result_key and permission
  - pricing_action entries have the shape the loader accepts: known keys only,
    a link that stays inside Celerp, and braces only around a placeholder
  - item_action links stay inside Celerp, with braces only around {entity_id}
  - every top-level manifest field holds the one type Celerp reads it as
  - table_prefix has the shape Celerp requires, claims no table Celerp reserves,
    and a module with migrations sets one
  - every table the module's code or migrations name starts with its table_prefix
  - no source file imports a protected celerp internal (revenue-gated; the
    loader rejects modules that do)
  - no fragment is rendered with str(); FT.__str__ returns the element id, so
    that sends the browser a word where its markup should be

Findings come in two kinds. A problem is something Celerp refuses, or breaks on,
or a rule this repo holds modules to (an unknown slot name is one: the loader
refuses the module). An ignored finding is a part of the manifest Celerp reads
straight past (an unknown key), which is usually a typo.

Usage:  python lint.py path/to/your-module-folder
Exit 0 = clean, 1 = findings (printed).
"""
from __future__ import annotations

import ast
import os
import re
import sys
from pathlib import Path

# Kept in sync with celerp/modules/loader.py _PROTECTED_BSL_INTERNALS.
PROTECTED = {
    "celerp.session_gate", "celerp.ai.service", "celerp.ai.quota",
    "celerp.gateway", "celerp.connectors",
}
REQUIRED_FIELDS = ("name", "version", "display_name", "license")
# The longest module name Celerp accepts (celerp/modules/importer.py _NAME_MAX).
NAME_MAX = 64
# Every top-level field Celerp reads out of a manifest, and the one type it reads
# it as (celerp/modules/loader.py, importer.py, migrations_runner.py). Lint refuses
# any other type; Celerp refuses some (a slots list, a depends_on string) and
# breaks on others (a migrations number has no .split()). None is the same as
# leaving the field out, which is how Celerp reads it, except for table_prefix:
# Celerp checks a declared table_prefix whatever its value.
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
# Every slot core consumes (celerp/modules/slots.py). The loader refuses a module
# that fills any other name, so a misspelled slot is a load failure.
SLOT_NAMES = {
    "nav", "search_provider", "bulk_action", "item_action", "doc_detail_actions",
    "doc_detail_badges", "category_schema", "on_company_created", "on_modules_ready",
    "send_to_targets", "catalog_channel", "projection_handler", "pricing_action",
    "doc_finalize_hook", "on_doc_payment", "inventory_in_production", "item_lineage_guard",
}
# Slots whose answer the books are judged by: Celerp takes them from its own modules
# only and refuses a module of anyone else's that fills one.
FIRST_PARTY_SLOTS = {"inventory_in_production"}
# The slots a module built from this template may fill.
PUBLIC_SLOTS = SLOT_NAMES - FIRST_PARTY_SLOTS
# The event-type prefixes Celerp projects itself. An event type has one handler: the
# loader refuses a module whose projection_handler prefix overlaps one of these.
KERNEL_PROJECTION_PREFIXES = {"sys.", "mp.", "shop.sync."}
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
PRICING_ACTION_KEYS = {"label", "label_key", "href_template", "permission", "show_on", "presentation",
                       "requires_connector"}
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
    "inventory_in_production": ("handler", True),
    "item_lineage_guard": ("handler", True),
}
# Callable slots Celerp calls with keyword arguments only, and those arguments. The
# handler takes exactly these: no other parameter, none positional-only, and no
# *args or **kwargs (the loader's _HANDLER_KEYWORDS).
HANDLER_KEYWORDS = {
    "inventory_in_production": ("session", "company_id"),
    "item_lineage_guard": ("session", "entry", "transition"),
}
# Package names Celerp keeps for itself, and the prefix of its own modules' packages.
RESERVED_IMPORT_NAMES = frozenset({"celerp", "ui", "default_modules", "premium_modules"})
RESERVED_IMPORT_PREFIX = "celerp_"
# Package names a module cannot use for its code: Python's own modules and Celerp's.
# Celerp imports a module's code by package name, and a name Python or Celerp already
# uses means theirs, not the module's file, to everything else in the process.
TAKEN_PACKAGE_NAMES = frozenset(sys.stdlib_module_names) | RESERVED_IMPORT_NAMES
# The action types the inventory toolbar runs a bulk_action as.
BULK_ACTION_TYPES = frozenset({"htmx", "navigate"})
# The keys a category_schema field definition may set beside its key, and their types.
CATEGORY_FIELD_KEYS = {"label": (str,), "type": (str,), "options": (list,)}
_TEXT, _NUMBER = (str,), (int, float)
_LABEL_KEYS = {"label": (_TEXT, False), "label_key": (_TEXT, False)}
# What the code reading each slot takes from an entry: per key, the types it reads
# the value as and whether the entry must carry it (a required text value must not
# be empty). Callable keys, destinations and permissions have their own rules above.
SLOT_ENTRY_KEYS = {
    "nav": {**_LABEL_KEYS, "key": (_TEXT, False), "group": ((str, type(None)), False),
            "order": (_NUMBER, False)},
    "bulk_action": {**_LABEL_KEYS, "action_type": (_TEXT, False)},
    "send_to_targets": {**_LABEL_KEYS, "doc_type": (_TEXT, True)},
    "catalog_channel": {**_LABEL_KEYS, "id": (_TEXT, True), "marker": (_TEXT, False),
                        "can_create": ((bool,), False)},
    "item_action": _LABEL_KEYS,
    "pricing_action": _LABEL_KEYS,
    "category_schema": {"category": (_TEXT, True), "fields": ((list,), True)},
    "projection_handler": {"prefix": (_TEXT, True)},
    "search_provider": {"result_key": (_TEXT, True)},
    "doc_detail_actions": {},
    "doc_detail_badges": {},
    "on_company_created": {},
    "on_modules_ready": {},
    "doc_finalize_hook": {},
    "on_doc_payment": {},
    "inventory_in_production": {},
    "item_lineage_guard": {},
}
ENTRY_TYPE_NAMES = {str: "text", int: "a number", float: "a number", bool: "true or false",
                    list: "a list", type(None): "None"}


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
    """Protected internals `py_file` imports: by name, as a submodule
    (`from celerp.ai import quota`), or by a literal handed to
    importlib.import_module or __import__ (the loader's _scan_protected_imports)."""
    hits: set[str] = set()
    try:
        tree = ast.parse(py_file.read_text())
    except Exception:
        return hits
    for node in ast.walk(tree):
        names = []
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            names = [node.module] + [f"{node.module}.{a.name}" for a in node.names]
        elif (isinstance(node, ast.Call) and _called_name(node) in ("import_module", "__import__")
                and node.args and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)):
            names = [node.args[0].value]
        for name in names:
            for p in PROTECTED:
                if name == p or name.startswith(p + "."):
                    hits.add(p)
    return hits


def _type_problem(field, value) -> str | None:
    """Why `value` cannot be manifest field `field`, or None when it can (or when
    `field` is not one Celerp reads; _ignored_parts names those)."""
    expected = MANIFEST_FIELD_TYPES.get(field) if isinstance(field, str) else None
    if expected is None or value is None:
        return None
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
    """The slots dict (lint() has already dropped a slots value of the wrong type,
    and None is the same as no slots)."""
    return manifest.get("slots") or {}


def _ignored_parts(manifest: dict) -> list[str]:
    """Manifest parts Celerp reads straight past."""
    problems = []
    for key in _unknown(manifest, MANIFEST_KEYS):
        problems.append(f"manifest has unknown key {key!r} - Celerp reads none of it, "
                        f"so it does nothing at load time")
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


def _item_action_problems(where: str, item: dict, folder: Path) -> list[str]:
    """The 2.5.4 loader's _validate_item_action, for one entry."""
    return _href_template_problems(where, item, ITEM_ACTION_PLACEHOLDERS)


def _pricing_action_problems(where: str, item: dict, folder: Path) -> list[str]:
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


def _bulk_action_problems(where: str, item: dict, folder: Path) -> list[str]:
    """The loader's _validate_bulk_action, for one entry."""
    if item.get("action_type", "htmx") not in BULK_ACTION_TYPES:
        return [f"{where} action_type must be one of {sorted(BULK_ACTION_TYPES)}, "
                f"not {item['action_type']!r}"]
    return []


def _category_schema_problems(where: str, item: dict, folder: Path) -> list[str]:
    """The loader's _validate_category_schema, for one entry."""
    return [f"{where} fields must be field definitions: a dict with a text key, and text "
            f"label and type and a list of options where given, not {field!r}"
            for field in item["fields"]
            if not isinstance(field, dict) or not isinstance(field.get("key"), str)
            or not field["key"]
            or any(not _is_type(field[k], types)
                   for k, types in CATEGORY_FIELD_KEYS.items() if k in field)]


def _prefixes_overlap(a: str, b: str) -> bool:
    """Whether one prefix starts with the other (slots.projection_prefixes_overlap):
    the engine applies the first match, so the other handler would never run."""
    return a.startswith(b) or b.startswith(a)


def _projection_prefix_problems(slot: str, items: list, folder: Path) -> list[str]:
    """The loader's _validate_projection_prefixes, plus the prefixes admission
    claims for Celerp itself (_refuse_overlapping_projection_prefixes)."""
    prefixes = [item["prefix"] for _, item in items]
    problems = [f"Slot 'projection_handler' prefixes {a!r} and {b!r} overlap; "
                f"each event type may have one handler only."
                for i, a in enumerate(prefixes) for b in prefixes[i + 1:]
                if _prefixes_overlap(a, b)]
    problems += [f"Projection prefix {p!r} overlaps {c!r}, which 'Celerp' already handles; "
                 f"each event type may have one handler only."
                 for p in prefixes for c in sorted(KERNEL_PROJECTION_PREFIXES)
                 if _prefixes_overlap(p, c)]
    return problems


def _each(check):
    """A slot check over every entry, from a check of one entry."""
    def run(slot: str, items: list, folder: Path) -> list[str]:
        return [p for index, item in items for p in check(f"{slot} entry {index}", item, folder)]
    return run


def _keyword_check(slot: str):
    """The check for a slot in HANDLER_KEYWORDS: the handler's def takes exactly the
    slot's keyword arguments (the loader's _keyword_validator). A handler lint.py
    cannot follow to its def is already refused by _callable_problems."""
    keywords = HANDLER_KEYWORDS[slot]

    def check(where: str, item: dict, folder: Path) -> list[str]:
        node, _ = _callable_node(folder, item.get("handler"))
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) or _takes_exactly(node, keywords):
            return []
        return [f"{where} handler {item['handler']!r} must take exactly the keyword arguments "
                f"{', '.join(keywords)}; Celerp calls it with those and nothing else"]
    return check


def _takes_exactly(node: ast.FunctionDef | ast.AsyncFunctionDef, keywords: tuple[str, ...]) -> bool:
    """Whether the def can be called with exactly `keywords` as keyword arguments and
    nothing else: no positional-only parameter, no *args or **kwargs, no other name."""
    a = node.args
    return (not a.posonlyargs and a.vararg is None and a.kwarg is None
            and sorted(p.arg for p in a.args + a.kwonlyargs) == sorted(keywords))


# Per-slot checks beyond the generic entry rules (the loader's _SLOT_VALIDATORS),
# each called as check(slot, items, folder) with the (index, entry) pairs that pass
# those rules.
SLOT_CHECKS = {
    "item_action": _each(_item_action_problems),
    "pricing_action": _each(_pricing_action_problems),
    "bulk_action": _each(_bulk_action_problems),
    "category_schema": _each(_category_schema_problems),
    "projection_handler": _projection_prefix_problems,
    **{slot: _each(_keyword_check(slot)) for slot in HANDLER_KEYWORDS},
}


def _is_type(value, types: tuple) -> bool:
    """isinstance, except that True and False are not numbers here (loader._is_type)."""
    return isinstance(value, types) and (bool in types or not isinstance(value, bool))


def _entry_problems(where: str, slot: str, item) -> list[str]:
    """The rules every slot entry follows, whatever its slot (the loader's
    _validate_slot_entry)."""
    if not isinstance(item, dict):
        return [f"{where} must be a dict, not {type(item).__name__}"]
    problems = []
    for key, (types, required) in SLOT_ENTRY_KEYS.get(slot, {}).items():
        if key not in item:
            if required:
                problems.append(f"{where} needs a {key}")
        elif not _is_type(item[key], types):
            names = " or ".join(dict.fromkeys(ENTRY_TYPE_NAMES[t] for t in types))
            problems.append(f"{where} {key} must be {names}, not {item[key]!r}")
        elif required and types == _TEXT and not item[key]:
            problems.append(f"{where} {key} must not be empty")
    for key in PERMISSION_ENTRY_KEYS:
        if key in item and not (isinstance(item[key], str) and item[key] in PERMISSION_KEYS):
            problems.append(f"{where} {key} {item[key]!r} is not a Celerp permission key - "
                            f"pick the closest existing key, or leave {key} out")
    connector = item.get("requires_connector")
    if connector is not None and not isinstance(connector, str):
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
    """Every slot the loader would refuse, by name or by entry (_check_slot_contracts).
    A slot it refuses by name is named once; its entries are not read."""
    problems = []
    for slot, contribution in _slots(manifest).items():
        if slot not in SLOT_NAMES:
            problems.append(f"manifest fills unknown slot {slot!r} - the loader refuses the "
                            f"module; a module may fill {', '.join(sorted(PUBLIC_SLOTS))}")
            continue
        if slot in FIRST_PARTY_SLOTS:
            problems.append(f"Slot {slot!r} is filled by Celerp's own modules only.")
            continue
        if slot == "search_provider":
            problems += _search_provider_problems(folder, contribution)
            continue
        items = contribution if isinstance(contribution, list) else [contribution]
        checked = []
        for index, item in enumerate(items):
            where = f"{slot} entry {index}"
            entry = _entry_problems(where, slot, item)
            problems += entry
            if entry:
                continue
            checked.append((index, item))
            if slot in CALLABLE_SLOTS:
                key, awaited = CALLABLE_SLOTS[slot]
                problems += _callable_problems(f"{where} {key}", folder, item.get(key), awaited)
        if slot in SLOT_CHECKS:
            problems += SLOT_CHECKS[slot](slot, checked, folder)
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


def _inside(path: Path, folder: Path) -> bool:
    """Whether `path` resolves inside `folder`, links and '..' followed, as the
    loader's _inside reads it."""
    return Path(os.path.realpath(path)).is_relative_to(os.path.realpath(folder))


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


def _source_callable(folder: Path, top: str, source: Path, name: str,
                     seen: set) -> tuple[ast.AST | None, str | None]:
    """(the def, async def, class or lambda the callable `name` in `source` is, None),
    or (None, why)."""
    if (source, name) in seen:
        return None, "is defined in a circle of imports"
    if not _inside(source, folder):
        return None, f"is in {source.name}, a link to a file outside the module"
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
            return binding, None
    elif isinstance(binding, (ast.Assign, ast.AnnAssign)):
        targets = binding.targets if isinstance(binding, ast.Assign) else [binding.target]
        if (len(targets) == 1 and isinstance(targets[0], ast.Name)
                and targets[0].id == name):
            if isinstance(binding.value, ast.Lambda):
                return binding.value, None
            if isinstance(binding.value, ast.Name):
                return _source_callable(folder, top, source, binding.value.id, seen)
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
            return _source_callable(folder, top, target, alias.name, seen)
    return None, ("cannot be followed to a def, async def, class or lambda in the module's "
                  "own files without running it")


def _callable_node(folder: Path, dotted) -> tuple[ast.AST | None, str | None]:
    """(the def, async def, class or lambda `dotted` names in the module's own files,
    None), or (None, why Celerp would refuse it) (the loader's _owned_callable_source
    and _source_callable)."""
    if not (isinstance(dotted, str) and dotted.count(":") == 1 and all(dotted.split(":"))):
        return None, f"{dotted!r} must be 'module.path:function'"
    module_path, name = dotted.split(":")
    top = module_path.split(".")[0]
    if not all(module_path.split(".")):
        return None, f"{dotted!r} has an empty part in its module path"
    if top in TAKEN_PACKAGE_NAMES or top.startswith(RESERVED_IMPORT_PREFIX):
        return None, (f"{dotted!r}: the package name {top!r} belongs to Python or Celerp, "
                      "so Celerp would import theirs, not this module's code")
    source = _module_source_file(folder, module_path)
    if source is None:
        return None, f"{dotted!r} does not name a file inside this module"
    pkg_dir = folder if folder.name == top else folder / top
    for init in [source, *(p / "__init__.py" for p in source.parents if p.is_relative_to(pkg_dir))]:
        if init.exists() and _parsed(init) is None:
            return None, f"{dotted!r}: {init.relative_to(folder)} does not parse"
    node, why = _source_callable(folder, top, source, name, set())
    return node, None if node is not None else f"{dotted!r} {why}"


def _callable_problems(where: str, folder: Path, dotted, awaited: bool) -> list[str]:
    """Why Celerp would refuse `dotted` as the callable of a slot whose call it
    awaits (`awaited`) or makes plainly (the loader's _check_slot_callable)."""
    node, why = _callable_node(folder, dotted)
    if node is None:
        return [f"{where} {why}"]
    kind = "async" if isinstance(node, ast.AsyncFunctionDef) else "sync"
    if awaited and kind != "async":
        return [f"{where} {dotted!r} must be async; Celerp awaits it"]
    if not awaited and kind == "async":
        return [f"{where} {dotted!r} must not be async; Celerp calls it without awaiting"]
    return []


def _route_problems(manifest: dict, folder: Path) -> list[str]:
    """Why Celerp would refuse the module's api_routes or ui_routes: before any of
    the module's code runs (loader._check_route_source) the file must be inside the
    module and define setup_<kind>_routes or import it from the module's own files,
    and before Celerp calls it (loader._register_module_routes) that setup must be
    the module's own plain function."""
    problems = []
    for kind in ("api", "ui"):
        key, setup = f"{kind}_routes", f"setup_{kind}_routes"
        dotted = manifest.get(key)
        if not dotted:
            continue
        found = _callable_problems(f"{key} setup", folder, f"{dotted}:{setup}", awaited=False)
        if found:
            problems += found
            continue
        binding = None
        for node in _parsed(_module_source_file(folder, dotted)).body:
            if _binds(node, setup):
                binding = node
        if not isinstance(binding, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ImportFrom)):
            problems.append(f"{key} {dotted!r} must define {setup} with def, or import it "
                            "from the module's own files")
    return problems


def _migrations_problems(manifest: dict, folder: Path) -> list[str]:
    """The loader's module_migration_files: migrations is a dotted package path of
    identifiers, resolved under the module folder, and neither the package nor a
    migration file in it (one not starting with _) resolves outside the folder."""
    package = manifest.get("migrations")
    if not package:
        return []
    if not all(part.isidentifier() for part in package.split(".")):
        return [f"migrations {package!r} must be a dotted package path inside the module, "
                "such as 'acme_thing.migrations'"]
    directory = folder.joinpath(*package.split("."))
    if not _inside(directory, folder):
        return [f"migrations {package!r} is a link to a folder outside the module"]
    if not directory.is_dir():
        return []
    return [f"migration file {path.name!r} is a link to a file outside the module"
            for path in sorted(directory.glob("*.py"))
            if path.is_file() and not path.name.startswith("_") and not _inside(path, folder)]


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
            elif (isinstance(node, ast.Call) and _called_name(node) in ("create_table", "Table")
                    and node.args):
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


def _stray_table_problems(manifest: dict, folder: Path) -> list[str]:
    """The loader's _stray_table_problem: every table the module defines carries
    its table_prefix, so a module defining any table needs one."""
    prefix = manifest.get("table_prefix")
    tables = sorted(_owned_tables(folder))
    if tables and not (isinstance(prefix, str) and prefix):
        return [f"table {tables[0]!r} needs a table_prefix the module's tables start with - "
                "Celerp takes out a module defining a table without one"]
    return [f"table {table!r} does not start with table_prefix {prefix!r} - Celerp takes out "
            "a module defining a table outside its prefix" for table in tables
            if not table.startswith(prefix)]


def _import_name_problems(folder: Path) -> list[str]:
    """The loader's _check_import_names, as far as the module's own files tell: the
    names the module answers to (loader._import_roots) are its folder's name and each
    package or source file directly in the folder."""
    roots = {folder.name} | {entry.stem for entry in folder.iterdir()
                             if (entry.is_dir() and (entry / "__init__.py").is_file())
                             or (entry.suffix == ".py" and entry.name != "__init__.py")}
    return [f"the package name {root!r} is already used by Python or Celerp - the module "
            "must use its own" for root in sorted(roots)
            if root in TAKEN_PACKAGE_NAMES or root.startswith(RESERVED_IMPORT_PREFIX)]


def _table_prefix_problems(manifest: dict) -> list[str]:
    """The table_prefix rules Celerp checks at install (importer._validate_table_prefix)
    that need nothing but the manifest. Celerp also refuses a prefix that overlaps
    another installed module's (neither prefix may be a prefix of the other) or
    claims one of its tables, which only the installation can tell."""
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
    if name and (len(name) > NAME_MAX or not name[0].isalnum()
                 or not all(c.isascii() and (c.isalnum() or c in "-_") for c in name)):
        problems.append(f"name {name!r} must start with a letter or digit and hold only "
                        f"letters, digits, '-' and '_', {NAME_MAX} characters at most")
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
    problems.extend(_stray_table_problems(manifest, folder))
    problems.extend(_import_name_problems(folder))
    problems.extend(_route_problems(manifest, folder))
    problems.extend(_migrations_problems(manifest, folder))
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
