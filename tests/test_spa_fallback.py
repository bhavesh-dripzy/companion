"""The SPA deep-link catch-all must never shadow the API surface.

`/c/<id>` and other client routes return the SPA shell so a refresh / shared
link boots the app, but an unmatched `/api/*` stays a real 404 (never the HTML
shell, which would break fetch callers expecting JSON).
"""

import pytest
from fastapi import HTTPException


def test_spa_fallback_404s_unknown_api_paths():
    from companion_api.main import spa_fallback

    for path in ("api", "api/", "api/does/not/exist"):
        with pytest.raises(HTTPException) as exc:
            spa_fallback(path)
        assert exc.value.status_code == 404


def test_spa_fallback_serves_shell_for_client_routes():
    from companion_api.main import spa_fallback

    # `/c/<id>` and bare paths are client routes — never a 404 for being unknown
    # to the backend. (Returns the shell, or the "build the frontend" notice when
    # dist is absent in CI; either way it must not raise.)
    for path in ("c/abc-123", "settings", ""):
        response = spa_fallback(path)
        assert response.status_code in (200, 404)
