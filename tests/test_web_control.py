from app.web import sensor_control_request_allowed


def test_sensor_control_requires_custom_header():
    assert not sensor_control_request_allowed("", "same-origin")
    assert not sensor_control_request_allowed("wrong", "same-origin")


def test_sensor_control_rejects_cross_site_requests():
    assert not sensor_control_request_allowed("sensor-control", "cross-site")
    assert sensor_control_request_allowed("sensor-control", "same-origin")
    assert sensor_control_request_allowed("sensor-control", "none")
