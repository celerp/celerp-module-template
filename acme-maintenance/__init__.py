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
    "version": "0.1.0",
    "display_name": "Equipment Maintenance",
    "description": "Track company equipment and see what is due for service.",
    "license": "MIT",
    "author": "Acme",
    # The oldest Celerp this module is built and tested against. Celerp refuses
    # to install it on anything older, with a message telling the user to update.
    "min_celerp_version": "1.4.2",

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
    },

    # ── DB migrations ─────────────────────────────────────────────────────────
    # Dotted path to this package's Alembic directory. Declare it so the path is
    # discoverable, but know what actually creates your tables: your models
    # register on Celerp's shared metadata when the loader imports them, and
    # Celerp runs create_all after loading modules (celerp/main.py), so a NEW
    # table appears on the next launch with no migration involved. Nothing in
    # Celerp runs a module's Alembic directory today, so a migration that CHANGES
    # a table you already shipped is yours to apply against the live database
    # before the new code reaches it. Write it anyway: create_all cannot alter an
    # existing table, so without one an upgrade lands on the old shape.
    "migrations": "acme_maintenance.migrations",

    # ── depends_on / requires ─────────────────────────────────────────────────
    # This template needs nothing. If your module depends on another module,
    # list its name in "depends_on". If it needs pip packages, add a
    # requirements.txt beside this file and list them in "requires" (advisory).
}
