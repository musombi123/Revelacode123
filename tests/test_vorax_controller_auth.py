import os
import unittest
from unittest.mock import patch

from scripts.vorax_controller import _has_kaggle_credentials


class KaggleCliAuthenticationTests(unittest.TestCase):
    def test_accepts_current_personal_api_token(self):
        with patch.dict(os.environ, {"KAGGLE_API_TOKEN": "token-value"}, clear=True):
            self.assertTrue(_has_kaggle_credentials())

    def test_keeps_legacy_key_compatibility(self):
        with patch.dict(os.environ, {"KAGGLE_KEY": "legacy-key"}, clear=True):
            self.assertTrue(_has_kaggle_credentials())

    def test_rejects_missing_credentials(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(_has_kaggle_credentials())


if __name__ == "__main__":
    unittest.main()
