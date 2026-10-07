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

**If you are a chat assistant working through a GitHub connector** (ChatGPT or
Claude in a browser), you cannot run commands, so the steps below change in these
ways:

- A connector cannot create a repository, so the person creates theirs from this
  template on GitHub and gives you its address. Work in that repository through the
  connector: write every file in full, and commit to its default branch. Never
  create another repository.
- GitHub runs lint and the tests on every commit. Read the result through the
  connector; if a check failed, read its log, fix the cause, and commit again. Never
  say a check passed unless you read the green result, and never skip a failure.
- A connector cannot fork either, so the person forks `celerp/community-modules`
  on GitHub and gives you the fork's address. Through the connector, create a
  branch in the fork from its `main`, change `index.json` only, and open the pull
  request into `celerp/community-modules` (step 6). If the connector cannot open a
  pull request across forks, give the person
  `https://github.com/celerp/community-modules/compare/main...<owner>:<fork>:<branch>?expand=1`
  with the names filled in; they open it and click **Create pull request**.
- Never hand over a zip or ask the person to upload files. If the connector cannot
  do a step, follow step 9.

1. **Make it theirs.** Agree a short id: lowercase letters, digits and hyphens. It
   must not start with `celerp`, look like Celerp, or match the name or id of a
   module already in
   <https://github.com/celerp/community-modules/blob/main/index.json>
   (`acme-maintenance` is taken by this template). Rename `acme-maintenance/` to the
   id and the inner package `acme_maintenance/` to the id with underscores. The
   folder you replace must be deleted, not left beside the new one: the checks find
   the one folder whose `__init__.py` defines `PLUGIN_MANIFEST` and stop if there
   are two. Then rename everything that still carries the template's names:
   - the manifest `name`, `api_routes` and `ui_routes`, imports, and the tests;
   - `table_prefix` (`acme_`), to a prefix built from the new id, and every
     `__tablename__` in `models.py` to start with it;
   - the table names inside the files in `migrations/`;
   - every table key in `company_backup`;
   - the nav entry's `key` and `href` (`maintenance`, `/maintenance`) and every
     link to them;
   - this README's title and text.

   Two modules that share a table prefix cannot both be installed, so a module that
   keeps `acme_` clashes with every other one that did. Set `display_name`,
   `description`, `version` and `author`, then replace the equipment code with what
   was asked for, following the eight rules below.
2. **Say what it touches.** The module's README states which data it reads and
   writes and which network calls it makes ("None." when it makes none); the
   catalog entry repeats both. Keep the MIT `LICENSE` unless the person picks
   another, and put their name or GitHub account in its copyright line. The
   manifest `license` must be the SPDX id of that file.
3. **Check it.** Run `python lint.py <id>`, `pytest tests`, and the module suite
   exactly as `.github/workflows/ci.yml` runs it. Fix every failure; never skip
   one. If you cannot run commands (a chat assistant), commit and read the CI
   result on that commit instead, as described above.
4. **Publish it.** The repository must be public. Commit to its default branch
   (usually `main`): a listing points at a commit on that branch, so work left on
   another branch cannot be listed. If you can only push a branch, open a pull
   request into the default branch of the person's own repository, ask them to
   press **Merge**, and continue from the merged commit.
5. **Build the zip, if they want to try it first** (terminal only). From the
   repository root, `zip -r <id>.zip <id>/`. In Celerp, **Modules**, then
   **Installed Modules**, then **Import Module** takes that zip. A chat assistant
   skips this step: the person installs the module from the catalog once it is
   listed.
6. **Submit it.** The submission is a pull request to `celerp/community-modules`
   opened from the GitHub account that owns the module's repository. It changes
   `index.json` only; the catalog's `README.md` is rebuilt after merge.
   - Take the full 40-character id of the commit at the head of the default branch
     (terminal: `git rev-parse HEAD` after the push, and check it matches GitHub;
     chat: read it through the connector).
   - Fork `celerp/community-modules` into the person's account (terminal:
     `gh repo fork celerp/community-modules --clone`; chat: the person forks it on
     GitHub) and branch from its `main`.
   - Add one entry to `index.json` with `id`, `name`, `description`,
     `"tier": "community"`, `repo` (`https://github.com/<owner>/<repository>`),
     `commit`, `author`, `license`, `data_access` and `network_calls`, and no
     `version`. Official entries stay first; community entries sort by `id`.
     From a terminal, `python3 scripts/validate_index.py` checks it.
   - Commit `index.json`, and open the pull request against `main`. Give the
     person its link.
