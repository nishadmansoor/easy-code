"""User persistence."""

from app.core.base import BaseRepository
from app.users.models import User


class UserRepository(BaseRepository):
    """Looks up users by email or identifier."""

    def find_by_email(self, email):
        """Return the user with this email address, or None."""
        for row in self.all():
            if row.email == email:
                return row
        return None

    def find_by_id(self, user_id):
        """Return the user with this identifier, or None."""
        for row in self.all():
            if row.user_id == user_id:
                return row
        return None

    def add(self, user_id, email, password_hash):
        """Store a new user."""
        user = User(user_id, email, password_hash)
        self.rows.append(user)
        return user
