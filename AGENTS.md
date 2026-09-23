# Building a Celerp module

Read this before writing any code in this repo. It is written for whoever does the
work next, human or agent, and every rule below is followed by the file in Celerp
that proves it. Paths starting `celerp/` or `ui/` are in the Celerp checkout; the
rest are in this one.

`acme-maintenance/` is a working module, not a sketch. Copy the folder, rename it,
and change the manifest; the patterns it already uses are the answer to most of the
questions that come up next.

## The eight rules

**1. Render every fragment with `to_xml`.** fastcore's `FT.__str__` returns the
element's `id`, so `HTMLResponse(str(Div(..., id="rows")))` sends the browser the
five characters `rows`. Nothing raises and nothing is logged; the page region just
goes blank or fills with a word. Core renders fragments through `to_xml` everywhere,
and so does this module: `acme-maintenance/acme_maintenance/ui_routes.py:550`.

**2. A module ships no CSS, so use classes that already exist.** There is no hook
for a module stylesheet. A class core has never heard of renders unstyled, which is
how a page ends up full-width with the boxes touching. The whole vocabulary is in
`ui/static/app.css`; read it before naming anything. Note that the cell components
compose `cell--{type}` at render time, so `cell--date` is core's even though no core
file contains the string.

**3. Use the shared components rather than a lookalike.** `page_header`
(`ui/components/shell.py:1599`) puts search and actions in the header at the house
size. `display_cell` (`ui/components/table.py:1059`) and `editable_cell`
(`ui/components/table.py:851`) give you double-click-to-edit, ESC to cancel,
save-on-blur, and `--` for an empty value, all pointed at your own routes through
`patch_url` and `edit_url`. `files_section` (`ui/components/files.py:72`) renders
the house files block against any `base_url`. Every one of these is a place where a
hand-rolled version would drift from the rest of the app the first time core changes.

**4. Gate reads and writes with a permission key, and use an existing one.**
Permission keys are a closed registry (`celerp/services/permissions.py:46`), and
the loader refuses a module whose slots name a key outside it
(`celerp/modules/loader.py:763`), so a module cannot invent one today. Pick the
key that matches what the page does. The API router depends on `require_permission`
(`acme-maintenance/acme_maintenance/routes.py:54`) and the sidebar hides an entry
whose `permission` the role does not have (`ui/components/shell.py:1405`); the page
asks the same question so a viewer is never offered a control that would only fail.
Hiding a control is presentation, never protection: the router is what stops a
hand-made request.

**5. Ask for the manifest keys Celerp reads, and nothing else.** An unknown key is
not an error to the loader, it is ignored, so a misspelled gate ships wide open in
silence. `min_role` is the classic: it looks like it gates the nav entry and it does
nothing at all. There is no `icon` key either. The loader reads route modules by
name (`celerp/modules/loader.py:730`), and `lint.py` holds the full list of accepted
keys.

The `search_provider` slot is the same discipline applied to a descriptor rather
than a manifest. It contributes a read-only, company-scoped, permission-gated
provider to Celerp's aggregated global search: the aggregator calls your async
handler once per query, gated by the descriptor's `permission`, reads your rows
back under `result_key`, and caps the count it keeps, so the provider only finds
and returns its own matches. Unlike `nav`, this slot is a single descriptor, not
a list: a module contributes exactly one search provider, and the core loader
reads one dict. It carries exactly three keys, all required. `handler` is a
dotted `module:function` string (here `acme_maintenance.search:global_search`)
for an async function returning `{result_key: [rows]}`: exactly one module path,
one `:`, one function name, and the loader resolves it to source inside this
module's own package, never core or another module. `result_key` is the list
field those rows come back under and must be `"items"` or `"entries"`; any other
value returns rows the aggregator never reads. `permission` is a real Celerp
permission key from the same closed registry as rule 4, never left off, because a
provider is never implicitly public. Each row is a canonical dict (`id`, `label`,
`href`, optional `subtitle`); `href` must be an app-local path that starts with a
single `/`, never `//`, with no backslash or control character. A single
malformed row degrades your whole provider (all or nothing), not just that row,
so return only well-formed rows. Return an empty list only for a genuine
no-match; on an actual failure raise and let it propagate, so the aggregator
marks only your provider degraded and still returns the others' results.
Swallowing the error into an empty list reports "nothing here" for "we could not
ask". `lint.py` flags a descriptor given as a list instead of one dict, missing a
key, carrying an unknown one, naming a `result_key` outside the two the
aggregator reads, or a handler that is not a single `module:function` string.

**6. Your tables come from your models, not from your migrations.** Module models
register on Celerp's shared metadata when the loader imports them, and Celerp runs
`create_all` after loading modules (`celerp/main.py:195`), so a new table appears on
the next launch. Nothing runs a module's Alembic directory today, so a migration
that changes a table you have already shipped is yours to apply against the live
database before the new code reaches it. Write it anyway: `create_all` cannot alter
an existing table.

**7. Filters and view state live in the URL.** Search text, status, and which view
is showing all belong in the query string, so Back works, a bookmark works, and a
link to what someone is looking at works. Fragment routes need a guard for the case
where a browser lands on one directly: redirect to the page carrying the same query,
rather than serving a bare fragment as a document.

**8. Fail honestly.** An unreachable API renders an error that says so; it never
renders an empty list, because "nothing here" and "we could not ask" are different
facts and the user acts differently on each. A rejected edit comes back as the
editor with the value still in it, marked `cell--error` and carrying the reason in
its `title`, which is how core marks one (`ui/routes/inventory.py:2050`); a
toast on its own vanishes and leaves the refused value looking accepted. A list a
user cannot load is not a list they should be told is empty.

## What you may not import

Module code must not import `celerp.session_gate`, `celerp.ai.*`, `celerp.gateway`,
or `celerp.connectors`. Those are licensed internals, and the loader refuses to load
a module that reaches into them (`celerp/modules/loader.py:54`) - not a warning, the
module simply does not start. The public surface for AI features is
`celerp.modules.api`. Everything else in `celerp.services` and `ui.components` is
fair game, and this module uses both.

## What is checked mechanically

Three of these rules are enforced, so a mistake surfaces before a restart rather
than in front of a user:

- Rule 1 and rule 5 are checked by `lint.py:115` and `lint.py:91`. Run
  `python lint.py acme-maintenance` (or your renamed folder) before every restart.
- Rule 2 is checked by a test that renders every view and fails on any class core
  neither styles nor emits: `acme-maintenance/tests/test_render.py:230`.
- The protected-import rule above is checked by `lint.py` as well, so you find out
  before a restart rather than from a module that will not load.
- The citations in this file are checked too, so guidance that has drifted from the
  code fails a test instead of quietly misleading the next reader.

Everything else is checked by the module's own test suite, which runs against a real
SQLite database and the real API app with nothing mocked in between. Start there
when you change behaviour: write the test that fails first.
