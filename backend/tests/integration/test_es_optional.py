import os
import pytest


@pytest.mark.skipif(not os.getenv('ES_URL'), reason='ES_URL not set')
def test_es_optional():
    assert True
