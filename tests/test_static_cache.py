import hashlib
import pytest
from test_ui_templates import ui_client  # noqa: F401


@pytest.mark.parametrize('filename', ['app.css', 'tokens.css', 'components.css'])
def test_only_fingerprinted_public_assets_are_cached(ui_client, filename):
    asset = ui_client.get('/static/' + filename)
    assert asset.headers['Cache-Control'] == 'no-store'
    version = hashlib.sha256(asset.data).hexdigest()[:16]
    cached = ui_client.get('/static/' + filename + '?v=' + version)
    assert cached.status_code == 200
    assert cached.headers['Cache-Control'] == 'public, max-age=31536000, immutable'
    assert 'Set-Cookie' not in cached.headers
    assert ui_client.get('/static/' + filename + '?v=obsolete').headers['Cache-Control'] == 'no-store'
    for route in ['/', '/settings', '/logs', '/api/status', '/login']:
        assert ui_client.get(route).headers['Cache-Control'] == 'no-store'


def test_font_css_uses_actual_content_fingerprints(ui_client):
    css = ui_client.get('/static/fonts.css').data.decode()
    for family in ['Inter', 'NotoSansSC', 'JetBrainsMono']:
        font = ui_client.get('/static/fonts/' + family + '.woff2')
        version = hashlib.sha256(font.data).hexdigest()[:16]
        assert 'fonts/' + family + '.woff2?v=' + version in css
        assert 'immutable' in ui_client.get('/static/fonts/' + family + '.woff2?v=' + version).headers['Cache-Control']
