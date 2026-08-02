"""Shared text normalization helpers for matching and quality analysis."""

import unicodedata


def normalize_search_text(value: object) -> str:
    """Return case-folded, accent-insensitive text with normalized whitespace."""
    text = unicodedata.normalize("NFKD", str(value or "").strip().casefold())
    return " ".join(
        "".join(
            character for character in text
            if not unicodedata.combining(character)
        ).split()
    )