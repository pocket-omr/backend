"""Teacher roster ingestion: parse the uploaded .xlsx into a match catalogue."""

from __future__ import annotations

import io

import pandas as pd

from app.recognition.fuzzy_match import CATALOGUE_FIELDS, build_catalogue


class RosterError(ValueError):
    """Raised when the uploaded roster is missing required columns."""

    def __init__(self, required: list[str], found: list[str]):
        self.required = required
        self.found = found
        super().__init__(
            f"Roster is missing required columns. Required: {required}; found: {found}"
        )


def _normalize_headers(columns) -> list[str]:
    """lowercase, trim, spaces -> underscores."""
    return [str(c).strip().lower().replace(" ", "_") for c in columns]


def parse_roster(file_bytes: bytes) -> tuple[list[dict], dict[str, str]]:
    """Parse an .xlsx roster into ``(catalogue, column_mapping)``.

    ``dtype=str`` is required to preserve leading zeros in registration_number
    and group. Raises :class:`RosterError` (-> HTTP 400) if a required column is
    missing.
    """
    df = pd.read_excel(io.BytesIO(file_bytes), dtype=str)

    original = list(df.columns)
    normalized = _normalize_headers(original)
    df.columns = normalized
    column_mapping = dict(zip(original, normalized))

    missing = [c for c in CATALOGUE_FIELDS if c not in df.columns]
    if missing:
        raise RosterError(required=CATALOGUE_FIELDS, found=normalized)

    # Drop fully-empty rows and blank NaNs -> "" so str() is clean.
    df = df.fillna("")
    catalogue = build_catalogue(df)
    return catalogue, column_mapping
