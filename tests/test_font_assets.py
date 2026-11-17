from pathlib import Path

import pytest
from test_ui_templates import ui_client  # noqa: F401


@pytest.mark.parametrize("family", ["Inter", "NotoSansSC", "JetBrainsMono"])
def test_fonts_and_licenses_are_local_and_served(ui_client, family):
    response = ui_client.get(f"/static/fonts/{family}.woff2")
    assert response.status_code == 200
    assert response.data.startswith(b"wOF2")
    license_text = ui_client.get(f"/static/fonts/{family}-OFL.txt")
    assert license_text.status_code == 200
    assert b"SIL OPEN FONT LICENSE" in license_text.data
    css = ui_client.get("/static/fonts.css").data.decode()
    assert f"fonts/{family}.woff2" in css
    assert "https://" not in css and "base64" not in css
    assert "/static/fonts.css" in ui_client.get("/").data.decode()


def test_fonts_are_in_distribution_manifest():
    root = Path(__file__).resolve().parents[1]
    assert '"static/fonts/*"' in (root / "pyproject.toml").read_text(encoding="utf-8")
