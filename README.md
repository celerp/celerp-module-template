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
    migrations/                 schema changes Celerp runs at every start
  tests/                        the module's suite: real database, real API app
    conftest.py                 the harness to copy into your own module
    test_api.py                 permissions, validation, and failure paths
    test_render.py              what the pages render, and what they must not
    test_calendar.py            the calendar view and its print rule
    test_migration.py           the migration applies, and is safe to re-run
    test_module.py              manifest shape and domain logic, no app needed
    htmlq.py                    a small HTML query helper for the render tests
tests/test_lint.py              tests for lint.py itself
tests/test_core_parity.py       lint.py against Celerp's own checks (needs Celerp 2.5.4)
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
   Every package or `.py` file directly in the module folder is a name Python
   imports it by, so none may be a name Python or Celerp already uses (no
   `json.py`, no `ui/`, nothing starting `celerp_`).
3. Update `PLUGIN_MANIFEST` in `__init__.py`: name, display name, the nav slot.
   The nav entry's `permission` is what hides it from a role that cannot use the
   page; permission keys are a fixed registry in Celerp, so reuse the key that
   matches what your page does rather than inventing one. Your API router should
   depend on the same key, which is what makes hiding the link and refusing the
   request one decision instead of two.
4. Rename the tables in `models.py` and the migrations, prefixed with your name,
   and set the manifest's `table_prefix` to that prefix (at least 3 characters,
   ending in `_`, such as `"acme_"`). No table Celerp keeps for itself may start
   with the prefix, so `label_`, `marketplace_` and `bank_` are taken, and no other
   installed module's prefix may overlap it: neither prefix may be a prefix of the
   other. From Celerp 2.5.4 a `table_prefix` that is present must follow these
   rules even without migrations, so leave the key out rather than setting it to
   `None` if the module has no tables. Every table your code defines must start
   with the prefix: Celerp takes out a module defining a table outside it, or any
   table without one, before creating any table.
   List each one in the manifest's `company_backup` as `"include"` (the company's
   records, carried by a company backup) or `"exclude"` (this installation's state,
   such as stored credentials). Celerp refuses to back up a company while one of
   your tables is not listed.
5. Copy `acme-maintenance/tests/conftest.py` and keep the suite honest. It stands
   the real API app and the real pages up against SQLite with nothing mocked, so
   a test failure means a user-visible failure.
