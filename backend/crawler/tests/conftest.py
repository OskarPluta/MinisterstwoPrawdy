"""Fixture'y testów offline crawlera: lokalne serwery na trzech różnych hostach.

Limit zapytań, wycofanie i stan robots.txt są liczone per host, więc zwykły serwer, serwer zawsze
odpowiadający 429 i serwer z robots.txt odpowiadającym 500 muszą mieć różne nazwy hostów.
Wszystkie trzy wskazują na loopback na Linuksie i macOS bez dodatkowej konfiguracji (są w LOCAL_HOSTS).
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from . import local_server


@pytest.fixture(scope="session")
def servers():
    """base: 127.0.0.1 (zwykły), limited: localhost (429), r5: [::1] (robots.txt 500)."""
    srv = local_server.start("127.0.0.1")
    srv2 = local_server.start("localhost")
    srv3 = local_server.start("::1")
    assert local_server.ROBOTS_5XX_HOST == "[::1]"
    yield SimpleNamespace(
        base=f"http://127.0.0.1:{srv.server_address[1]}",
        limited=f"http://localhost:{srv2.server_address[1]}",
        r5=f"http://[::1]:{srv3.server_address[1]}",
    )
    for s in (srv, srv2, srv3):
        s.shutdown()
        s.server_close()


@pytest.fixture
def fresh_server(servers):
    """Wyzerowane liczniki żądań i domyślna treść stron (przywracane też po teście)."""
    local_server.reset()
    yield servers
    local_server.reset()


@pytest.fixture(scope="session")
def chromium() -> None:
    """Pomija testy wymagające przeglądarki, gdy Chromium dla Playwright nie jest zainstalowany."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        path = p.chromium.executable_path
    if not Path(path).exists():
        pytest.skip("Brak Chromium dla Playwright: uv run playwright install chromium")
