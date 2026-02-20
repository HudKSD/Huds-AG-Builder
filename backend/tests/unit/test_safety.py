import pytest
from app.es.safety import enforce_safe_mode, SafetyError


def test_safety_rejects_disallowed_query_type():
    with pytest.raises(SafetyError):
        enforce_safe_mode({'query': {'regexp': {'user.name': 'a.*'}}})
