#!/usr/bin/env python3
"""Collect existing Run35683010080/1 collect logs; information only, no rerun."""
import argparse
from datetime import datetime, timezone
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import pwd
import re
import socket
import stat
import tarfile
import tempfile


HOME_DIR = Path('/home/server01_cicd-runner')
ARCHIVE_SHA = '11578ae33211f569df91830a2b26314c01ffcaea70a67dc7165f9e3dad4b1c79'
CASE_NAME = re.compile(r'CICD_Evidence35683010080_1\.[A-Za-z0-9]{6}')
LOG_RESULT = {
    'EVIDENCE_CHECK': 'PASS', 'RECORDED_APP_WORKFLOW': 'BLOCK',
    'BINDING': 'COMPLETE', 'SOURCE_BINDING': 'MATCH',
    'RAW_PAYLOAD_CHECK': 'LOCAL_BYTES',
    'SOURCE_APPROVAL': 'NOT_INDEPENDENTLY_VERIFIED',
    'DELIVERY_GATE': 'NOT_VERIFIED',
}


class Stop(Exception):
    pass


def read_regular(path, limit):
    """Read bounded regular bytes with no symlinks in any path component."""
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts:
        raise Stop('UNSAFE_PATH')
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    directory = os.open('/', flags | os.O_DIRECTORY)
    try:
        for part in path.parts[1:-1]:
            child = os.open(part, flags | os.O_DIRECTORY, dir_fd=directory)
            os.close(directory)
            directory = child
        fd = os.open(path.name, flags, dir_fd=directory)
        with os.fdopen(fd, 'rb') as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
                raise Stop('UNSAFE_OR_OVERSIZED_FILE')
            raw = stream.read(limit + 1)
            after = os.fstat(stream.fileno())
        attrs = ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns')
        if len(raw) > limit or any(getattr(before, x) != getattr(after, x) for x in attrs):
            raise Stop('FILE_CHANGED')
        return raw
    finally:
        os.close(directory)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise Stop('DUPLICATE_JSON_KEY')
        result[key] = value
    return result


def choose_case(home, selected=None):
    if selected is not None:
        candidates = [Path(selected)]
        if candidates[0].parent != home or not CASE_NAME.fullmatch(candidates[0].name):
            raise Stop('CASE_OUTSIDE_ALLOWED_LOCATION')
    else:
        candidates = []
        with os.scandir(home) as entries:
            for count, entry in enumerate(entries, 1):
                if count > 4096:
                    raise Stop('HOME_ENTRY_LIMIT')
                if CASE_NAME.fullmatch(entry.name):
                    candidates.append(home / entry.name)
                    if len(candidates) > 32:
                        raise Stop('CASE_COUNT_LIMIT')
    matches = []
    for case in sorted(candidates):
        if case.is_symlink() or not stat.S_ISDIR(case.lstat().st_mode):
            raise Stop('UNSAFE_CASE')
        try:
            raw = read_regular(case / 'export/app-evidence.tar.gz', 1024 * 1024)
        except FileNotFoundError:
            continue
        if sha(raw) == ARCHIVE_SHA:
            matches.append(case)
    if len(matches) != 1:
        raise Stop('MATCH_NOT_UNIQUE')
    return matches[0]


def validate_record(name, raw, case):
    text = raw.decode('utf-8')
    if name == 'collect.log':
        lines = text.splitlines()
        if (len(lines) != 3 or
                lines[0] != 'ARCHIVE=' + str(case / 'export/app-evidence.tar.gz') or
                lines[1] != 'SHA256=' + ARCHIVE_SHA or
                json.loads(lines[2], object_pairs_hook=unique_object) != LOG_RESULT):
            raise Stop('UNEXPECTED_COLLECT_LOG')
        return None
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:[+-]\d{2}:\d{2}|Z)\n?', text):
        raise Stop('UNEXPECTED_TIME_RECORD')
    return datetime.fromisoformat(text.strip().replace('Z', '+00:00'))


