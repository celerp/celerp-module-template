# Celerp module template

A working Celerp module you can copy and have running in about ten minutes.
It adds an **Equipment Maintenance** page to the sidebar: track company
equipment and see what is due for service.

Every feature in Celerp is a module on this same loader API — the built-in
inventory, accounting, and manufacturing modules are built exactly like this
one. See the full guide at <https://www.celerp.com/docs/modules>.

## What's here

```
acme-maintenance/               the module (copy and rename this whole folder)
  __init__.py                   PLUGIN_MANIFEST — the module's identity and slots
  acme_maintenance/             the inner Python package (underscore, not hyphen)
    models.py                   one table: company equipment
    routes.py                   API: list / add / mark-serviced  (/api/maintenance)
    ui_routes.py                the /maintenance sidebar page
    migrations/                 Alembic migration on the module's own branch
  tests/test_module.py          manifest + domain-logic tests, no app needed
lint.py                         check a module without installing the app
```

## Run it in your Celerp

1. Copy the `acme-maintenance/` folder into your Celerp data directory's
   `modules/` folder:
   - **macOS**: `~/Library/Application Support/Celerp/celerp-data/modules/`
   - **Linux**: `~/.config/celerp/celerp-data/modules/`
   - **Windows**: `%APPDATA%\Celerp\celerp-data\modules\`

   That folder already contains Celerp's own seeded modules. Don't edit those,
   and don't reuse their names — drop your module in alongside them.
2. In Celerp, open **Settings → Modules** and enable **Equipment Maintenance**.
3. Restart Celerp. A **Maintenance** entry appears under "Operations" in the
   sidebar. Open it, add a piece of equipment, mark it serviced.

That's the whole loop. Now change something in `ui_routes.py`, restart, and see it.

## Make it yours

1. Rename the folder and the inner package (keep the hyphen/underscore split:
   `your-thing` outside, `your_thing` inside). **Do not use a `celerp-` name —
   that prefix is reserved for official modules.**
2. Update `PLUGIN_MANIFEST` in `__init__.py`: name, display name, the nav slot.
3. Rename the table in `models.py` and the migration, prefixed with your name.
4. `python lint.py your-thing/` before every restart — it runs the same checks
   the loader runs, so you catch mistakes in seconds instead of on a failed boot.

## What to reach for next

- More sidebar behavior and other slots (`bulk_action`, `item_action`,
  `settings_tab`) — see the guide.
- The public module API for AI features lives in `celerp.modules.api`. Module
  code must **not** import `celerp.session_gate`, `celerp.ai.*`,
  `celerp.gateway`, or `celerp.connectors` directly — those are revenue-gated
  internals and the loader rejects modules that import them. `lint.py` flags it.

## A note on compatibility

The module loader API is still evolving between releases. Build against the
current release of Celerp; this template tracks it. If a future release changes
the API, update against the latest template.

## License

MIT (see `LICENSE`) — you are free to license your own module however you like.
