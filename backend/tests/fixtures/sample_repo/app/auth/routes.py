"""HTTP routes for authentication."""

from app.auth.service import AuthService


def login_route(request, service: AuthService):
    """Handle POST /login."""
    token = service.login(request.get("email"), request.get("password"))
    if token is None:
        return {"status": 401, "error": "invalid credentials"}
    return {"status": 200, "token": token}


def logout_route(request, service: AuthService):
    """Handle POST /logout."""
    ok = service.logout(request.get("token"))
    return {"status": 200 if ok else 404}