def collect(home=HOME_DIR, selected=None):
    """Copy only known, bounded historical files into a new private directory."""
    case = choose_case(home, selected)
    files, missing, times = {}, [], {}
    for name in ('collect.log', 'started-at.txt', 'finished-at.txt'):
        try:
            raw = read_regular(case / name, 8192 if name == 'collect.log' else 128)
        except FileNotFoundError:
            missing.append(name)
            continue
        times[name] = validate_record(name, raw, case)
        files['original/' + name] = raw
    if ('started-at.txt' in times and 'finished-at.txt' in times and
            times['finished-at.txt'] < times['started-at.txt']):
        raise Stop('TIME_ORDER_INVALID')
    # Bind this collection to the already received archive, without re-running its verifier.
    if sha(read_regular(case / 'export/app-evidence.tar.gz', 1024 * 1024)) != ARCHIVE_SHA:
        raise Stop('ARCHIVE_CHANGED')
    for name, raw in files.items():
        if read_regular(case / Path(name).name, 8192) != raw:
            raise Stop('RECORD_CHANGED')
    state = 'COLLECTED_WITH_MISSING' if missing else 'COLLECTED'
    record = {
        'schema': 'P1-CICD-existing-collect-record-1',
        'scope': 'INFORMATION_ONLY_NOT_A_GATE_OR_APPROVAL',
        'tool_sha256': sha(read_regular(Path(__file__).absolute(), 1024 * 1024)),
        'collection_status': state,
        'recorded_at': datetime.now(timezone.utc).isoformat(),
        'collector_host': socket.gethostname(),
        'collector_account': pwd.getpwuid(os.getuid()).pw_name,
        'collector_uid': os.getuid(),
        'selection_mode': 'EXPLICIT_CASE' if selected is not None else 'AUTO_SINGLE_MATCH',
        'source_case': case.name,
        'run_id': '35683010080', 'attempt': 1,
        'previously_received_archive_sha256': ARCHIVE_SHA,
        'historical_collect_exit_code': 'NOT_RECORDED',
        'historical_terminal_screenshot': 'NOT_INCLUDED',
        'raw_app_payloads': 'NOT_READ',
        'app_execution': 'NOT_RUN', 'delivery_gate': 'NOT_VERIFIED',
        'missing_original_records': missing,
        'original_files': {n: {'bytes': len(b), 'sha256': sha(b)} for n, b in files.items()},
    }
    files['collection-record.json'] = (json.dumps(record, indent=2, sort_keys=True) + '\n').encode()
    files['SHA256SUMS'] = ''.join(sha(b) + '  ' + n + '\n' for n, b in sorted(files.items())).encode()
    # Writes start only after all inputs are checked. No existing output can be overwritten.
    output = Path(tempfile.mkdtemp(prefix='CICD_CollectRecord35683010080_1.', dir=home))
    archive = output / 'collect-record.tar.gz'
    with archive.open('xb') as stream, gzip.GzipFile(fileobj=stream, mode='wb', mtime=0) as gz:
        with tarfile.open(fileobj=gz, mode='w') as tar:
            for name, raw in sorted(files.items()):
                member = tarfile.TarInfo(name)
                member.size, member.mode = len(raw), 0o600
                tar.addfile(member, io.BytesIO(raw))
    checksum = sha(read_regular(archive, 65536))
    sidecar = output / 'collect-record.tar.gz.sha256'
    with sidecar.open('x', encoding='ascii') as stream:
        stream.write(checksum + '  collect-record.tar.gz\n')
    return archive, sidecar, state


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case-dir', type=Path, help='Optional original case directory if auto-selection is ambiguous')
    args = parser.parse_args(argv)
    os.umask(0o077)
    try:
        if (os.getuid() != 1000 or pwd.getpwuid(os.getuid()).pw_name != 'server01_cicd-runner' or
                socket.gethostname().split('.')[0] != 'cicd-runner01'):
            raise Stop('WRONG_HOST_OR_ACCOUNT')
        archive, sidecar, state = collect(HOME_DIR, args.case_dir)
        print('COLLECT_RECORD=' + state + '\nASSESSMENT=INFORMATION_ONLY')
        print('ARCHIVE=' + str(archive) + '\nSIDECAR=' + str(sidecar))
        return 0
    except Stop as error:
        print('COLLECT_RECORD=STOP\nREASON=' + str(error) + '\nACTION=PRESERVE_ORIGINALS_AND_RETURN_THIS_OUTPUT')
        return 1
    except (OSError, ValueError, TypeError, KeyError, RecursionError):
        # Never print arbitrary log contents, filenames from source JSON or raw exception text.
        print('COLLECT_RECORD=STOP\nREASON=INPUT_OR_OUTPUT_ERROR\nACTION=PRESERVE_ORIGINALS_AND_RETURN_THIS_OUTPUT')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
