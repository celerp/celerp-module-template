# SPDX-License-Identifier: MIT
"""Files the module ships beside its code, read through Celerp's read_resource."""
from __future__ import annotations

from pathlib import Path

import acme_maintenance.ui_routes as ui_routes
from celerp.modules import api

SHIPPED = Path(ui_routes.__file__).parent / "resources" / "calendar-print.css"


def test_calendar_print_rule_is_the_file_the_module_ships(env):
    """The print rule lives in a shipped file, read once with read_resource."""
    assert ui_routes.read_resource is api.read_resource
    rule = SHIPPED.read_text(encoding="utf-8")
    assert ui_routes.CALENDAR_PRINT_CSS == rule
    assert rule.strip() in env.get("/maintenance?view=calendar&month=2026-08").text
