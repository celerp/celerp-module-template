# Building a Celerp module

Read this before writing any code in this repo. It is written for whoever does the
work next, human or agent, and every rule below is followed by the file in Celerp
that proves it. Paths starting `celerp/` or `ui/` are in the Celerp checkout, with
line numbers from Celerp 2.5.4; the rest are in this one.

`acme-maintenance/` is a working module, not a sketch. Copy the folder, rename it,
and change the manifest; the patterns it already uses are the answer to most of the
questions that come up next.

## Building for someone with an idea

Often the person you are working for describes a module in a sentence and is not a
developer. Your job is the whole path: build the module, check it, publish it in
their repository, and send it to Celerp's community catalog. They should never need
a terminal or git. Explain each step in plain words, and ask them only for decisions
that are theirs: what the module does, its name, and its license. The full guide is
at <https://www.celerp.com/docs/modules.html>; this section is the order of work.

1. **Make it theirs.** Agree a short id: lowercase letters, digits and hyphens. It
   must not start with `celerp`, look like Celerp, or match the name or id of a
   module already in
   <https://github.com/celerp/community-modules/blob/main/index.json>
   (`acme-maintenance` is taken by this template). Rename `acme-maintenance/` to the
   id and the inner package `acme_maintenance/` to the id with underscores, then
   update every reference: the manifest `name`, `api_routes` and `ui_routes`,
   imports, the tests, `.github/workflows/ci.yml`, and this README. Set
   `display_name`, `description`, `version` and `author`, then replace the
   equipment code with what was asked for, following the eight rules below.
2. **Say what it touches.** The module's README states which data it reads and
   writes and which network calls it makes ("None." when it makes none); the
   catalog entry repeats both. Keep the MIT `LICENSE` unless the person picks
   another, and put their name or GitHub account in its copyright line. The
   manifest `license` must be the SPDX id of that file.
3. **Check it.** Run `python lint.py <id>`, `pytest tests`, and the module suite
   exactly as `.github/workflows/ci.yml` runs it. Fix every failure; never skip
   one. If you cannot install Celerp where you are running, push and read the CI
   result instead.
4. **Publish it.** The repository must be public. Commit and push to its default
   branch (usually `main`): a listing points at a commit on that branch, so work
   left on another branch cannot be listed. If you can only push a branch, open a
   pull request into the default branch of the person's own repository, ask them
   to press **Merge**, and continue from the merged commit.
5. **Build the zip, if they want to try it first.** From the repository root,
   `zip -r <id>.zip <id>/`. In Celerp, **Modules**, then **Installed Modules**, then
   **Import Module** takes that zip. GitHub's **Code**, then **Download ZIP** also
   works, since Celerp finds the one folder that holds `PLUGIN_MANIFEST`.
6. **Submit it.** The submission is a pull request to `celerp/community-modules`
   opened from the GitHub account that owns the module's repository:
   - Take the full 40-character id of the commit at the head of the default branch
     (`git rev-parse HEAD` after the push, and check it matches GitHub).
   - Fork `celerp/community-modules` into the person's account
     (`gh repo fork celerp/community-modules --clone`) and branch from its `main`.
   - Add one entry to `index.json` with `id`, `name`, `description`,
     `"tier": "community"`, `repo` (`https://github.com/<owner>/<repository>`),
     `commit`, `author`, `license`, `data_access` and `network_calls`, and no
     `version`. Official entries stay first; community entries sort by `id`.
   - Run `python3 scripts/gen_readme.py` and `python3 scripts/validate_index.py`,
     commit `index.json` and `README.md` only, push, and open the pull request
     against `main`. Give the person its link.
7. **Follow it through.** An automatic listing check posts one comment on the pull
   request and keeps it current. When every check passes, the pull request is
   merged automatically and the module appears in the Community Modules tab of
   every Celerp on its next catalog refresh, usually within minutes. A comment
   headed "Listing check: changes needed" lists what to fix: fix the module, push
   it to the default branch, set `commit` to the new head, run `gen_readme.py`
   again, and push to the same pull request; the check runs again. "Listing check:
   waiting for the maintainer" means something in the code was flagged for a
   person to look at; tell them what, and wait.
