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
    path this app can route (`/maintenance/42`), never an off-site or scheme
    URL; `subtitle` is optional secondary text. The aggregator validates every
    row against this shape and drops any that does not match, so return the
    canonical shape rather than your raw table columns.
  - Return an empty list on no match or on any failure: an empty result is
    honest, a fabricated one is not.

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