7. **Follow it through.** An automatic listing check posts one comment on the pull
   request and keeps it current. When every check passes, the pull request is
   merged automatically and the module appears in the Community Modules tab of
   every Celerp on its next catalog refresh. A comment headed "Listing check:
   changes needed" lists what to fix: fix the module, commit it to the default
   branch, and set `commit` in the same pull request's `index.json` to the new head;
   the check runs again. "Listing check: waiting for the maintainer" means
   something in the code was flagged for a person to look at; tell them what, and
   wait.
8. **New versions.** Raise `version` in the manifest, commit to the default branch,
   and submit an update the same way: first bring the fork's `main` up to date
   with `celerp/community-modules` `main` (GitHub's **Sync fork**; with a chat, the
   person clicks it), branch from it,
   set the existing entry's `commit` to the new full commit id, and open a new pull
   request from the same account. Celerp only offers the listed commit, so every
   version is a new `commit` in the entry.
9. **If you cannot do a step** (the connector cannot commit, read the checks, or
   create the branch), do not stop at "done" and do not hand over files.
   Tell the person which step you could not do and why, and stop there.

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
(`ui/components/shell.py:2601`) puts search and actions in the header at the house
size. `display_cell` (`ui/components/table.py:1175`) and `editable_cell`
(`ui/components/table.py:954`) give you double-click-to-edit, ESC to cancel,
save-on-blur, and `--` for an empty value, all pointed at your own routes through
`patch_url` and `edit_url`. `files_section` (`ui/components/files.py:72`) renders
the house files block against any `base_url`. Every one of these is a place where a
hand-rolled version would drift from the rest of the app the first time core changes.

**4. Gate reads and writes with a permission key, and use an existing one.**
Permission keys are a closed registry (`celerp/services/permissions.py:56`), and
the loader refuses a module whose slot entries name a key outside it, in either
`permission` or `write_permission` (`celerp/modules/loader.py:2607`), so a module
cannot invent one today. Pick the
key that matches what the page does. The API router depends on `require_permission`
(`acme-maintenance/acme_maintenance/routes.py:57`) and the sidebar hides an entry
whose `permission` the role does not have (`ui/components/shell.py:2408`); the page
asks the same question so a viewer is never offered a control that would only fail.
Hiding a control is presentation, never protection: the router is what stops a
hand-made request.

**5. Ask for the manifest keys Celerp reads, and nothing else.** An unknown key is
not an error to the loader, it is ignored, so a misspelled gate ships wide open in
silence. `min_role` is the classic: it looks like it gates the nav entry and it does
nothing at all. There is no `icon` key either. The loader reads route modules by
name (`celerp/modules/loader.py:1833`), and `lint.py` holds the full list of accepted
keys. `api_routes` and `ui_routes` each name a file inside the module that defines
its own `setup_api_routes` or `setup_ui_routes` (`celerp/modules/loader.py:690`). The
manifest `name` must equal the module's folder name, start with a letter or digit, and
hold only letters, digits, `-` and `_`, 64 characters at most
(`celerp/modules/importer.py:89`). Every top-level manifest field must also hold the one type Celerp reads it as.
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
(`celerp/modules/loader.py:2580`). A module may fill every slot in `lint.PUBLIC_SLOTS`;
`inventory_in_production` is filled by Celerp's own modules only. Each entry is a
dict, and carries what the code reading its slot takes from it, in the type it
reads it as (`celerp/modules/loader.py:2550`): a `nav` `order` is a number, a
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
this module's own files, async exactly where Celerp awaits it (`celerp/modules/loader.py:2818`).
The `item_lineage_guard` handler is called with keyword arguments only, so it
takes exactly `session, entry, transition`: no other parameter, none
positional-only, and no `*args` or `**kwargs` (`celerp/modules/loader.py:2425`).

The tables a module creates must start with its `table_prefix`: at least 3
characters, ending in `_`, and no table Celerp keeps for itself may start with it,
so `label_`, `marketplace_` and `bank_` are taken (`celerp/modules/importer.py:256`). Two installed
modules' prefixes may not overlap either: neither prefix may be a prefix of the
other (`celerp/modules/importer.py:330`). Only the installation knows the other
modules, so `lint.py` checks every rule here except that last one. A module whose
code defines a table outside its prefix, or any table without one, is taken out
before any table is created (`celerp/modules/loader.py:1897`).

A module's package names are its own. The module folder, and each package or
importable file (source, compiled or extension) directly inside it, is a name
Python imports it by, so none of them may
be a name Python, Celerp, an installed package or another module already uses: no `json.py`, no
`ui/` package, nothing starting `celerp_` (`celerp/modules/loader.py:806`).

