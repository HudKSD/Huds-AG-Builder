from planner import PlannerOutput


def test_planner_output_schema_validation():
    payload = {
        "intent": "failed_login_investigation",
        "confidence": 0.8,
        "suggested_mode": "esql",
        "index_hint": "logs-*",
        "why": "User asked for simple listing",
    }
    parsed = PlannerOutput.model_validate(payload)
    assert parsed.intent == "failed_login_investigation"
    assert parsed.suggested_mode == "esql"
