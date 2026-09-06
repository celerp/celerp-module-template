# SPDX-License-Identifier: MIT
"""acme-maintenance - the Celerp module template.

Copy this whole folder, rename it, and you have a working Celerp module:
an "Equipment Maintenance" log that adds a sidebar page for tracking
company equipment and flagging what is due for service.

The loader reads PLUGIN_MANIFEST below to learn the module's identity, which
extension slots it fills, and where its routes and migrations live. Everything
the loader needs is in this one dict.

NAMING: third-party modules must NOT use the `celerp-` prefix - that namespace
is reserved for official modules so users can tell first-party from community
at a glance. Prefix with your own name instead (here: `acme-`).
"""

PLUGIN_MANIFEST = {
    # ── Identity ──────────────────────────────────────────────────────────────
    "name": "acme-maintenance",          # the module folder name; your vendor prefix, not celerp-
    "version": "0.2.0",
    "display_name": "Equipment Maintenance",
    "description": "Track company equipment and see what is due for service.",
    "license": "MIT",
    "author": "Acme",
    # The oldest Celerp this module is built and tested against. Celerp refuses
    # to install it on anything older, with a message telling the user to update.
    # Raise this whenever you start using a core component an earlier release
    # did not have: these pages need the shared cell's caller-supplied save URL
    # and date type, and the public files section, which arrive in 2.0.0.
    "min_celerp_version": "2.0.0",

    # ── Routes ────────────────────────────────────────────────────────────────
    # Dotted paths to the inner package's route modules. The loader imports each
    # and calls setup_api_routes(app) / setup_ui_routes(app). Note the inner
    # package uses an underscore (acme_maintenance) even though the folder uses
    # a hyphen (acme-maintenance) - Python packages can't have hyphens.
    "api_routes": "acme_maintenance.routes",
    "ui_routes": "acme_maintenance.ui_routes",

    # ── Extension slots ───────────────────────────────────────────────────────
    # `nav` puts entries in the sidebar. This is the slot to start with: it is
    # consumed by core today, so your page shows up the moment you restart. It is
    # a LIST, the same shape the first-party modules use, so a module that grows a
    # second page adds a second dict rather than changing the slot's type.
    "slots": {
        "nav": [{
            "group": "Operations",       # sidebar group heading; omit for a top-level item
            "key": "maintenance",        # unique nav key
            "label": "Maintenance",      # the sidebar text; there is no icon key to set
            "href": "/maintenance",
            "order": 50,                 # lower sorts higher in its group
            # Core hides a nav entry the user's role cannot use, and it reads
            # "permission" to decide. Permission keys are a fixed registry
            # (celerp/services/permissions.py), so a module reuses the key that
            # matches what its page does rather than inventing one: this module
            # shows equipment records, so it borrows inventory's view key. The
            # same key gates the API router, so hiding the link and blocking the
            # request are one decision.
            "permission": "view_inventory",
        }],
        # `search_provider` contributes this module's rows to the aggregated
        # global search. The aggregator calls the handler company-scoped, gated
        # by the same permission key, and reads the rows back under result_key;
        # it caps the count, so the provider only has to find and return its own
        # matches. Unlike `nav`, this is a single descriptor, not a list: a
        # module contributes exactly one search provider. It carries exactly
        # three keys, all required: a dotted module:function handler returning
        # {result_key: [rows]}, a result_key of "items" or "entries", and a real
        # Celerp permission key (never implicitly public).
        "search_provider": {
            "handler": "acme_maintenance.search:global_search",
            "result_key": "items",
            "permission": "view_inventory",
        },
    },

    # ── DB migrations ─────────────────────────────────────────────────────────
    # table_prefix is the prefix every table this module owns shares; "migrations"
    # is the dotted path to this package's migration files. Celerp runs each
    # enabled module's migrations at startup, in filename order, before the module
    # loads, and keeps no version state - so write each one to check for what it
    # creates before it creates it, and it stays safe to run on every launch (see
    # the migrations/ files). A brand new table also appears from your models
    # through create_all, but only a migration can ALTER a table you have already
    # shipped, which create_all cannot. Celerp reads table_prefix to keep a
    # migration's changes to your own tables, and it is the exact set Celerp drops
    # if the module is deleted.
    "table_prefix": "acme_",
    "migrations": "acme_maintenance.migrations",

    # ── depends_on / requires ─────────────────────────────────────────────────
    # This template needs nothing. If your module depends on another module,
    # list its name in "depends_on". If it needs pip packages, add a
    # requirements.txt beside this file and list them in "requires" (advisory).
}
