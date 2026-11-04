"""Small bilingual length heuristic; Chinese does not use word spaces."""

import re


def text_units(text: str) -> int:
    han = len(re.findall(r"[\u3400-\u9fff]", text or ""))
    other = re.sub(r"[\u3400-\u9fff，。！？、；：]", " ", text or "")
    return len(other.split()) + (han + 1) // 2
