# SPDX-License-Identifier: MIT
"""The module's REST surface: what it stores, what it refuses, and who may call it.

`env.api` talks to the API app directly, which is where validation and permissions
live. `env.get`/`env.post` talk to the UI app, for the handful of behaviours that
belong to the proxy layer (a redirect after create, a 401 turned into a login hop).
"""
from __future__ import annotations

import uuid
from datetime import date, timedelta

API = "/api/maintenance/equipment"


def _id(env, name="Lathe", **kw):
    return env.equipment(name, **kw)


# ── create ────────────────────────────────────────────────────────────────────

def test_create_blank_returns_204_and_redirect(env):
    """A6: Add lands the user on the new record, not on a broken fragment."""
    r = env.post("/maintenance/create-blank")
    assert r.status_code == 204, r.text
    target = r.headers["HX-Redirect"]
    rows = env.rows("acme_equipment")
    assert len(rows) == 1
    assert target == f"/maintenance/{rows[0]['id']}"


# ── field validation ──────────────────────────────────────────────────────────

def test_serviced_date_future_rejected(env):
    """A12: a service cannot have happened tomorrow."""
    eq = _id(env)
    future = (date.today() + timedelta(days=1)).isoformat()
    r = env.api.patch(f"{API}/{eq}/field/serviced_at", json={"value": future})
    assert r.status_code == 422
    assert "future" in r.json()["detail"].lower()


def test_serviced_date_unparseable_rejected(env):
    """A29: a date field that accepts "soon" stores nothing anyone can read."""
    eq = _id(env)
    r = env.api.patch(f"{API}/{eq}/field/serviced_at", json={"value": "soon"})
    assert r.status_code == 422
    assert "YYYY-MM-DD" in r.json()["detail"]


def test_last_cost_negative_rejected(env):
    """A30: a negative service cost is a typo, not a refund."""
    eq = _id(env)
    r = env.api.patch(f"{API}/{eq}/field/last_cost", json={"value": "-5"})
    assert r.status_code == 422
    assert "negative" in r.json()["detail"].lower()
    assert float(env.rows("acme_equipment")[0]["last_cost"]) == 0


def test_location_not_in_company_list_rejected(env, make_env):
    """A31: the select offers the company's locations, so the API accepts only those.

    A company with no locations configured is the exception: there is nothing to
    check a value against, so the field stays free text rather than rejecting
    everything the user can type.
    """
    eq = _id(env)
    r = env.api.patch(f"{API}/{eq}/field/location", json={"value": "Mars"})
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert "Main plant" in detail and "Warehouse" in detail

    listless = make_env(locations=[])
    free = listless.equipment("Lathe")
    ok = listless.api.patch(f"{API}/{free}/field/location", json={"value": "Back shed"})
    assert ok.status_code == 200, ok.text
    assert listless.rows("acme_equipment")[0]["location"] == "Back shed"


def test_instructions_over_limit_rejected(env):
    """A32: the instructions box has a stated ceiling, enforced server side."""
    eq = _id(env)
    r = env.api.patch(f"{API}/{eq}/field/instructions", json={"value": "x" * 4001})
    assert r.status_code == 422
    assert "4000" in r.json()["detail"]
    ok = env.api.patch(f"{API}/{eq}/field/instructions", json={"value": "x" * 4000})
    assert ok.status_code == 200


# ── mark serviced, the log, and undo ──────────────────────────────────────────

def test_mark_serviced_twice_writes_one_log_row(env):
    """A21: marking an item serviced twice in a day is one service, not two."""
    eq = _id(env)
    for _ in range(2):
        r = env.api.post(f"{API}/mark-serviced", json={"ids": [eq]})
        assert r.status_code == 200, r.text
    assert len(env.rows("acme_service_log", equipment_id=eq)) == 1


def test_undo_last_service_restores_previous_date(env):
    """A21: removing the newest entry falls back to the one before it (GDR 2a)."""
    eq = _id(env)
    older = date.today() - timedelta(days=200)
    env.service_log(eq, serviced_at=older)
    newest = env.service_log(eq, serviced_at=date.today())
    r = env.api.delete(f"{API}/{eq}/service-log/{newest}")
    assert r.status_code == 200, r.text
    detail = env.api.get(f"{API}/{eq}").json()
    assert detail["item"]["serviced_at"] == older.isoformat()


