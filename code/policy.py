"""Policy decisions must come from supplied rules rather than model guesses."""

from __future__ import annotations


def unconditional_action(rules: tuple[dict[str, str], ...], category: str) -> str | None:
    """Only execute a rule if its condition is explicitly unconditional."""
    matching = [row["required_action"].upper() for row in rules
                if row["data_category"] == category and row["condition"].strip().casefold() == "always"]
    if len(matching) > 1 and len(set(matching)) > 1:
        raise ValueError(f"Conflicting deletion rules for {category}")
    return matching[0] if matching else None
