import planner
from planner import Planner


def test_planner_falls_back_if_openai_client_init_typeerror(monkeypatch):
    class BrokenOpenAI:
        def __init__(self, *args, **kwargs):
            raise TypeError("Client.__init__() got an unexpected keyword argument 'proxies'")

    monkeypatch.setattr(planner, "OpenAI", BrokenOpenAI)

    p = Planner(api_key="dummy", model="gpt-4o-mini")
    out = p.plan("test", {"mode": "auto"}, [])
    assert p.client is None
    assert out.intent == "investigate_logs"
    assert "Fallback planner used" in out.why
