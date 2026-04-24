import os
import tempfile
import unittest
from pathlib import Path

import file_handler


class FileHandlerTests(unittest.TestCase):
    def test_delete_file_rejects_symlink(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            target = temp_path / "target.txt"
            target.write_text("secret", encoding="utf-8")
            symlink = temp_path / "link.txt"
            symlink.symlink_to(target)

            with self.assertRaises(PermissionError):
                file_handler.delete_file(str(symlink), require_confirm=False)

            self.assertTrue(target.exists())
            self.assertTrue(symlink.exists())

    def test_read_file_content_supports_streaming_for_large_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            large_file = Path(temp_dir) / "large.txt"
            large_content = "A" * (2 * 1024 * 1024)
            large_file.write_text(large_content, encoding="utf-8")

            streamed = file_handler.read_file_content(
                str(large_file),
                max_size_mb=1,
                streaming=True,
            )

            self.assertFalse(isinstance(streamed, str))
            self.assertEqual("".join(streamed), large_content)

    def test_batch_create_rolls_back_all_created_items_on_failure(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            existing_file = temp_path / "existing.txt"
            existing_file.write_text("already here", encoding="utf-8")

            actions = [
                {"create": "directory", "path": str(temp_path / "notes")},
                {"create": "file", "path": str(temp_path / "notes" / "todo.txt"), "content": "hello"},
                {"create": "file", "path": str(existing_file), "content": "collision"},
            ]

            result = file_handler._execute_batch_create(actions)

            self.assertIn("Batch create failed.", result)
            self.assertFalse((temp_path / "notes").exists())
            self.assertFalse((temp_path / "notes" / "todo.txt").exists())
            self.assertTrue(existing_file.exists())

    def test_create_and_update_file_preserve_written_content(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            file_path = Path(temp_dir) / "draft.txt"

            create_result = file_handler.create_file(str(file_path), content="hello")
            update_result = file_handler.update_file(str(file_path), "world")

            self.assertIn("Created file", create_result)
            self.assertIn("Updated file", update_result)
            self.assertEqual(file_path.read_text(encoding="utf-8"), "world")


if __name__ == "__main__":
    unittest.main()
