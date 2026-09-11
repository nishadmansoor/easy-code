"""Session issuing and revocation."""

import uuid

from app.config import settings


class SessionManager:
    """Creates and revokes user sessions."""

    def __init__(self):
        self.sessions = {}

    def create(self, user_id):
        """Issue a new session token for a user."""
        token = uuid.uuid4().hex
        self.sessions[token] = {"user_id": user_id, "ttl": settings.session_ttl}
        return token

    def revoke(self, token):
        """Invalidate an existing session token."""
        return self.sessions.pop(token, None) is not None
