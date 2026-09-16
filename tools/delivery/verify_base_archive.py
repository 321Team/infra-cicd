#!/usr/bin/env python3
"""Check supplied OCI bytes against independently recorded identities; no import."""
import argparse
import json
from pathlib import Path
import re
import stat
import sys
import tarfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools' / 'trivy'))
from oci_archive import inspect_archive  # noqa: E402
from prepare_db import PreflightError, require, utcnow  # noqa: E402
from verify_runner import sha256  # noqa: E402

MAX_ARCHIVE_BYTES = 1024 * 1024 * 1024
HEX = r'[0-9a-f]{64}'


def signature(path):
    value = path.lstat()
    require(stat.S_ISREG(value.st_mode), 'ARCHIVE_REGULAR_FILE_REQUIRED')
    require(0 < value.st_size <= MAX_ARCHIVE_BYTES, 'ARCHIVE_SIZE_UNSUPPORTED')
    return tuple(getattr(value, key) for key in (
        'st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns'))


def verify(path, expected_archive_sha256, expected_manifest, expected_config):
    for value, prefix in ((expected_archive_sha256, ''),
                          (expected_manifest, 'sha256:'), (expected_config, 'sha256:')):
        require(isinstance(value, str) and re.fullmatch(prefix + HEX, value),
                'EXPECTED_IDENTITIES_REQUIRED')
    before = signature(path)
    require(sha256(path) == expected_archive_sha256, 'ARCHIVE_TRANSPORT_HASH_MISMATCH')
    identity = inspect_archive(path)
    require(identity['archive_sha256'] == expected_archive_sha256,
            'ARCHIVE_TRANSPORT_HASH_MISMATCH')
    require(identity['manifest_digest'] == expected_manifest, 'BASE_MANIFEST_MISMATCH')
    require(identity['config_digest'] == expected_config, 'BASE_CONFIG_MISMATCH')
    require(signature(path) == before and sha256(path) == expected_archive_sha256,
            'ARCHIVE_CHANGED')
    return {'BASE_ARCHIVE_CHECK': 'PASS', **identity,
            'archive_bytes': before[2], 'referenced_layer_count': len(identity['diff_ids']),
            'REFERENCED_BLOB_HASHES': 'PASS'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', type=Path, required=True)
    parser.add_argument('--expected-archive-sha256', required=True)
    parser.add_argument('--expected-manifest', required=True)
    parser.add_argument('--expected-config', required=True)
    args = parser.parse_args(argv)
    result = {'schema': 'P1-CICD-base-archive-check-1', 'BASE_ARCHIVE_CHECK': 'FAIL',
              'verified_at': utcnow().isoformat(), 'NETWORK': 'NOT_EXECUTED',
              'IMAGE_IMPORT': 'NOT_EXECUTED', 'LAYER_DIFFID_CONTENT': 'NOT_VERIFIED',
              'REGISTRY_PROVENANCE': 'NOT_VERIFIED', 'SUPPLY_APPROVAL': 'NOT_VERIFIED',
              'PYTHON_DEPENDENCIES': 'NOT_VERIFIED', 'IMAGE_SCAN': 'NOT_EXECUTED',
              'DELIVERY_GATE': 'NOT_VERIFIED'}
    rc = 1
    try:
        result.update(verify(args.archive, args.expected_archive_sha256,
                             args.expected_manifest, args.expected_config))
        rc = 0
    except PreflightError as exc:
        result['error_code'] = str(exc)
    except (ValueError, UnicodeError, RecursionError, TypeError, KeyError):
        result['error_code'] = 'ARCHIVE_STRUCTURE_INVALID'
    except (OSError, tarfile.TarError, EOFError):
        result['error_code'] = 'ARCHIVE_READ_FAILED'
    print(json.dumps(result, indent=2, sort_keys=True))
    return rc


if __name__ == '__main__':
    raise SystemExit(main())