def test_undo_non_newest_log_entry_rejected(env):
    """A34: history is not free-form; only the latest entry can be taken back."""
    eq = _id(env)
    oldest = env.service_log(eq, serviced_at=date.today() - timedelta(days=200))
    env.service_log(eq, serviced_at=date.today())
    r = env.api.delete(f"{API}/{eq}/service-log/{oldest}")
    assert r.status_code == 422
    assert "most recent" in r.json()["detail"].lower()


def test_mark_serviced_empty_ids_rejected(env):
    """A33: an empty selection is a mistake to report, not a silent no-op."""
    r = env.api.post(f"{API}/mark-serviced", json={"ids": []})
    assert r.status_code == 422
    assert "select" in r.json()["detail"].lower()


def test_mark_serviced_skips_malformed_id(env):
    """A39: one bad id in a batch must not throw away the good ones."""
    eq = _id(env)
    r = env.api.post(f"{API}/mark-serviced", json={"ids": [eq, "not-a-uuid"]})
    assert r.status_code == 200, r.text
    assert r.json() == {"updated": 1, "skipped": 1}
    assert len(env.rows("acme_service_log", equipment_id=eq)) == 1


def test_serviced_at_column_is_gone(env):
    """A22: the service log is the single source of the last-serviced date."""
    from acme_maintenance.models import Equipment
    assert "serviced_at" not in Equipment.__table__.columns
    eq = _id(env)
    assert env.api.get(f"{API}/{eq}").json()["item"]["serviced_at"] is None
    env.service_log(eq, serviced_at=date(2026, 7, 1))
    assert env.api.get(f"{API}/{eq}").json()["item"]["serviced_at"] == "2026-07-01"


# ── archive and restore ───────────────────────────────────────────────────────

def test_archive_then_restore(env):
    """A20: nothing is destroyed, and what leaves the list can come back (GDR 2a)."""
    eq = _id(env)
    assert env.api.post(f"{API}/{eq}/archive").status_code == 200
    assert [i["id"] for i in env.api.get(API).json()["items"]] == []
    archived = env.api.get(f"{API}?show=archived").json()["items"]
    assert [i["id"] for i in archived] == [eq]
    assert env.api.post(f"{API}/{eq}/restore").status_code == 200
    assert [i["id"] for i in env.api.get(API).json()["items"]] == [eq]
    assert len(env.rows("acme_equipment")) == 1, "archive deleted the row"


# ── permissions ───────────────────────────────────────────────────────────────

def test_viewer_cannot_write(make_env):
    """A24: reading the maintenance list never implies changing it."""
    env = make_env(role="viewer")
    eq = env.equipment("Lathe")
    assert env.api.get(API).status_code == 200
    for method, url, kw in (
        ("post", API, {"json": {"name": "New"}}),
        ("patch", f"{API}/{eq}/field/name", {"json": {"value": "Renamed"}}),
        ("post", f"{API}/mark-serviced", {"json": {"ids": [eq]}}),
        ("post", f"{API}/{eq}/archive", {}),
        ("post", f"{API}/{eq}/restore", {}),
    ):
        r = getattr(env.api, method)(url, **kw)
        assert r.status_code == 403, f"{method.upper()} {url} returned {r.status_code}"


def test_viewer_cannot_touch_files(make_env):
    """A44: the file routes are gated exactly like the rest of the writes."""
    env = make_env(role="viewer")
    eq = env.equipment("Lathe")
    up = env.api.post(f"{API}/{eq}/files",
                      files={"file": ("manual.txt", b"text", "text/plain")})
    assert up.status_code == 403
    rm = env.api.delete(f"{API}/{eq}/files/{env.company_id}")
    assert rm.status_code == 403


def test_other_company_cannot_download(env, make_env):
    """A44: a file id is not an access grant."""
    eq = env.equipment("Lathe")
    up = env.api.post(f"{API}/{eq}/files",
                      files={"file": ("manual.txt", b"text", "text/plain")})
    assert up.status_code == 200, up.text
    fid = up.json()["id"]
    intruder = env.equipment("Their lathe", company_id=env.other_company_id)
    assert env.api.get(f"{API}/{intruder}/files/{fid}/download").status_code == 404


