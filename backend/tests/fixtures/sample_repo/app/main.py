"""Application entry point."""

from app.auth.routes import login_route, logout_route
from app.auth.service import AuthService
from app.core.session import SessionManager
from app.users.repository import UserRepository


def build_service():
    """Wire the application dependencies together."""
    users = UserRepository()
    sessions = SessionManager()
    return AuthService(users, sessions)


def handle(path, request):
    """Route one incoming request to its handler."""
    service = build_service()
    if path == "/login":
        return login_route(request, service)
    if path == "/logout":
        return logout_route(request, service)
    return {"status": 404}


def main():
    """Start the application."""
    return handle("/login", {"email": "a@example.com", "password": "secret"})