8. **New versions.** Raise `version` in the manifest, push to the default branch,
   and submit an update the same way: a fresh branch of the fork from the current
   `celerp/community-modules` `main`, the existing entry's `commit` set to the new
   full commit id, `gen_readme.py` run, and a new pull request from the same
   account. Celerp only offers the listed commit, so every version is a new
   `commit` in the entry.
9. **If you cannot act on GitHub as the person** (you cannot fork, push, or open a
   pull request from where you run), do not stop at "done". Finish everything you
   can, then give them the exact entry text, the regenerated `README.md`, and
   numbered steps to make the same change on github.com, and say plainly which
   steps are theirs.

## The eight rules

**1. Render every fragment with `to_xml`.** fastcore's `FT.__str__` returns the
element's `id`, so `HTMLResponse(str(Div(..., id="rows")))` sends the browser the
five characters `rows`. Nothing raises and nothing is logged; the page region just
goes blank or fills with a word. Core renders fragments through `to_xml` everywhere,
and so does this module: `acme-maintenance/acme_maintenance/ui_routes.py:548`.

**2. A module ships no CSS, so use classes that already exist.** There is no hook
for a module stylesheet. A class core has never heard of renders unstyled, which is
how a page ends up full-width with the boxes touching. The whole vocabulary is in
`ui/static/app.css`; read it before naming anything. Note that the cell components
compose `cell--{type}` at render time, so `cell--date` is core's even though no core
file contains the string.

**3. Use the shared components rather than a lookalike.** `page_header`
(`ui/components/shell.py:2571`) puts search and actions in the header at the house
size. `display_cell` (`ui/components/table.py:1159`) and `editable_cell`
(`ui/components/table.py:938`) give you double-click-to-edit, ESC to cancel,
save-on-blur, and `--` for an empty value, all pointed at your own routes through
`patch_url` and `edit_url`. `files_section` (`ui/components/files.py:72`) renders
the house files block against any `base_url`. Every one of these is a place where a
hand-rolled version would drift from the rest of the app the first time core changes.

**4. Gate reads and writes with a permission key, and use an existing one.**
Permission keys are a closed registry (`celerp/services/permissions.py:56`), and
the loader refuses a module whose slot entries name a key outside it, in either
`permission` or `write_permission` (`celerp/modules/loader.py:2501`), so a module
cannot invent one today. Pick the
key that matches what the page does. The API router depends on `require_permission`
(`acme-maintenance/acme_maintenance/routes.py:57`) and the sidebar hides an entry
whose `permission` the role does not have (`ui/components/shell.py:2376`); the page
asks the same question so a viewer is never offered a control that would only fail.
Hiding a control is presentation, never protection: the router is what stops a
hand-made request.

**5. Ask for the manifest keys Celerp reads, and nothing else.** An unknown key is
not an error to the loader, it is ignored, so a misspelled gate ships wide open in
silence. `min_role` is the classic: it looks like it gates the nav entry and it does
nothing at all. There is no `icon` key either. The loader reads route modules by
name (`celerp/modules/loader.py:1727`), and `lint.py` holds the full list of accepted
keys. `api_routes` and `ui_routes` each name a file inside the module that defines
its own `setup_api_routes` or `setup_ui_routes` (`celerp/modules/loader.py:656`). The
manifest `name` must equal the module's folder name, start with a letter or digit, and
hold only letters, digits, `-` and `_`, 64 characters at most
(`celerp/modules/importer.py:86`). Every top-level manifest field must also hold the one type Celerp reads it as.
`lint.py` reports an ignored key as its own kind of finding, separate from a
problem the loader refuses, and exits 1 on either. A slot name is not like a key:
the loader refuses a module that fills a slot Celerp does not read, and `lint.py`
reports it as a problem.

