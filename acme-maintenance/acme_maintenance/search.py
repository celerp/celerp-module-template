# SPDX-License-Identifier: MIT
"""Global-search provider for acme-maintenance.

The `search_provider` slot in PLUGIN_MANIFEST points at this module's
`global_search`. Celerp's aggregator calls it once per query, company-scoped and
gated by the manifest's permission key, and merges the rows it returns into the
one global search result. The aggregator caps the number of rows it keeps, so
the provider's only job is to find its own matches and hand them back.

The contract, both ends:

  - The aggregator calls `global_search(session, company_id, role, q, limit=5)`
    with the shared async session, the caller's company id, their role, the raw
    query text, and the per-provider row cap. The handler must be async.
  - The provider returns a dict whose `result_key` field (here "items", set in
    the manifest) is a list of canonical rows. Each row is a plain dict:
        {"id": str, "label": str, "href": str, "subtitle": str (optional)}
    `id` is your record's identifier; `label` is the text the search dropdown
    shows; `href` is an app-local link to the record and must be a single-slash
    path this app can route (`/maintenance/42`): it starts with one `/`, never
    `//`, and carries no backslash or control character, so it can only link
    within this app. `subtitle` is optional secondary text. A malformed row
    fails the aggregator's per-provider check and degrades this whole provider
    (all or nothing), so return only well-formed canonical rows, never your raw
    table columns.
  - Return an empty list only for a genuine no-match: the query ran and matched
    nothing. On an actual failure (an exception, an upstream error) raise and
    let it propagate: the aggregator marks this one provider degraded and still
    returns the other providers' results. Swallowing the error into an empty
    list hides the outage and reports "nothing here" for "we could not ask".

This template ships a read-only stub so the sample stays runnable without a
database. Fill in the query against your own tables, scoped to `company_id`, and
respect `limit`.
"""
from __future__ import annotations


async def global_search(session, company_id, role, q, limit=5):
    """Return this module's matches for *q*, company-scoped, capped at *limit*.

    Read-only and company-scoped: it queries only this module's own tables for
    the caller's company and never writes. The stub returns no rows; replace the
    body with a real lookup that maps each match to a canonical row (see the
    module docstring for the row shape) and keep the return shape.
    """
    return {"items": []}
