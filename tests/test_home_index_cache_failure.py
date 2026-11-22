import unittest
from unittest.mock import patch

from vorax import hf_runtime


class HomeIndexCacheFailureTests(unittest.TestCase):
    def test_live_home_index_remains_available_when_optional_vault_cache_write_fails(self):
        index = {
            "version": "vorax-home-method-index/v1",
            "methods_total": 30,
            "records": [],
        }
        with patch.object(hf_runtime, "HOME_READ_TOKEN", "github-read-token"), \
             patch.object(hf_runtime, "fetch_method_index", return_value=index), \
             patch.object(
                 hf_runtime,
                 "write_home_metadata_cache",
                 side_effect=RuntimeError("simulated HfHubHTTPError"),
             ), \
             patch.object(hf_runtime, "_log") as log, \
             patch.object(hf_runtime, "_HOME_INDEX", None), \
             patch.object(hf_runtime, "_HOME_INDEX_AT", 0.0), \
             patch.object(hf_runtime, "_HOME_INDEX_STATUS", {}):
            result, status = hf_runtime._refresh_home_index(force=True)

        self.assertEqual(result, index)
        self.assertEqual(status["status"], "metadata_index_read")
        self.assertEqual(status["methods_total"], 30)
        self.assertIs(status["stale"], False)
        self.assertEqual(status["vault_cache"], "unavailable")
        self.assertEqual(status["cache_error"], "RuntimeError")
        log.assert_called_once()
        self.assertEqual(log.call_args.args[0], "home_metadata_cache_write_failed")
        self.assertEqual(
            log.call_args.kwargs["verification"],
            "live_home_index_retained",
        )


if __name__ == "__main__":
    unittest.main()