The `search_provider` slot is the same discipline applied to a descriptor rather
than a manifest. It contributes a read-only, company-scoped, permission-gated
provider to Celerp's aggregated global search: the aggregator calls your async
handler once per query, gated by the descriptor's `permission`, reads your rows
back under `result_key`, and caps the count it keeps, so the provider only finds
and returns its own matches. Unlike `nav`, this slot is a single descriptor, not
a list: a module contributes exactly one search provider, and the core loader
reads one dict. It carries exactly three keys, all required. `handler` is a
dotted `module:function` string (here `acme_maintenance.search:global_search`)
for an async function returning `{result_key: [rows]}`, held to the same rules as
every other slot callable (below). `result_key` is the list
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
aggregator reads, or a handler the loader would refuse.

Every slot entry, in any slot, follows the same rules from Celerp 2.5.4
(`celerp/modules/loader.py:2474`). A module may fill every slot in `lint.PUBLIC_SLOTS`;
`inventory_in_production` is filled by Celerp's own modules only. Each entry is a
dict, and carries what the code reading its slot takes from it, in the type it
reads it as (`celerp/modules/loader.py:2444`): a `nav` `order` is a number, a
`send_to_targets` entry names its `doc_type`, a `catalog_channel` its `id`, a
`category_schema` entry its `category` and a list of `fields`, each a dict with a
text `key`, and a `bulk_action` `action_type` is `htmx` or `navigate`.
`permission` and `write_permission` name registry keys (rule 4).
`requires_connector`, when set, is a connector id string. A destination Celerp
links to (`nav` `href` and `settings_href`, `bulk_action` `form_action`, which is
required, and `item_action` `href_template`) is a path inside Celerp: one leading
`/`, never `//`, no backslash and no control character
(`celerp/services/app_paths.py:12`). A `projection_handler` `prefix` is a non-empty
string, and no prefix starts with another: within the module, across enabled
modules, or against Celerp's own `sys.`, `mp.` and `shop.sync.`
(`celerp/modules/slots.py`, `KERNEL_PROJECTION_PREFIXES`). A slot that names code to run (its `handler`, or `render` for the
`doc_detail_*` slots) gives one `module:function` that resolves to a callable in
this module's own files, async exactly where Celerp awaits it (`celerp/modules/loader.py:2712`).
The `item_lineage_guard` handler is called with keyword arguments only, so it
takes exactly `session, entry, transition`: no other parameter, none
positional-only, and no `*args` or `**kwargs` (`celerp/modules/loader.py:2319`).

The tables a module creates must start with its `table_prefix`: at least 3
characters, ending in `_`, and no table Celerp keeps for itself may start with it,
so `label_`, `marketplace_` and `bank_` are taken (`celerp/modules/importer.py:253`). Two installed
modules' prefixes may not overlap either: neither prefix may be a prefix of the
other (`celerp/modules/importer.py:327`). Only the installation knows the other
modules, so `lint.py` checks every rule here except that last one. A module whose
code defines a table outside its prefix, or any table without one, is taken out
before any table is created (`celerp/modules/loader.py:1791`).

A module's package names are its own. The module folder, and each package or
importable file (source, compiled or extension) directly inside it, is a name
Python imports it by, so none of them may
be a name Python, Celerp, an installed package or another module already uses: no `json.py`, no
`ui/` package, nothing starting `celerp_` (`celerp/modules/loader.py:772`).

**6. New tables come from your models; changes to shipped tables come from
migrations.** Module models register on Celerp's shared metadata when the loader
imports them, and Celerp runs `create_all` after loading modules
(`celerp/main.py:309`), so a new table appears on the next launch. A module that
fails to load, or whose routes fail to register, gets none of its tables created
(`celerp/modules/loader.py:1321`). `create_all`
cannot alter an existing table, so any change to a table you have shipped is a
migration. Celerp runs every file in the manifest's `migrations` package at each
start, in filename order, before the module loads
(`celerp/modules/migrations_runner.py:167`), and only from a package inside the module
folder (`celerp/modules/loader.py:626`). It keeps no version record, so each
step checks before it acts and is safe to run again, and every table a migration
touches must start with the manifest's `table_prefix`.

**7. Filters and view state live in the URL.** Search text, status, and which view
is showing all belong in the query string, so Back works, a bookmark works, and a
link to what someone is looking at works. Fragment routes need a guard for the case
where a browser lands on one directly: redirect to the page carrying the same query,
rather than serving a bare fragment as a document.

