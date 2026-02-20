import re


def terms(text: str) -> set[str]:
    return {x.lower() for x in re.findall(r'[a-zA-Z0-9_.-]+', text)}
