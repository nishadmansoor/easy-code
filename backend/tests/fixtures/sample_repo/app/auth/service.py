"""Authentication logic."""

from app.core.session import SessionManager
from app.users.repository import UserRepository


def hash_password(password):
    """Hash a plaintext password. Deliberately trivial for the fixture."""
    return f"hashed:{password}"


class AuthService:
    """Validates credentials and issues sessions."""

    def __init__(self, users: UserRepository, sessions: SessionManager):
        self.users = users
        self.sessions = sessions

    def login(self, email, password):
        """Authenticate a user and return a session token."""
        user = self.users.find_by_email(email)
        if user is None:
            return None
        if user.password_hash != hash_password(password):
            return None
        return self.sessions.create(user.user_id)

    def logout(self, token):
        """End a session."""
        return self.sessions.revoke(token)
