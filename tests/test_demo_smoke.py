"""The smoke test tells the app's own browser errors from those of Community Cloud's page around it
(its analytics, its /api/ calls for a signed-in user), which only the live link has."""

from tools.demo_smoke import cloud_own

LIVE = "https://lookahead.streamlit.app"


def test_cloud_s_own_requests_are_told_apart_from_the_app_s() -> None:
    assert cloud_own("https://cdn.heapanalytics.com/js/heap-.js", LIVE)
    assert cloud_own(f"{LIVE}/api/v1/app/event/open", LIVE)
    assert cloud_own(f"{LIVE}/api/v2/user/details", LIVE)
    assert not cloud_own(f"{LIVE}/~/+/_stcore/stream", LIVE)  # the app's own connection
    assert not cloud_own(f"{LIVE}/~/+/static/js/index.js", LIVE)
    assert not cloud_own(f"{LIVE}/Forecast", LIVE)
    assert not cloud_own("", LIVE)  # an error with no address counts
