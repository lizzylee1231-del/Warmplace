"""Run isolated tests without loading local secrets or calling external services."""
import unittest
from unittest.mock import patch

if __name__ == "__main__":
    with (
        patch("dotenv.load_dotenv", return_value=False),
        patch("supabase.create_client", return_value=None),
        patch("requests.sessions.Session.request", side_effect=AssertionError("Network disabled in tests")),
    ):
        result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.discover("tests"))
    raise SystemExit(not result.wasSuccessful())