**8. Fail honestly.** An unreachable API renders an error that says so; it never
renders an empty list, because "nothing here" and "we could not ask" are different
facts and the user acts differently on each. A rejected edit comes back as the
editor with the value still in it, marked `cell--error` and carrying the reason in
its `title`, which is how core marks one (`ui/routes/inventory.py:2587`); a
toast on its own vanishes and leaves the refused value looking accepted. A list a
user cannot load is not a list they should be told is empty.

## Calling Celerp, shipped files, and AI

`celerp.modules.api` is the public surface for module code, with three helpers:

- `api_request(request, method, path, *, json=None, params=None)` calls Celerp's
  API as the signed-in user, for a path inside Celerp only
  (`celerp/modules/api.py:24`). Every page here reaches the API through it
  (`acme-maintenance/acme_maintenance/ui_routes.py:110`); never build a client of
  your own or read Celerp's address. It takes a JSON body or query parameters, so
  this module uploads a file as base64 in JSON.
- `read_resource(__file__, "relative/path")` reads a file the module ships, from
  the caller's own folder or below (`celerp/modules/api.py:51`). It checks the
  calling file, so call it from the module file whose `__file__` you pass; this
  module reads its print rule at import time
  (`acme-maintenance/acme_maintenance/ui_routes.py:96`).
- `ai_query(query, company_id, session_token=None, db_session=None)` runs an AI
  query through Celerp's AI service, for the request's company and a role allowed
  to use the AI assistant (`celerp/modules/api.py:78`). It is the only way a module
  uses AI.

Three rules follow, and they decide whether a module can be listed:

- A module that makes network calls of its own is reviewed by a person before the
  Community catalog admits it, and must declare those calls. Reach Celerp through
  `api_request` and the question does not arise.
- A module that runs AI inference itself, through a provider SDK, a model server,
  or a model address the user configures, cannot be approved. Use `ai_query`.
- Celerp installs no dependencies for a module. Use only what Celerp already
  ships; a `requirements.txt` anywhere in the module is a lint problem.

## What you may not import

Module code must not import `celerp.session_gate`, `celerp.ai` or anything under
it, `celerp.gateway`, `celerp.connectors`, or `celerp.credentials`. Those are
licensed internals, and the loader refuses to load a module that reaches into them,
matching each name and everything beneath it (`celerp/modules/loader.py:88`) - not
a warning, the module simply does not start. Use `celerp.modules.api` instead.
Everything else in `celerp.services` and `ui.components` is fair game, and this
module uses both.

## What is checked mechanically

Three of these rules are enforced, so a mistake surfaces before a restart rather
than in front of a user:

- Rule 1 and rule 5 are checked by `lint.py:768` and `lint.py:317`, the slot
  and table rules above by `lint.py:546` and `lint.py:880`, the package name rule by
  `lint.py:861`, and the route module and migrations rules by `lint.py:722` and
  `lint.py:748`. `tests/test_core_parity.py`
  loads each case through Celerp's own loader and fails wherever lint and the loader
  disagree. Run
  `python lint.py acme-maintenance` (or your renamed folder) before every restart.
- Rule 2 is checked by a test that renders every view and fails on any class core
  neither styles nor emits: `acme-maintenance/tests/test_render.py:344`.
- The protected-import rule above is checked by `lint.py` as well (`lint.py:264`),
  so you find out before a restart rather than from a module that will not load,
  and so is a shipped `requirements.txt`. `tests/test_core_parity.py` also checks
  that the three helpers keep the signatures this module calls them with.
- The citations in this file are checked too, so guidance that has drifted from the
  code fails a test instead of quietly misleading the next reader.

Everything else is checked by the module's own test suite, which runs against a real
SQLite database and the real API app with nothing mocked in between. Start there
when you change behaviour: write the test that fails first.

## Citation anchors

Each `path:line` above, with the exact text of that line. Paths under `celerp/` and `ui/` are in Celerp 2.5.4, at the commit `.github/workflows/ci.yml` pins. When code moves, the test that checks these fails, and both the citation and its anchor are updated together.

