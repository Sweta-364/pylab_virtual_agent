import os
import unittest
from unittest.mock import patch

from PIL import Image

import action
import conversation_manager


class _FakeImageHandler:
    def __init__(self, output_path):
        self.output_path = output_path

    def convert_text_to_handwritten_image(self, _text):
        Image.new("RGB", (4, 4), color=(255, 255, 255)).save(self.output_path, format="JPEG")
        return self.output_path


class VoiceToResponseTests(unittest.TestCase):
    def setUp(self):
        conversation_manager.reset_history()

    def tearDown(self):
        conversation_manager.reset_history()

    @patch("action.ollama_handler.query_ollama", return_value="Here is your poem.")
    def test_createfile_keyword_starts_handwriting_generation(self, _mock_query):
        image_path = os.path.join(os.path.dirname(__file__), "image", "test_handwriting.jpg")
        if os.path.exists(image_path):
            os.remove(image_path)

        try:
            with patch("action._get_image_handler", return_value=_FakeImageHandler(image_path)):
                result = action.Action("createfile poem", speak_response=False)
                record = conversation_manager.wait_for_pending_operation(
                    result.operation_id,
                    timeout_seconds=2.0,
                    poll_interval=0.1,
                )

            self.assertIsNotNone(result.operation_id)
            self.assertEqual(record.get("status"), "success")
            self.assertTrue(os.path.isfile(image_path))
            self.assertEqual(record.get("display_path"), "./image/test_handwriting.jpg")
        finally:
            if os.path.exists(image_path):
                os.remove(image_path)

    @patch("action._generate_handwritten_image_async")
    @patch("action.ollama_handler.query_ollama", return_value="Normal response.")
    def test_create_file_phrase_does_not_trigger_exact_createfile_keyword(self, _mock_query, mock_generate):
        result = action.Action("create file abc", speak_response=False)

        self.assertIsNotNone(result)
        mock_generate.assert_not_called()


if __name__ == "__main__":
    unittest.main()