def test_write_401_redirects_to_login(env):
    """A38: an expired session sends the user to log in, not into a silent refresh."""
    env.inject["/api/maintenance/equipment"] = 401
    r = env.post("/maintenance/create-blank")
    assert r.status_code == 401
    assert r.headers["HX-Redirect"] == "/login"
    page = env.get("/maintenance", htmx=False, follow_redirects=False)
    assert page.status_code == 302
    assert page.headers["location"] == "/login"


# ── files ─────────────────────────────────────────────────────────────────────

def test_file_upload_then_delete(env):
    """A15: the shared files section round-trips against the module's own routes."""
    eq = env.equipment("Lathe")
    up = env.post(f"/maintenance/{eq}/files",
                  files={"file": ("manual.txt", b"how to service it", "text/plain")})
    assert up.status_code == 200, up.text
    assert "manual.txt" in up.text
    fid = env.rows("acme_equipment_file", equipment_id=uuid.UUID(eq))[0]["id"]
    rm = env.delete(f"/maintenance/{eq}/files/{fid}")
    assert rm.status_code == 200, rm.text
    assert "manual.txt" not in rm.text
    assert env.rows("acme_equipment_file") == []


def test_upload_rejects_empty_oversize_and_bad_mime(env):
    """A16: three ways an upload can be wrong, each answered plainly."""
    eq = env.equipment("Lathe")
    cases = {
        "empty": ("empty.txt", b"", "text/plain"),
        "type": ("payload.exe", b"MZ", "application/x-msdownload"),
        "size": ("huge.txt", b"x" * (50 * 1024 * 1024 + 1), "text/plain"),
    }
    for label, payload in cases.items():
        r = env.api.post(f"{API}/{eq}/files", files={"file": payload})
        assert r.status_code == 422, f"{label}: got {r.status_code}"
    assert env.rows("acme_equipment_file") == []


def test_upload_storage_failure_returns_500(env, monkeypatch):
    """A35: a disk that will not take the bytes must not leave a row claiming it did."""
    import celerp.services.attachments as attachments

    async def _boom(*a, **kw):
        raise OSError("no space left on device")

    monkeypatch.setattr(attachments, "store_upload", _boom)
    eq = env.equipment("Lathe")
    r = env.api.post(f"{API}/{eq}/files",
                     files={"file": ("manual.txt", b"text", "text/plain")})
    assert r.status_code == 500
    assert r.json()["detail"] == "Upload failed, the file was not saved"
    assert env.rows("acme_equipment_file") == []


def test_download_streams_rather_than_redirects(env):
    """A41: the module serves the bytes; the storage path stays server side."""
    eq = env.equipment("Lathe")
    fid = env.api.post(f"{API}/{eq}/files",
                       files={"file": ("manual.txt", b"how to service it", "text/plain")}).json()["id"]
    r = env.api.get(f"{API}/{eq}/files/{fid}/download", follow_redirects=False)
    assert r.status_code == 200
    assert r.content == b"how to service it"


def test_download_missing_storage_key_404(env):
    """A36: an unknown file id is a 404 from the module's own route, saying what is wrong.

    The message matters: an unrouted URL 404s too, so a bare status check here would
    pass before the download route exists at all.
    """
    eq = env.equipment("Lathe")
    r = env.api.get(f"{API}/{eq}/files/{env.company_id}/download")
    assert r.status_code == 404
    assert r.json()["detail"] == "File not found"


def test_download_row_present_bytes_missing(env, tmp_path):
    """A40: a row whose bytes vanished is a distinct, honest 404."""
    eq = env.equipment("Lathe")
    fid = env.api.post(f"{API}/{eq}/files",
                       files={"file": ("manual.txt", b"bytes", "text/plain")}).json()["id"]
    for path in (tmp_path / "static" / "attachments").rglob("*"):
        if path.is_file():
            path.unlink()
    r = env.api.get(f"{API}/{eq}/files/{fid}/download")
    assert r.status_code == 404
    assert r.json()["detail"] == "File missing from storage"


def test_download_sanitises_filename_header(env):
    """A42: a filename is data, and data never becomes a second response header."""
    eq = env.equipment("Lathe")
    fid = env.api.post(f"{API}/{eq}/files",
                       files={"file": ("man\r\nual.txt", b"bytes", "text/plain")}).json()["id"]
    r = env.api.get(f"{API}/{eq}/files/{fid}/download")
    assert r.status_code == 200
    disposition = r.headers["content-disposition"]
    assert "\r" not in disposition and "\n" not in disposition
