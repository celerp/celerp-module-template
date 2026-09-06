# SPDX-License-Identifier: MIT
"""Global-search provider for acme-maintenance.

The `search_provider` slot in PLUGIN_MANIFEST points at this module's
`global_search`. Celerp's aggregator calls it once per query, company-scoped and
gated by the manifest's permission key, and merges the rows it returns into the
one global search result. The aggregator caps the number of rows and stamps each
with this module's identity, so the provider's only job is to find its own
matches and hand them back.

The contract, both ends:

  - The aggregator calls `global_search(session, company_id, role, q, limit=5)`
    with the shared async session, the caller's company id, their role, the raw
    query text, and the per-provider row cap.
  - The provider returns a dict whose `result_key` field (here "items", set in
    the manifest) is a list of plain-dict rows. Return an empty list on no match
    or on any failure: an empty result is honest, a fabricated one is not.

This template ships a read-only stub so the sample stays runnable without a
database. Fill in the query against your own tables, scoped to `company_id`, and
respect `limit`.
"""
from __future__ import annotations


async def global_search(session, company_id, role, q, limit=5):
    """Return this module's matches for *q*, company-scoped, capped at *limit*.

    Read-only and company-scoped: it queries only this module's own tables for
    the caller's company and never writes. The stub returns no rows; replace the
    body with a real lookup and keep the shape.
    """
    return {"items": []}