6. `python lint.py your-thing/` before every restart - it checks the rules the
   Celerp 2.5.4 loader enforces that need only your module's files, plus the two
   mistakes that are invisible at runtime, so you catch them in seconds instead
   of on a failed boot. It reports two kinds of finding: problems Celerp refuses
   or breaks on, and parts of the manifest Celerp ignores (an unknown key or slot
   name, usually a typo). It exits 1 on either. Rename the folder and the
   manifest `name` together: Celerp installs a module under its manifest name
   whatever the folder is called, from 2.5.4 it will not load a module whose
   folder and name differ, and lint says so if the two drift apart.

   `tests/test_core_parity.py` loads every case lint checks through Celerp's own
   loader and fails wherever the two disagree. Lint is stricter in a few places,
   because it reads your code without running it: it refuses a slot callable it
   cannot follow to a `def`, `async def`, class or lambda in your own files (a
   decorated function, a star import, a `functools.partial`, a binding inside
   `if` or `try`) and a protected import in any file, used or not. It cannot see
   other installed modules or packages, so a prefix or table clash with another
   module, or a package name an installed package already uses, only shows when
   Celerp loads the module.

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
  community directory. Sign in at [celerp.com/authors](https://www.celerp.com/authors/)
  to publish one. The [module guide](https://www.celerp.com/docs/modules.html#share)
  covers both routes.

## The search_provider slot

Alongside `nav`, this template fills the `search_provider` slot, which
contributes a read-only, company-scoped, permission-gated provider to Celerp's
aggregated global search. The aggregator calls your async handler once per
query, gates it by the descriptor's permission, reads your rows back under
`result_key`, and caps the count it keeps, so the provider only finds and
returns its own matches. Unlike `nav`, this slot is a single descriptor, not a
list: a module contributes exactly one search provider. It carries exactly three
required keys:

- `handler`: a dotted `module:function` string (here
  `acme_maintenance.search:global_search`) for an async function returning
  `{result_key: [rows]}`. It is exactly one module path, one `:`, one function
  name, and the loader resolves it to source inside this module's own package
  (a handler that resolves into core or another module is rejected at load).
- `result_key`: the list field those rows come back under, either `"items"` or
  `"entries"`. Any other value returns rows the aggregator never reads.
- `permission`: a real Celerp permission key from the same fixed registry the
  nav entry uses. It is never left off, because a provider is never implicitly
  public.

Each row is a canonical dict: `id`, `label`, `href`, and an optional `subtitle`.
`href` must be an app-local path Celerp can route: it starts with a single `/`,
never `//`, and carries no backslash or ASCII control character, so it can only
link within this app and never off-site. The aggregator validates every row, and
a single malformed row degrades your whole provider (all or nothing), not just
that row, so return only well-formed canonical rows. Return an empty list only
for a genuine no-match, where the query ran and found nothing; on an actual
failure raise and let the exception propagate, so the aggregator marks only your
provider degraded and still returns the other providers' results. Swallowing the
error into an empty list reports "nothing here" for "we could not ask".
`acme_maintenance/search.py` ships a read-only stub so the sample runs without a
database; replace its body with a query against your own tables, scoped to the
company id the aggregator passes in, mapping each match to a canonical row.
`python lint.py your-thing/` flags a descriptor given as a list instead of one
dict, missing a key, carrying an unknown one, naming a `result_key` outside those
two, or a handler the loader would refuse (see the next section).

## Rules every slot entry follows

Any module may fill any slot, and from Celerp 2.5.4 the loader refuses a module
whose slot entries break any of these:

- Each entry is a dict, and carries what the code reading its slot takes from
  it, in the type it reads it as: `nav` `order` is a number and `key`, `label`
  and `group` are text; a `send_to_targets` entry names its `doc_type`; a
  `catalog_channel` entry names its `id`; a `category_schema` entry names its
  `category` and a list of `fields`, each a dict with a text `key` and, where
  given, text `label` and `type` and a list of `options`; a `bulk_action`
  `action_type` is `htmx` or `navigate`.
- `permission` and `write_permission`, where present, name keys from Celerp's
  permission registry.
- `requires_connector`, where set, is a connector id string.
- A destination Celerp links to is a path inside Celerp (one leading `/`, never
  `//`, no backslash and no control character): `nav` `href` and
  `settings_href`, `bulk_action` `form_action` (required), and `item_action`
  `href_template`.
- A `projection_handler` entry names its event-type `prefix`.
- A slot that names code to run (`handler`, or `render` for `doc_detail_actions`
  and `doc_detail_badges`) gives one `module:function` that resolves to a
  callable in your module's own files. It is an `async def` exactly where Celerp
  awaits it (`search_provider`, `on_company_created`, `on_modules_ready`,
  `doc_finalize_hook`, `on_doc_payment`, `inventory_in_production`,
  `item_lineage_guard`) and a plain (not async) callable everywhere else.
- Celerp calls two handlers with keyword arguments only, so each takes exactly
  these and no other parameter, none positional-only and no `*args` or
  `**kwargs`: `inventory_in_production(session, company_id)` returns the stock
  value issued to work still open that the books still carry on the inventory
  accounts, and `item_lineage_guard(session, entry, transition)` runs on every
  live item event, after it is applied and before its effects are booked, and
  refuses the event by raising.

Every top-level manifest field must also hold the one type Celerp reads it as;
`python lint.py` reports each of these.

## A link on the Pricing tab

From Celerp 2.5.4 a module can put a link on rows of an item's Pricing tab, for
example a page that suggests a price for one price list:

```python
"min_celerp_version": "2.5.4",
"slots": {
    "pricing_action": [{
        "label": "Suggest price",
        "href_template": "/acme-pricing/{entity_id}?list={price_list}&field={field_name}",
        "show_on": ["sell", "manual", "editable"],
        "permission": "set_inventory_prices",
    }],
},
```

`{entity_id}` is the item, `{price_list}` the list name and `{field_name}` the
item field holding that price; each is URL-encoded. `show_on` limits the link to
rows with every listed trait (`editable`/`readonly`, `sell`/`cost`,
`manual`/`derived`); leave it out for every row. The link opens your page, and
that page's route must check `set_inventory_prices` itself.

`href_template` must be a path inside Celerp: one leading `/`, never `//`, no
backslash and no control character. Braces may only wrap one of the three
placeholders, the descriptor takes only `label`, `label_key`,
`href_template`, `permission`, `show_on`, `presentation` and
`requires_connector` (the link shows only while the company is connected to
that connector), and `show_on` holds
only the six traits, never both of one pair. From 2.5.4 the loader refuses a
module that breaks any of these; 2.5.3 and earlier ignore the slot and show
nothing. `python lint.py` reports each one, whichever release you target. The sample module does not use this slot, so it still
installs on 2.0.0.

## What to reach for next

- Other slots (`bulk_action`, `item_action`, `doc_detail_actions`,
  `category_schema` and the rest), the manifest reference, the permission keys,
  and how to list or sell a module - see the
  [module guide](https://www.celerp.com/docs/modules.html). `item_action`,
  `doc_detail_actions` and `doc_detail_badges` show only while the module is
  switched on and the role holds the contribution's `permission`, from Celerp
  2.5.4; older releases show them to everyone. Either way, the target page must
  check permissions. From 2.5.4 an `item_action` `href_template` follows
  the Pricing-row link rules (inside Celerp, braces only around `{entity_id}`),
  and `python lint.py` reports a link that breaks them.
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
