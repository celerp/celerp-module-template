# Celerp module template

A working Celerp module you can copy and have running in about ten minutes.
It adds an **Equipment Maintenance** page to the sidebar: track company
equipment, see what is due for service, and keep a record of each service.

The page is built the Celerp way, so your module looks and behaves native:

- A list page with a search box in the header, status filter cards, a sortable
  table (click a header), Excel-style column filters, a date-range filter, and
  bulk actions that appear once rows are ticked.
- Click-to-edit cells throughout: double-click a value, Esc cancels, nothing
  reloads. Location is a dropdown of the company's own locations, and the date
  fields edit in place.
- A detail page per piece of equipment: its identity, service instructions,
  the full service history with the newest service undoable, and file uploads.
- A printable calendar view of what is due, behind `?view=calendar` on the same
  page, laid out landscape so a month fits one sheet.
- Archive and restore instead of delete, so nothing a user does is one-way.

All of it reuses Celerp's own components and JavaScript, so the behaviour matches
the rest of the app and keeps matching it when the app changes.

Every feature in Celerp is a module on this same loader API - the built-in
inventory, accounting, and manufacturing modules are built exactly like this
one. See the full guide at <https://www.celerp.com/docs/modules.html>.

## What's here

```
AGENTS.md                       read this first: the eight rules and what proves each
acme-maintenance/               the module (copy and rename this whole folder)
  __init__.py                   PLUGIN_MANIFEST - the module's identity and slots
  acme_maintenance/             the inner Python package (underscore, not hyphen)
    models.py                   equipment, its service log, and its files
    routes.py                   API: list / create / edit-field / mark-serviced /
                                archive / restore / service log / files
    ui_routes.py                the /maintenance list, detail and calendar pages
    migrations/                 Alembic migrations on the module's own branch
  tests/                        the module's suite: real database, real API app
    conftest.py                 the harness to copy into your own module
    test_api.py                 permissions, validation, and failure paths
    test_render.py              what the pages render, and what they must not
    test_calendar.py            the calendar view and its print rule
    test_migration.py           the migration applies, and is safe to re-run
    test_module.py              manifest shape and domain logic, no app needed
    htmlq.py                    a small HTML query helper for the render tests
tests/test_lint.py              tests for lint.py itself
lint.py                         check a module without installing the app
.github/workflows/ci.yml        both suites on every push
```

## Run it in your Celerp

1. Copy the `acme-maintenance/` folder into your Celerp data directory's
   `modules/` folder:
   - **macOS**: `~/Library/Application Support/Celerp/celerp-data/modules/`
   - **Linux**: `~/.config/Celerp/celerp-data/modules/`
   - **Windows**: `%APPDATA%\Celerp\celerp-data\modules\`

   That folder already contains Celerp's own seeded modules. Don't edit those,
   and don't reuse their names - drop your module in alongside them.
2. In Celerp, open the **Modules** section and enable **Equipment
   Maintenance**. (You can skip step 1 entirely and use **Import Module**
   there to add the folder directly.)
3. Restart Celerp. A **Maintenance** entry appears under "Operations" in the
   sidebar. Open it, add a piece of equipment, mark it serviced, open the
   record, upload its manual, then switch to the calendar view and print it.

That's the whole loop. Now change something in `ui_routes.py`, restart, and see it.

## Make it yours

1. Read `AGENTS.md`. It is short, and it is the difference between a module that
   looks native and one that looks like a bolt-on.
2. Rename the folder and the inner package (keep the hyphen/underscore split:
   `your-thing` outside, `your_thing` inside). **Do not use a `celerp-` name -
   that prefix is reserved for official modules.**
3. Update `PLUGIN_MANIFEST` in `__init__.py`: name, display name, the nav slot.
   The nav entry's `permission` is what hides it from a role that cannot use the
   page; permission keys are a fixed registry in Celerp, so reuse the key that
   matches what your page does rather than inventing one. Your API router should
   depend on the same key, which is what makes hiding the link and refusing the
   request one decision instead of two.
4. Rename the tables in `models.py` and the migrations, prefixed with your name.
5. Copy `acme-maintenance/tests/conftest.py` and keep the suite honest. It stands
   the real API app and the real pages up against SQLite with nothing mocked, so
   a test failure means a user-visible failure.
6. `python lint.py your-thing/` before every restart - it runs the same checks
   the loader runs plus the two mistakes that are invisible at runtime, so you
   catch them in seconds instead of on a failed boot. Rename the folder and the
   manifest `name` together: Celerp installs a module under its manifest name
   whatever the folder is called, and lint says so if the two drift apart.

### About files

The files section on the detail page is Celerp's own component rendered against
this module's endpoints, so uploads look and work like they do everywhere else.
One consequence to know before you build on it: Celerp's **Company Files** view
aggregates core entities only, and there is no slot for a module to contribute
to it. Your module's files live on your module's pages. Nothing is lost or
hidden, but a user looking for them in Company Files will not find them.

## Disclose what it touches

If you list your module, users see two statements you write, labelled as your
own declaration: what data it touches, and what network calls it makes. Write
the true, specific answer. This module's would be:

- **Data access**: reads and writes three tables of its own (equipment, its
  service log, and its file records), scoped to the current company, plus the
  uploaded files themselves. It reads the company's locations and settings to
  fill a dropdown and to check the current role. No access to any other Celerp
  data.
- **Network calls**: none outside Celerp itself. The page calls Celerp's own
  local API on this machine; nothing reaches the internet.

Put the same two statements in your module's README and in your directory
listing, so a user reading either one sees the same answer.

## List it, or sell it

- **List it free**: open a pull request against
  [community-modules](https://github.com/celerp/community-modules) adding one
  catalog entry. Its README has the full bar a listing must meet.
- **Sell it**: paid modules go through Celerp's marketplace rather than the
  community directory. The "Sell your module" section of the community-modules
  README walks through it.

## The search_provider slot

Alongside `nav`, this template fills the `search_provider` slot, which
contributes a read-only, company-scoped, permission-gated provider to Celerp's
aggregated global search. The aggregator calls your handler once per query,
gates it by the descriptor's permission, reads your rows back under
`result_key`, caps the count, and stamps each row with this module's identity,
so the provider only finds and returns its own matches. A descriptor carries
exactly three required keys:

- `handler`: a dotted `module:function` string (here
  `acme_maintenance.search:global_search`) returning `{result_key: [rows]}`.
- `result_key`: the list field those rows come back under, either `"items"` or
  `"entries"`. Any other value returns rows the aggregator never reads.
- `permission`: a real Celerp permission key from the same fixed registry the
  nav entry uses. It is never left off, because a provider is never implicitly
  public.

`acme_maintenance/search.py` ships a read-only stub so the sample runs without a
database; replace its body with a query against your own tables, scoped to the
company id the aggregator passes in. `python lint.py your-thing/` flags a
descriptor missing a key, carrying an unknown one, or naming a `result_key`
outside those two.

## What to reach for next

- More sidebar behavior and other slots (`bulk_action`, `item_action`,
  `settings_tab`) - see the guide.
- The public module API for AI features lives in `celerp.modules.api`. Which
  internals are off limits, and why, is in `AGENTS.md`; `lint.py` enforces it.

## A note on compatibility

The module loader API can change between releases. Build against the current
release of Celerp; this template tracks it. If a later release changes the API,
update from the latest template.

`min_celerp_version` in the manifest is the oldest Celerp your module runs on.
Celerp refuses to install a module on an older build and tells the user to
update, so set it to the release you actually tested against.

## License

MIT (see `LICENSE`) - you are free to license your own module however you like.
