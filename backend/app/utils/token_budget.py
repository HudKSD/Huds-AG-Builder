def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def trim_sections(sections: list[tuple[str, str]], budget: int) -> list[tuple[str, str]]:
    kept = []
    remaining = budget
    for name, content in sections:
        cost = estimate_tokens(content)
        if cost <= remaining:
            kept.append((name, content))
            remaining -= cost
        else:
            kept.append((name, content[: max(0, remaining * 4)]))
            break
    return kept
