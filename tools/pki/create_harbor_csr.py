#!/usr/bin/env python3
"""Create a new Harbor-local key and export only a CSR and public receipt."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import pwd
import socket
import stat
import subprocess
import tarfile
import tempfile
from datetime import datetime, timezone

ENV = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C"}
PRIVATE_BASE = Path("/etc/harbor/pki/requests")
ARCHIVE = "P1-CICD-01_Harbor_CSR_Result.tar.gz"


def stderr_summary(stderr):
    """Return fixed diagnostic labels, never arbitrary command output."""
    value = stderr.decode("utf-8", errors="replace") if isinstance(stderr, bytes) else (stderr or "")
    lowered = value.lower()
    for phrase, label in (("permission denied", "PERMISSION_DENIED"),
                          ("no such file or directory", "PATH_NOT_FOUND"),
                          ("error loading", "CONFIG_OR_EXTENSION_LOAD_FAILED"),
                          ("verify failure", "VERIFICATION_FAILED")):
        if phrase in lowered:
            return label
    return "DETAIL_WITHHELD" if value else "EMPTY"


def command(args):
    try:
        result = subprocess.run(args, env=ENV, capture_output=True, timeout=120)
    except subprocess.TimeoutExpired:
        raise RuntimeError("command failed: " + args[0] + " reason=TIMEOUT") from None
    if result.returncode:
        raise RuntimeError("command failed: " + args[0] + " rc=" + str(result.returncode)
                           + " stderr_summary=" + stderr_summary(result.stderr))
    return result.stdout


def identity():
    if socket.gethostname().split(".")[0] != "harbor01":
        raise RuntimeError("target host must be harbor01")
    if os.geteuid() != 0 or os.environ.get("SUDO_USER") != "server08":
        raise RuntimeError("run with sudo from server08")
    account = pwd.getpwnam("server08")
    addresses = json.loads(command(["ip", "-j", "-4", "addr", "show"]))
    if not any(a.get("local") == "10.1.93.54" for n in addresses for a in n.get("addr_info", [])):
        raise RuntimeError("required local IPv4 address missing")
    return account


def root_directory(path):
    if not path.is_absolute():
        raise RuntimeError("absolute private path required")
    current = Path("/")
    for part in path.parts[1:]:
        current /= part
        try:
            current.mkdir(mode=0o700)
        except FileExistsError:
            pass
        s = current.lstat()
        if not stat.S_ISDIR(s.st_mode) or s.st_uid != 0 or s.st_mode & 0o022:
            raise RuntimeError("unsafe private directory: " + str(current))
    if path.stat().st_mode & 0o077:
        raise RuntimeError("private request directory must be mode 0700")


def public_parent(path, account):
    # An absolute, existing path in the login account's home, without symlinks.
    home = Path(account.pw_dir)
    if not path.is_absolute() or ".." in path.parts or not path.is_relative_to(home):
        raise RuntimeError("output directory must be inside server08 home")
    for p in [*reversed(path.parents), path]:
        s = p.lstat()
        if not stat.S_ISDIR(s.st_mode) or s.st_uid not in (0, account.pw_uid) or s.st_mode & 0o022:
            raise RuntimeError("unsafe output directory")


def export_public(directory, csr, receipt):
    # Explicit allowlist; never archive the private directory.
    (directory / "harbor01.csr.pem").write_bytes(csr)
    (directory / "receipt.json").write_text(json.dumps(receipt, indent=2, ensure_ascii=False) + "\n")
    payload = ["harbor01.csr.pem", "receipt.json"]
    sums = "".join(hashlib.sha256((directory / n).read_bytes()).hexdigest() + "  " + n + "\n" for n in payload)
    (directory / "SHA256SUMS").write_text(sums)
    archive = directory / ARCHIVE
    with tarfile.open(archive, "w:gz") as tar:
        for name in payload + ["SHA256SUMS"]:
            p = directory / name
            if not stat.S_ISREG(p.lstat().st_mode):
                raise RuntimeError("public artifact is not regular")
            tar.add(p, arcname=name, recursive=False)
    sidecar = directory / (ARCHIVE + ".sha256")
    sidecar.write_text(hashlib.sha256(archive.read_bytes()).hexdigest() + "  " + ARCHIVE + "\n")
    return archive, sidecar


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args(argv)
    if not args.apply:
        print("PLAN=NEW_HARBOR_LOCAL_RSA3072_KEY_AND_CSR\nCHANGES_APPLIED=false")
        return 0
    if args.output_dir is None:
        parser.error("--apply requires --output-dir")
    private = None
    phase = "preconditions"
    try:
        account = identity()
        public_parent(args.output_dir, account)
        config = Path(__file__).resolve().parents[2] / "pki/harbor-csr.cnf"
        if not config.is_file() or config.is_symlink():
            raise RuntimeError("CSR profile missing or symlinked")
        os.umask(0o077)
        root_directory(PRIVATE_BASE)
        private = Path(tempfile.mkdtemp(prefix="harbor01-", dir=PRIVATE_BASE))
        key = private / "harbor01.key"
        csr_path = private / "harbor01.csr.pem"
        phase = "key_generation"
        command(["openssl", "genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:3072", "-out", str(key)])
        key.chmod(0o600)
        phase = "csr_generation"
        command(["openssl", "req", "-new", "-sha256", "-key", str(key), "-config", str(config), "-out", str(csr_path)])
        phase = "csr_verification"
        command(["openssl", "req", "-in", str(csr_path), "-verify", "-noout"])
        csr_bytes = csr_path.read_bytes()
        receipt = {
            "schema": "P1-CICD-01-harbor-csr-1.0",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "host": "harbor01", "csr_signature": "VERIFIED",
            "private_key_local_path_reference_only": str(key),
            "private_key_exported": False,
            "key_profile": "RSA3072", "leaf_requested_validity_days": 365,
            "ip_sans": ["10.1.93.54"], "dns_sans": ["harbor.fds.internal", "harbor01"],
            "profile_sha256": hashlib.sha256(config.read_bytes()).hexdigest(),
            "changes_applied": True, "harbor_service_changed": False,
            "leaf_issuance": "NOT_EXECUTED", "delivery_gate": "NOT_VERIFIED"
        }
        # Finish privileged work before writing anything into the user's home.
        # Only the public CSR bytes are retained; the private key is never read.
        phase = "public_export"
        os.setgroups([])
        os.setgid(account.pw_gid)
        os.setuid(account.pw_uid)
        result_dir = Path(tempfile.mkdtemp(prefix="P1_CICD_Harbor_CSR_", dir=args.output_dir))
        archive, sidecar = export_public(result_dir, csr_bytes, receipt)
        for p in result_dir.iterdir():
            p.chmod(0o600)
        print("CSR=CREATED\nPRIVATE_KEY_EXPORTED=false\nHARBOR_SERVICE_CHANGED=false\nDELIVERY_GATE=NOT_VERIFIED")
        print("ARCHIVE=" + str(archive))
        print("SIDECAR=" + str(sidecar))
        return 0
    except (OSError, RuntimeError, subprocess.TimeoutExpired, ValueError, KeyError) as exc:
        print("ERROR=" + str(exc))
        print("FAILED_PHASE=" + phase)
        if private is not None:
            print("PRIVATE_REQUEST_DIR=" + str(private))
            print("PRIVATE_REQUEST_PRESERVED=true")
        print("DELIVERY_GATE=NOT_VERIFIED")
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
