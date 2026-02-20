from app.rate_limit import _is_exempt_path


def test_rate_limit_exempts_health_and_metrics_paths():
    assert _is_exempt_path('/health')
    assert _is_exempt_path('/metrics')
    assert _is_exempt_path('/api/health')
    assert not _is_exempt_path('/api/chat')
