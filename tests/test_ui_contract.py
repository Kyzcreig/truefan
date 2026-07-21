from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path):
    return (ROOT / path).read_text(encoding="utf-8")


def test_dashboard_has_required_monitoring_and_percent_controls():
    html = read("app/templates/index.html")

    assert 'name="viewport"' in html
    assert "External watchdog: authoritative" in html
    assert 'id="safety-state"' in html
    assert 'id="bmc-mode"' in html
    assert 'id="duty-percent"' in html
    assert 'id="temp-max-drive"' in html
    assert 'id="temp-cpu"' in html
    assert 'id="temp-avg-drive"' in html
    assert 'id="temp-nvme"' in html
    assert 'id="drive-grid"' in html
    assert 'id="fan-grid"' in html
    assert 'id="agent-state"' in html
    assert 'id="last-refresh"' in html
    assert 'id="write-token"' in html
    assert 'id="duty-slider"' in html
    assert 'min="22"' in html and 'max="100"' in html
    assert 'id="ttl-seconds"' in html
    assert 'data-profile="quiet"' in html
    assert 'data-profile="cooling"' in html
    assert 'data-profile="emergency"' in html
    assert 'id="control-result"' in html


def test_dashboard_uses_session_only_auth_and_structured_control_apis():
    javascript = read("app/static/js/dashboard.js")

    assert "sessionStorage" in javascript
    assert "localStorage" not in javascript
    assert "Authorization" in javascript
    assert "Bearer" in javascript
    assert '"/api/control"' in javascript
    assert "/api/profile/" in javascript
    assert "requested_duty" in javascript
    assert "effective_duty" in javascript
    assert "controls_locked" in javascript


def test_mobile_css_prevents_390px_horizontal_overflow():
    css = read("app/static/style.css")

    assert "overflow-x: hidden" in css
    assert "max-width: 100%" in css
    assert "@media (max-width: 480px)" in css
    assert "minmax(0, 1fr)" in css


def test_hidden_attribute_beats_class_display_rules():
    # Any element with the hidden attribute must actually hide: class rules like
    # .metric{display:flex} otherwise win on specificity over the UA [hidden] default.
    # Kept as a defensive default for future data-optional elements.
    css = read("app/static/style.css")

    assert "[hidden]" in css
    assert "display: none !important" in css


def test_dashboard_formats_missing_values_and_machine_reasons_for_people():
    javascript = read("app/static/js/dashboard.js")

    assert 'replaceAll("_", " ")' in javascript
    assert "value === null || value === undefined" in javascript


def test_avg_hdd_card_replaces_board_and_is_computed_client_side():
    # The Board card (board_c often null on this BMC) was replaced by an always-
    # populated Avg HDD card computed from the drives array /status already sends.
    html = read("app/templates/index.html")
    javascript = read("app/static/js/dashboard.js")

    assert 'id="temp-avg-drive"' in html
    assert 'id="temp-board"' not in html
    # averaged over spinning drives, NVMe excluded, coloured on drive thresholds
    assert "avgDrive" in javascript
    assert "nvme" in javascript.lower()
    assert 'temperature(elements.avgDrive, "max_drive_c"' in javascript
