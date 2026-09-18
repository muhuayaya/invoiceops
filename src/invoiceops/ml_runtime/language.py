from __future__ import annotations

import re


_CJK = re.compile(r"[\u4e00-\u9fff]")
_LATIN = re.compile(r"[A-Za-z]")


def detect_language(text: str) -> str:
    has_cjk = bool(_CJK.search(text))
    has_latin = bool(_LATIN.search(text))
    if has_cjk and has_latin:
        return "mixed"
    if has_cjk:
        return "zh"
    if has_latin:
        return "en"
    return "unknown"
