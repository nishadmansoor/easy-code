"""Shared repository abstractions."""


class BaseRepository:
    """Common behaviour for every data repository."""

    def __init__(self, rows=None):
        self.rows = rows or []

    def all(self):
        """Return every stored row."""
        return list(self.rows)

    def count(self):
        """Number of stored rows."""
        return len(self.rows)
