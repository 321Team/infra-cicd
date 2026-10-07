#!/usr/bin/env python3
"""Stage the verified adc90410 supply as private files. Never load/run images."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import socket
import stat
import sys
import zipfile

SOURCE = 'adc90410f99cb7af5b40bc8ff92c45272e9f8bee'
ZIP_SHA = '2eaed6486b68f9260f735e362b45a167d7f01aa0fa819accf751ea8d74951ad6'
MANIFEST_SHA = 'f4d55db6ecbefe2057cdb7ca6539b6818549744eb8bfc127ba12dcfa4eb6143b'
HOME = Path('/home/server01_cicd-runner')
NAME = 'FDS_CICD_ApkoSupply.adc90410f99cb.f4d55db6ecbe'

def require(condition, message):
    if not condition:
        raise ValueError(message)

def sha(stream):
    return hashlib.file_digest(stream, 'sha256').hexdigest()

def safe_name(name):
    require(bool(re.fullmatch(r'[A-Za-z0-9_./+!-]+', name)), 'Unsafe archive path')
    require(not name.startswith('/') and all(p not in ('', '.', '..') for p in name.split('/')), 'Noncanonical path')
    return name

def verify(stream):
    require(os.fstat(stream.fileno()).st_size == 109786891, 'Archive size differs')
    stream.seek(0)
    require(sha(stream) == ZIP_SHA, 'Archive SHA256 differs')
    stream.seek(0)
    archive = zipfile.ZipFile(stream)
    entries = archive.infolist()
    require(len(entries) == 126 and len({e.filename for e in entries}) == 126, 'File count/duplicate differs')
    require(sum(e.file_size for e in entries) <= 1024**3, 'Expanded size bound')
    for entry in entries:
        safe_name(entry.filename)
        require(entry.orig_filename == entry.filename and not entry.is_dir() and not entry.flag_bits & 1,
                'Nonregular/encrypted entry')
        require(stat.S_IFMT(entry.external_attr >> 16) in (0, stat.S_IFREG), 'Link/special file')
    require(hashlib.sha256(archive.read('SUPPLY_MANIFEST.json')).hexdigest() == MANIFEST_SHA, 'Manifest differs')
    manifest = json.loads(archive.read('SUPPLY_MANIFEST.json'))
    require(manifest['source'] == manifest['supply_tool'] == {'repository':'321Team/fds-msa','commit':SOURCE}, 'Source differs')
    checks = {}
    for line in archive.read('SHA256SUMS').decode().splitlines():
        match = re.fullmatch(r'([a-f0-9]{64})  (.+)', line)
        require(match is not None, 'Invalid checksum line')
        digest, name = match.groups()
        safe_name(name)
        require(name not in checks, 'Duplicate checksum')
        checks[name] = digest
    require(set(checks) == {e.filename for e in entries} - {'SHA256SUMS'}, 'Coverage differs')
    for name, digest in checks.items():
        with archive.open(name) as member:
            require(sha(member) == digest, 'Member hash differs: ' + name)
    return archive, checks, sum(e.file_size for e in entries)

def identity():
    require(sys.platform.startswith('linux') and os.geteuid() == os.getuid() == 1000, 'Use normal UID1000 login')
    account = pwd.getpwuid(os.getuid())
    require(account.pw_name == 'server01_cicd-runner' and Path(account.pw_dir) == HOME, 'Account/home differs')
    require(os.environ.get('HOME') == str(HOME) and not os.environ.get('SUDO_USER'), 'Use original login, no sudo')
    require(socket.gethostname().split('.')[0] == 'cicd-runner01', 'Wrong host')
    return account

def directory(path, allowed_owner):
    """Open each ancestor without following links; return an owned directory FD."""
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for component in Path(path).parts[1:]:
            child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
            info = os.fstat(fd)
            require(info.st_uid in (0, allowed_owner) and info.st_mode & 0o022 == 0, 'Untrusted parent ownership/mode')
        return fd
    except BaseException:
        os.close(fd)
        raise

def stage(archive, checks, parent_fd, target_name, uid):
    os.mkdir(target_name, 0o700, dir_fd=parent_fd)  # Existing destination is always STOP.
    target_fd = os.open(target_name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
    completed = []
    try:
        for entry in archive.infolist():
            parts = entry.filename.split('/')
            fd = os.dup(target_fd)
            try:
                for part in parts[:-1]:
                    try: os.mkdir(part, 0o700, dir_fd=fd)
                    except FileExistsError: pass
                    child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                    os.close(fd)
                    fd = child
                    info = os.fstat(fd)
                    require(info.st_uid == uid and stat.S_IMODE(info.st_mode) == 0o700, 'Stage directory differs')
                out_fd = os.open(parts[-1], os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
                with os.fdopen(out_fd, 'wb') as output, archive.open(entry) as member:
                    shutil.copyfileobj(member, output, 1024**2)
                    output.flush()
                    os.fsync(output.fileno())
                read_fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW, dir_fd=fd)
                with os.fdopen(read_fd, 'rb') as materialized:
                    info = os.fstat(materialized.fileno())
                    require(info.st_uid == uid and info.st_nlink == 1 and stat.S_IMODE(info.st_mode) == 0o600, 'Stage file owner/mode differs')
                    expected = checks.get(entry.filename)
                    if expected is None: expected = hashlib.sha256(archive.read(entry)).hexdigest()
                    require(sha(materialized) == expected, 'Written file differs')
                completed.append(entry.filename)
            finally:
                os.close(fd)
        receipt = {'status':'STAGED_FILES_VERIFIED_NOT_RUNTIME', 'source':SOURCE,
                   'archive_sha256':ZIP_SHA, 'manifest_sha256':MANIFEST_SHA,
                   'files_verified':len(completed), 'uid':uid, 'host':socket.gethostname(),
                   'finished_at':datetime.now(timezone.utc).isoformat(),
                   'image_load':'NOT_RUN','test_build_scan':'NOT_RUN','active_config_changed':False}
        receipt_fd = os.open('STAGED_FILES_RECEIPT.json', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=target_fd)
        with os.fdopen(receipt_fd, 'w', encoding='utf-8') as handle:
            json.dump(receipt, handle, indent=2)
            handle.write('\n')
            handle.flush()
            os.fsync(handle.fileno())
        return receipt
    finally:
        os.close(target_fd)

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive', type=Path)
    parser.add_argument('--stage', action='store_true', help='Create new fixed host path; default is inspection only')
    args = parser.parse_args()
    os.umask(0o077)
    if args.stage: identity()
    fd = os.open(args.archive, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, 'Regular input archive required')
        archive, checks, size = verify(stream)
        with archive:
            if not args.stage:
                print(json.dumps({'status':'ARCHIVE_VERIFIED_STAGE_NOT_RUN','files':126,'source':SOURCE}))
                return
            home_fd = directory(HOME, 1000)
            try:
                try: os.mkdir('FDS_CICD_Runtime', 0o700, dir_fd=home_fd)
                except FileExistsError: pass
                parent_fd = os.open('FDS_CICD_Runtime', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=home_fd)
                try:
                    parent = os.fstat(parent_fd)
                    require(parent.st_uid == 1000 and parent.st_mode & 0o077 == 0, 'Runtime parent must be owned/private; do not chmod existing paths')
                    space = os.fstatvfs(parent_fd)
                    require(space.f_bavail * space.f_frsize >= size + 64*1024**2, 'Insufficient space')
                    result = stage(archive, checks, parent_fd, NAME, 1000)
                    print(json.dumps({**result, 'directory':str(HOME/'FDS_CICD_Runtime'/NAME)}))
                finally: os.close(parent_fd)
            finally: os.close(home_fd)

if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(json.dumps({'status':'STOP_PRESERVE_INPUT_AND_PARTIAL_DIRECTORY','reason':str(exc),'runtime':'NOT_RUN'}))
        sys.exit(1)