- `acme-maintenance/acme_maintenance/routes.py:57`: `require_permission("view_inventory")])`
- `acme-maintenance/acme_maintenance/ui_routes.py:110`: `r = await api_request(request, method, url, json=json, params=params)`
- `acme-maintenance/acme_maintenance/ui_routes.py:548`: `return HTMLResponse(to_xml(block),`
- `acme-maintenance/acme_maintenance/ui_routes.py:96`: `CALENDAR_PRINT_CSS = read_resource(__file__, "resources/calendar-print.css").decode("utf-8")`
- `acme-maintenance/tests/test_render.py:344`: `def test_all_emitted_classes_exist_in_core_css(env):`
- `celerp/main.py:309`: `await conn.run_sync(Base.metadata.create_all)`
- `celerp/modules/api.py:24`: `async def api_request(`
- `celerp/modules/api.py:51`: `def read_resource(module_file: str, relative_path: str) -> bytes:`
- `celerp/modules/api.py:78`: `async def ai_query(`
- `celerp/modules/importer.py:253`: `def reserved_tables(name: str) -> frozenset[str]:`
- `celerp/modules/importer.py:327`: `def table_prefix_problem(name: str, prefix: object,`
- `celerp/modules/importer.py:86`: `def _validate_name(name: str, *, official: bool = False) -> None:`
- `celerp/modules/loader.py:1321`: `def _drop_tables(names: set[str]) -> None:`
- `celerp/modules/loader.py:1727`: `route_mod_path = manifest.get(manifest_key)`
- `celerp/modules/loader.py:1791`: `def _stray_table_problem(manifest: dict) -> str | None:`
- `celerp/modules/loader.py:2319`: `_HANDLER_KEYWORDS = {`
- `celerp/modules/loader.py:2444`: `_SLOT_ENTRY_KEYS: dict[str, dict[str, tuple[tuple[type, ...], bool]]] = {`
- `celerp/modules/loader.py:2474`: `def _validate_slot_entry(slot: str, item) -> None:`
- `celerp/modules/loader.py:2501`: `if key in item and not is_permission_key(item[key]):`
- `celerp/modules/loader.py:2712`: `def _check_owned_callable(`
- `celerp/modules/loader.py:626`: `def module_migration_files(pkg_path: Path, migrations_pkg) -> list[Path]:`
- `celerp/modules/loader.py:656`: `def _check_route_source(pkg_path: Path, manifest: dict, kind: str) -> None:`
- `celerp/modules/loader.py:772`: `def _check_import_names(name: str, pkg_path: Path) -> None:`
- `celerp/modules/loader.py:88`: `_PROTECTED_BSL_INTERNALS: frozenset[str] = frozenset({`
- `celerp/modules/migrations_runner.py:167`: `async def run_migration_phase(engine, admission: loader.Admission) -> loader.Admission:`
- `celerp/services/app_paths.py:12`: `def is_app_local_path(path) -> bool:`
- `celerp/services/permissions.py:56`: `PERMISSIONS: list[Permission] = [`
- `lint.py:264`: `def _protected_imports(py_file: Path) -> set[str]:`
- `lint.py:317`: `def _ignored_parts(manifest: dict) -> list[str]:`
- `lint.py:546`: `def _slot_problems(manifest: dict, folder: Path) -> list[str]:`
- `lint.py:722`: `def _route_problems(manifest: dict, folder: Path) -> list[str]:`
- `lint.py:748`: `def _migrations_problems(manifest: dict, folder: Path) -> list[str]:`
- `lint.py:768`: `def _str_rendered_fragments(py_file: Path) -> list[str]:`
- `lint.py:861`: `def _import_name_problems(folder: Path) -> list[str]:`
- `lint.py:880`: `def _table_prefix_problems(manifest: dict) -> list[str]:`
- `ui/components/files.py:72`: `def files_section(`
- `ui/components/shell.py:2376`: `def _allowed(item: dict) -> bool:`
- `ui/components/shell.py:2571`: `def page_header(title: str, *actions: FT) -> FT:`
- `ui/components/table.py:1159`: `def display_cell(`
- `ui/components/table.py:938`: `def editable_cell(`
- `ui/routes/inventory.py:2587`: `edit_td.attrs["class"] = (edit_td.attrs.get("class", "") + " cell--error").strip()`
