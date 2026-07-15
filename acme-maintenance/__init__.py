# SPDX-License-Identifier: MIT
"""acme-maintenance — the Celerp module template.

Copy this whole folder, rename it, and you have a working Celerp module:
an "Equipment Maintenance" log that adds a sidebar page for tracking
company equipment and flagging what is due for service.

The loader reads PLUGIN_MANIFEST below to learn the module's identity, which
extension slots it fills, and where its routes and migrations live. Everything
the loader needs is in this one dict.

NAMING: third-party modules must NOT use the `celerp-` prefix — that namespace
is reserved for official modules so users can tell first-party from community
at a glance. Prefix with your own name instead (here: `acme-`).
"""

PLUGIN_MANIFEST = {
    # ── Identity ──────────────────────────────────────────────────────────────
    "name": "acme-maintenance",          # the module folder name; your vendor prefix, not celerp-
    "version": "0.1.0",
    "display_name": "Equipment Maintenance",
    "description": "Track company equipment and see what is due for service.",
    "license": "MIT",
    "author": "Acme",

    # ── Routes ────────────────────────────────────────────────────────────────
    # Dotted paths to the inner package's route modules. The loader imports each
    # and calls setup_api_routes(app) / setup_ui_routes(app). Note the inner
    # package uses an underscore (acme_maintenance) even though the folder uses
    # a hyphen (acme-maintenance) — Python packages can't have hyphens.
    "api_routes": "acme_maintenance.routes",
    "ui_routes": "acme_maintenance.ui_routes",

    # ── Extension slots ───────────────────────────────────────────────────────
    # `nav` puts an entry in the sidebar. This is the slot to start with: it is
    # consumed by core today, so your page shows up the moment you restart.
    "slots": {
        "nav": {
            "group": "Operations",       # sidebar group heading; omit for a top-level item
            "key": "maintenance",        # unique nav key
            "icon": "🛠",
            "label": "Maintenance",
            "href": "/maintenance",
            "order": 50,                 # lower sorts higher in its group
            "min_role": "operator",      # operator | admin | owner
        },
    },

    # ── DB migrations ─────────────────────────────────────────────────────────
    # Dotted path to an Alembic migrations directory inside this package. The
    # loader adds it to the migration version locations; your table is created
    # on the next launch. The migration sits on its own branch (see the file).
    "migrations": "acme_maintenance.migrations",

    # ── depends_on / requires ─────────────────────────────────────────────────
    # This template needs nothing. If your module depends on another module,
    # list its name in "depends_on". If it needs pip packages, add a
    # requirements.txt beside this file and list them in "requires" (advisory).
}
