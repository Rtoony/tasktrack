"""CSV-injection guard (audit #14 / CWE-1236).

Spreadsheet apps execute a cell that begins with = + - @ (or a leading tab/CR)
as a formula. Free-text we export (OCR transcriptions, user notes, names) can
therefore smuggle a formula into a downloaded report. Prefix a single quote to
neutralise those cells; everything else passes through unchanged.
"""
from __future__ import annotations

_DANGEROUS_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def csv_safe(value) -> str:
    s = "" if value is None else str(value)
    if s and s[0] in _DANGEROUS_PREFIXES:
        return "'" + s
    return s
