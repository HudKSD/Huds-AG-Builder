import re


def verify_answer(answer: str, tool_results: list[dict]) -> tuple[bool, str]:
    nums = re.findall(r'\b\d+\b', answer)
    corpus = str(tool_results[-3:])
    for n in nums:
        if n not in corpus:
            return False, f'Number {n} not found in recent tool results'
    return True, ''
