from app.agent.verifier import verify_answer


def test_verifier_catches_mismatch():
    ok, reason = verify_answer('Found 999 events', [{'result': {'total_hits': 12}}])
    assert not ok
    assert '999' in reason
