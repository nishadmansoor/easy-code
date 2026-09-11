"""Application configuration."""


class Settings:
    """Runtime settings for MiniApp."""

    def __init__(self, session_ttl: int = 3600, debug: bool = False):
        self.session_ttl = session_ttl
        self.debug = debug


settings = Settings()
