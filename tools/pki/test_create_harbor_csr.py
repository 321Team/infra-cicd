import importlib.util
import contextlib
import io
import json
import os
from pathlib import Path
import stat
import tarfile
import tempfile
import types
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("csr", Path(__file__).with_name("create_harbor_csr.py"))
csr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(csr)


class Safety(unittest.TestCase):
    def test_command_failure_reports_rc_and_safe_stderr_only(self):
        result = types.SimpleNamespace(returncode=7, stdout=b"SENTINEL_STDOUT",
                                       stderr=b"Permission denied: password=SENTINEL_SECRET")
        with patch.object(csr.subprocess, "run", return_value=result):
            with self.assertRaises(RuntimeError) as error:
                csr.command(["openssl", "req"])
        self.assertIn("rc=7", str(error.exception))
        self.assertIn("PERMISSION_DENIED", str(error.exception))
        self.assertNotIn("SENTINEL", str(error.exception))

    def test_timeout_does_not_disclose_partial_output(self):
        error = csr.subprocess.TimeoutExpired(["openssl"], 120, stderr=b"SENTINEL_PRIVATE")
        with patch.object(csr.subprocess, "run", side_effect=error):
            with self.assertRaises(RuntimeError) as caught:
                csr.command(["openssl"])
        self.assertIn("TIMEOUT", str(caught.exception))
        self.assertNotIn("SENTINEL", str(caught.exception))

    def test_failed_generation_preserves_request_and_reports_location(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            output = io.StringIO()
            def fail_after_write(args):
                Path(args[-1]).write_text("SENTINEL_INCOMPLETE_KEY")
                raise RuntimeError("simulated failure")
            with patch.object(csr, "identity", return_value=object()), patch.object(csr, "public_parent"), patch.object(csr, "root_directory"), patch.object(csr, "PRIVATE_BASE", base), patch.object(csr, "command", side_effect=fail_after_write), patch.object(csr, "export_public") as export, contextlib.redirect_stdout(output):
                self.assertEqual(csr.main(["--apply", "--output-dir", str(base)]), 3)
            keys = list(base.glob("harbor01-*/harbor01.key"))
            self.assertEqual(len(keys), 1)
            self.assertIn("PRIVATE_REQUEST_DIR=" + str(keys[0].parent), output.getvalue())
            self.assertIn("FAILED_PHASE=key_generation", output.getvalue())
            self.assertNotIn("SENTINEL", output.getvalue())
            export.assert_not_called()

    def test_plan_does_not_read_identity_or_write(self):
        with patch.object(csr, "identity", side_effect=AssertionError), patch.object(csr, "root_directory", side_effect=AssertionError):
            self.assertEqual(csr.main([]), 0)

    def test_wrong_host_stops_before_filesystem_changes(self):
        with patch.object(csr.socket, "gethostname", return_value="other"), patch.object(csr, "public_parent", side_effect=AssertionError), patch.object(csr, "root_directory", side_effect=AssertionError):
            self.assertEqual(csr.main(["--apply", "--output-dir", "/unwritten"]), 3)

    def test_wrong_account_stops_before_ip_query(self):
        with patch.object(csr.socket, "gethostname", return_value="harbor01"), patch.object(csr.os, "geteuid", return_value=0), patch.dict(csr.os.environ, {"SUDO_USER": "other"}), patch.object(csr, "command", side_effect=AssertionError):
            with self.assertRaises(RuntimeError):
                csr.identity()

    def test_ip_mismatch_rejected(self):
        with patch.object(csr.socket, "gethostname", return_value="harbor01"), patch.object(csr.os, "geteuid", return_value=0), patch.dict(csr.os.environ, {"SUDO_USER": "server08"}), patch.object(csr.pwd, "getpwnam", return_value=object()), patch.object(csr, "command", return_value=b'[{"addr_info":[{"local":"10.1.93.55"}]}]'):
            with self.assertRaises(RuntimeError):
                csr.identity()

    def test_output_escape_rejected(self):
        a = types.SimpleNamespace(pw_dir="/home/server08", pw_uid=1001)
        for p in ["/tmp/out", "/home/server08/../other", "relative"]:
            with self.subTest(p=p), self.assertRaises(RuntimeError):
                csr.public_parent(Path(p), a)

    def test_symlink_private_path_rejected(self):
        link = types.SimpleNamespace(st_mode=stat.S_IFLNK | 0o777, st_uid=0)
        with patch.object(csr.Path, "mkdir"), patch.object(csr.Path, "lstat", return_value=link):
            with self.assertRaises(RuntimeError):
                csr.root_directory(Path("/symlink"))

    def test_archive_excludes_private_material_and_matches_hashes(self):
        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            marker = b"TEST_PRIVATE_MATERIAL_DO_NOT_EXPORT"
            (d / "harbor01.key").write_bytes(marker)
            archive, sidecar = csr.export_public(d, b"TEST_CSR_PUBLIC_ONLY", {"private_key_exported": False})
            with tarfile.open(archive) as tar:
                self.assertEqual(sorted(tar.getnames()), ["SHA256SUMS", "harbor01.csr.pem", "receipt.json"])
                for member in tar:
                    self.assertNotIn(marker, tar.extractfile(member).read())
                for line in tar.extractfile("SHA256SUMS").read().decode().splitlines():
                    digest, name = line.split("  ")
                    self.assertEqual(csr.hashlib.sha256(tar.extractfile(name).read()).hexdigest(), digest)
            self.assertEqual(sidecar.read_text().split()[0], csr.hashlib.sha256(archive.read_bytes()).hexdigest())

    def test_privileges_dropped_before_public_output_without_reading_key(self):
        with tempfile.TemporaryDirectory() as t:
            base = Path(t)
            private, out = base / "private", base / "out"
            private.mkdir()
            out.mkdir()
            account = types.SimpleNamespace(pw_uid=1234, pw_gid=2345)
            events = []
            real_export, real_read = csr.export_public, Path.read_bytes

            def fake_command(args):
                if "genpkey" in args:
                    Path(args[-1]).write_text("TEST_KEY_ONLY")
                elif "-out" in args:
                    Path(args[-1]).write_text("TEST_PUBLIC_CSR")
                return b""

            def safe_read(path):
                if path.suffix == ".key":
                    raise AssertionError("private key must not be read into exporter")
                return real_read(path)

            def exporting(*args):
                self.assertEqual(events, ["groups", "gid", "uid"])
                return real_export(*args)

            with patch.object(csr, "identity", return_value=account), patch.object(csr, "public_parent"), patch.object(csr, "root_directory"), patch.object(csr, "PRIVATE_BASE", private), patch.object(csr, "command", side_effect=fake_command), patch.object(csr.os, "setgroups", side_effect=lambda _: events.append("groups")), patch.object(csr.os, "setgid", side_effect=lambda _: events.append("gid")), patch.object(csr.os, "setuid", side_effect=lambda _: events.append("uid")), patch.object(csr.Path, "read_bytes", safe_read), patch.object(csr, "export_public", side_effect=exporting):
                self.assertEqual(csr.main(["--apply", "--output-dir", str(out)]), 0)


if __name__ == "__main__":
    unittest.main()
