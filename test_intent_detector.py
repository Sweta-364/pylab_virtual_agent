import unittest
from unittest.mock import patch

import intent_detector


class IntentDetectorTests(unittest.TestCase):
    def tearDown(self):
        intent_detector.clear_cache()

    @patch("intent_detector.ollama_handler.query_ollama")
    def test_detect_os_intent_uses_confidence_threshold(self, mock_query):
        mock_query.return_value = '{"is_os_operation": true, "confidence": 0.92}'

        result = intent_detector.detect_os_intent("list files on desktop")

        self.assertTrue(result["is_os_operation"])
        self.assertAlmostEqual(result["confidence"], 0.92)
        self.assertEqual(result["source"], "ollama")

    @patch("intent_detector.ollama_handler.query_ollama")
    def test_detect_os_intent_rejects_low_confidence_classifier_output(self, mock_query):
        mock_query.return_value = '{"is_os_operation": true, "confidence": 0.35}'

        result = intent_detector.detect_os_intent("maybe organize something")

        self.assertFalse(result["is_os_operation"])
        self.assertTrue(result["raw_is_os_operation"])
        self.assertAlmostEqual(result["confidence"], 0.35)

    @patch("intent_detector.ollama_handler.query_ollama", side_effect=RuntimeError("ollama offline"))
    def test_detect_os_intent_avoids_false_positive_for_file_space_phrase(self, _mock_query):
        result = intent_detector.detect_os_intent("create file space for storage")

        self.assertFalse(result["is_os_operation"])
        self.assertFalse(result["raw_is_os_operation"])
        self.assertEqual(result["source"], "heuristic")
        self.assertAlmostEqual(result["confidence"], 0.5)


if __name__ == "__main__":
    unittest.main()
