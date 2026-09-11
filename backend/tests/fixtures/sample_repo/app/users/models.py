"""User domain models."""


class User:
    """A registered account."""

    def __init__(self, user_id, email, password_hash):
        self.user_id = user_id
        self.email = email
        self.password_hash = password_hash

    def describe(self):
        """Human readable label for logs."""
        return f"User({self.user_id}, {self.email})"
