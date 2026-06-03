import json
import struct


def _png_dimensions(data: bytes) -> tuple[int, int]:
    assert data.startswith(b"\x89PNG\r\n\x1a\n")
    assert data[12:16] == b"IHDR"
    return struct.unpack(">II", data[16:24])


def test_manifest_webmanifest_is_installable_metadata(client):
    response = client.get("/manifest.webmanifest")

    assert response.status_code == 200
    assert response.mimetype == "application/manifest+json"
    assert response.headers["Cache-Control"] == "no-cache"

    payload = json.loads(response.get_data(as_text=True))
    assert payload["name"] == "TaskTrack"
    assert payload["short_name"] == "TaskTrack"
    assert payload["start_url"] == "/"
    assert payload["scope"] == "/"
    assert payload["display"] == "standalone"
    assert payload["theme_color"] == "#0f62fe"

    icons = {icon["sizes"]: icon for icon in payload["icons"]}
    assert icons["192x192"]["src"] == "/pwa-icon-192.png"
    assert icons["192x192"]["type"] == "image/png"
    assert icons["192x192"]["purpose"] == "any maskable"
    assert icons["512x512"]["src"] == "/pwa-icon-512.png"


def test_service_worker_is_root_served_without_data_caching(client):
    response = client.get("/service-worker.js")

    assert response.status_code == 200
    assert response.mimetype == "application/javascript"
    assert response.headers["Cache-Control"] == "no-cache"

    source = response.get_data(as_text=True)
    assert "self.skipWaiting()" in source
    assert "self.clients.claim()" in source
    assert "addEventListener(\"fetch\"" not in source
    assert "addEventListener('fetch'" not in source
    assert "caches" not in source.lower()


def test_pwa_icons_are_route_served_square_pngs(client):
    for size in (192, 512):
        response = client.get(f"/pwa-icon-{size}.png")

        assert response.status_code == 200
        assert response.mimetype == "image/png"
        assert response.headers["Cache-Control"] == "public, max-age=86400"
        assert _png_dimensions(response.data) == (size, size)

    assert client.get("/pwa-icon-128.png").status_code == 404


def test_authenticated_root_includes_pwa_head_and_registration(auth_client):
    response = auth_client.get("/")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert '<link rel="manifest" href="/manifest.webmanifest">' in html
    assert '<meta name="theme-color" content="#0f62fe">' in html
    assert '<link rel="apple-touch-icon" href="/pwa-icon-192.png">' in html
    assert 'navigator.serviceWorker.register("/service-worker.js", { scope: "/" })' in html
