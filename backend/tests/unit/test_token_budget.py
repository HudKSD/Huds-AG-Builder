from app.utils.token_budget import trim_sections


def test_trim_sections():
    sections = [('a', 'x'*1000), ('b', 'y'*1000)]
    trimmed = trim_sections(sections, 50)
    assert len(trimmed) >= 1
    assert len(trimmed[0][1]) <= 1000