**6. New tables come from your models; changes to shipped tables come from
migrations.** Module models register on Celerp's shared metadata when the loader
imports them, and Celerp runs `create_all` after loading modules
(`celerp/main.py:448`), so a new table appears on the next launch. A module that
fails to load, or whose routes fail to register, gets none of its tables created
(`celerp/modules/loader.py:1412`). `create_all`
cannot alter an existing table, so any change to a table you have shipped is a
migration. Celerp runs every file in the manifest's `migrations` package at each
start, in filename order, before the module loads
(`celerp/modules/migrations_runner.py:169`), and only from a package inside the module
folder (`celerp/modules/loader.py:660`). It keeps no version record, so each
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
its `title`, which is how core marks one (`ui/routes/inventory.py:2630`); a
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

- Rule 1 and rule 5 are checked by `lint.py:1134` and `lint.py:662`, the slot
  and table rules above by `lint.py:891` and `lint.py:1244`, the package name rule by
  `lint.py:1225`, and the route module and migrations rules by `lint.py:1034` and
  `lint.py:1058`. `tests/test_core_parity.py`
  loads each case through Celerp's own loader and fails wherever lint and the loader
  disagree. Run
  `python lint.py acme-maintenance` (or your renamed folder) before every restart.
- Rule 2 is checked by a test that renders every view and fails on any class core
  neither styles nor emits: `acme-maintenance/tests/test_render.py:344`.
- The protected-import rule above is checked by `lint.py` as well (`lint.py:610`),
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
- `celerp/main.py:448`: `await create_tables(lifecycle_engine)`
- `celerp/modules/api.py:24`: `async def api_request(`
- `celerp/modules/api.py:51`: `def read_resource(module_file: str, relative_path: str) -> bytes:`
- `celerp/modules/api.py:78`: `async def ai_query(`
- `celerp/modules/importer.py:256`: `def reserved_tables(name: str) -> frozenset[str]:`
- `celerp/modules/importer.py:330`: `def table_prefix_problem(name: str, prefix: object,`
- `celerp/modules/importer.py:89`: `def _validate_name(name: str, *, official: bool = False) -> None:`
- `celerp/modules/loader.py:1412`: `def _drop_tables(names: set[str]) -> None:`
- `celerp/modules/loader.py:1833`: `route_mod_path = manifest.get(manifest_key)`
- `celerp/modules/loader.py:1897`: `def _stray_table_problem(manifest: dict) -> str | None:`
- `celerp/modules/loader.py:2425`: `_HANDLER_KEYWORDS = {`
- `celerp/modules/loader.py:2550`: `_SLOT_ENTRY_KEYS: dict[str, dict[str, tuple[tuple[type, ...], bool]]] = {`
- `celerp/modules/loader.py:2580`: `def _validate_slot_entry(slot: str, item) -> None:`
- `celerp/modules/loader.py:2607`: `if key in item and not is_permission_key(item[key]):`
- `celerp/modules/loader.py:2818`: `def _check_owned_callable(`
- `celerp/modules/loader.py:660`: `def module_migration_files(pkg_path: Path, migrations_pkg) -> list[Path]:`
- `celerp/modules/loader.py:690`: `def _check_route_source(pkg_path: Path, manifest: dict, kind: str) -> None:`
- `celerp/modules/loader.py:806`: `def _check_import_names(name: str, pkg_path: Path) -> None:`
- `celerp/modules/loader.py:88`: `_PROTECTED_BSL_INTERNALS: frozenset[str] = frozenset({`
- `celerp/modules/migrations_runner.py:169`: `async def run_migration_phase(engine, admission: loader.Admission) -> loader.Admission:`
- `celerp/services/app_paths.py:12`: `def is_app_local_path(path) -> bool:`
- `celerp/services/permissions.py:56`: `PERMISSIONS: list[Permission] = [`
- `lint.py:1034`: `def _route_problems(manifest: dict, folder: Path) -> list[str]:`
- `lint.py:1058`: `def _migrations_problems(manifest: dict, folder: Path) -> list[str]:`
- `lint.py:1134`: `def _str_rendered_fragments(py_file: Path) -> list[str]:`
- `lint.py:1225`: `def _import_name_problems(folder: Path) -> list[str]:`
- `lint.py:1244`: `def _table_prefix_problems(manifest: dict) -> list[str]:`
- `lint.py:610`: `def _protected_imports(py_file: Path) -> set[str]:`
- `lint.py:662`: `def _ignored_parts(manifest: dict) -> list[str]:`
- `lint.py:891`: `def _slot_problems(manifest: dict, folder: Path) -> list[str]:`
- `ui/components/files.py:72`: `def files_section(`
- `ui/components/shell.py:2408`: `def _allowed(item: dict) -> bool:`
- `ui/components/shell.py:2601`: `def page_header(title: str, *actions: FT) -> FT:`
- `ui/components/table.py:1175`: `def display_cell(`
- `ui/components/table.py:954`: `def editable_cell(`
- `ui/routes/inventory.py:2630`: `edit_td.attrs["class"] = (edit_td.attrs.get("class", "") + " cell--error").strip()`
