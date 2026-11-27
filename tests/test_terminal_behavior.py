from packages.core.terminal_behavior import auto_report_enabled, normalize


def test_terminal_behavior_defaults_preserve_existing_reports():
    assert normalize(None) == {"autoReportByErrorType": {}}
    assert auto_report_enabled("provider_rate_limited", None) is True


def test_terminal_behavior_explicit_structured_type_can_disable_report():
    policy = {"autoReportByErrorType": {"provider_rate_limited": False}}
    assert auto_report_enabled("provider_rate_limited", policy) is False
    assert auto_report_enabled("unknown", policy) is True
