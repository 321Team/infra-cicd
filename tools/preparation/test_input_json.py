import os
from pathlib import Path
import tempfile
import unittest

from input_json import read_record


class InputJSONTests(unittest.TestCase):
    def test_duplicate_keys_and_nonfinite_numbers_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'input.json'
            for raw in ('{"result":"FAIL","result":"PASS"}', '{"n":NaN}', '{"n":Infinity}'):
                path.write_text(raw)
                with self.subTest(raw=raw), self.assertRaises(ValueError): read_record(path)

    def test_symlink_and_fifo_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp); (base / 'input').write_text('{}')
            (base / 'link').symlink_to(base / 'input'); os.mkfifo(base / 'fifo')
            with self.assertRaises(OSError): read_record(base / 'link')
            with self.assertRaises(ValueError): read_record(base / 'fifo')

    def test_oversized_file_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'input'; path.write_bytes(b' ' * 1048577)
            with self.assertRaises(ValueError): read_record(path)


if __name__ == '__main__':
    unittest.main()
