"""Normalize optional token identity without inventing missing metadata."""

PLACEHOLDER_TEXTS = frozenset({"unknown", "unknown token", "—", "?"})


def display_text(value, address=None):
    if not isinstance(value, str):
        return None
    value = value.strip()
    if (
        not value
        or value.lower() in PLACEHOLDER_TEXTS
        or value.lower() == str(address).lower()
    ):
        return None
    return value
