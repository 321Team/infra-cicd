#!/usr/bin/env python3
"""One approved Docker Hub metadata window; no install, build, or policy changes."""
import argparse
from datetime import datetime, timezone
import hashlib
import http.client
import io
import json
import math
import os
from pathlib import Path
import pwd
import re
import secrets
import signal
import socket
import ssl
import stat
import sys
import tarfile
import time
from urllib.parse import urlsplit

APPROVAL = 'P1-CICD-OID03-20260917-01'
REFERENCE = 'https://github.com/321Team/infra-cicd/issues/11'
ACCOUNT = 'server01_cicd-runner'
HOME_PATH = '/home/' + ACCOUNT
LEASE = 'FDS_CICD_OID03_20260917_01.json'
AUTH = 'auth.docker.io'
REGISTRY = 'registry-1.docker.io'
TOKEN_PATH = '/token?service=registry.docker.io&scope=repository%3Alibrary%2Fpython%3Apull'
ROOT_PATH = '/v2/library/python/manifests/3.12-slim'
PREFIX = '/v2/library/python/'
LIMIT = 4 * 1024 * 1024
SHA = r'sha256:[0-9a-f]{64}'
INDEX = {'application/vnd.oci.image.index.v1+json',
         'application/vnd.docker.distribution.manifest.list.v2+json'}
MANIFEST = {'application/vnd.oci.image.manifest.v1+json',
            'application/vnd.docker.distribution.manifest.v2+json'}
CONFIG = {'application/vnd.oci.image.config.v1+json',
          'application/vnd.docker.container.image.v1+json'}
ACCEPT = ', '.join(sorted(INDEX | MANIFEST))


class Stop(Exception):
    pass


def require(condition, code):
    if not condition:
        raise Stop(code)


def utc(epoch=None):
    return datetime.fromtimestamp(time.time() if epoch is None else epoch,
                                  timezone.utc).isoformat()


def digest(raw):
    return 'sha256:' + hashlib.sha256(raw).hexdigest()


def document(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, 'DUPLICATE_JSON_KEY')
            result[key] = value
        return result

    def nonfinite(_):
        raise Stop('NONFINITE_JSON')

    try:
        value = json.loads(raw.decode('utf-8'), object_pairs_hook=pairs,
                           parse_constant=nonfinite)
    except (ValueError, UnicodeError, RecursionError):
        raise Stop('INVALID_JSON') from None
    require(isinstance(value, dict), 'JSON_OBJECT_REQUIRED')
    return value


def encode(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + '\n').encode()


def descriptor(value, types):
    require(isinstance(value, dict), 'DESCRIPTOR_REQUIRED')
    require(isinstance(value.get('mediaType'), str) and value['mediaType'] in types,
            'DESCRIPTOR_MEDIA_TYPE')
    require(isinstance(value.get('digest'), str) and re.fullmatch(SHA, value['digest']),
            'DESCRIPTOR_DIGEST')
    require(type(value.get('size')) is int and 0 < value['size'] <= LIMIT,
            'DESCRIPTOR_SIZE')
    require(not any(k in value for k in ('urls', 'data', 'artifactType')), 'EXTERNAL_DESCRIPTOR')
    return value


def match(value, raw):
    require(len(raw) == value['size'] and digest(raw) == value['digest'], 'BLOB_MISMATCH')


def image_doc(raw, types):
    obj = document(raw)
    require(type(obj.get('schemaVersion')) is int and obj['schemaVersion'] == 2,
            'SCHEMA_UNSUPPORTED')
    require(isinstance(obj.get('mediaType'), str) and obj['mediaType'] in types,
            'MEDIA_TYPE_UNSUPPORTED')
    require('subject' not in obj and 'artifactType' not in obj, 'ARTIFACT_UNSUPPORTED')
    return obj


def open_home():
    require(os.getuid() == os.geteuid() == 1000 and pwd.getpwuid(1000).pw_name == ACCOUNT
            and socket.gethostname().split('.')[0] == 'cicd-runner01', 'TARGET_MISMATCH')
    require(pwd.getpwuid(1000).pw_dir == HOME_PATH, 'HOME_PATH_MISMATCH')
    require(not any(os.environ.get(k) for k in (
        'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'http_proxy', 'https_proxy', 'all_proxy',
        'SSL_CERT_FILE', 'SSL_CERT_DIR')), 'PROXY_OR_CA_OVERRIDE_REQUIRES_REVIEW')
    fd = os.open(HOME_PATH, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    info = os.fstat(fd)
    if info.st_uid != 1000 or stat.S_IMODE(info.st_mode) != 0o700:
        os.close(fd)
        raise Stop('HOME_OWNER_OR_MODE_MISMATCH')
    return fd


def write_new(directory_fd, name, raw):
    require('/' not in name and name not in ('', '.', '..'), 'OUTPUT_NAME_INVALID')
    fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                 0o600, dir_fd=directory_fd)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def window(home_fd, now):
    value = {'approval': APPROVAL, 'reference': REFERENCE, 'owner': '이권욱',
             'started_epoch': now, 'expires_epoch': now + 3600}
    try:
        write_new(home_fd, LEASE, encode(value))
    except FileExistsError:
        fd = os.open(LEASE, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=home_fd)
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()
                    and stat.S_IMODE(info.st_mode) == 0o600 and info.st_size <= 8192,
                    'WINDOW_FILE_INVALID')
            raw = stream.read(8193)
            require(len(raw) <= 8192, 'WINDOW_FILE_INVALID')
        value = document(raw)
    require(set(value) == {'approval', 'reference', 'owner', 'started_epoch', 'expires_epoch'}
            and value['approval'] == APPROVAL and value['reference'] == REFERENCE
            and value['owner'] == '이권욱', 'WINDOW_SCOPE_MISMATCH')
    start, end = value['started_epoch'], value['expires_epoch']
    require(type(start) is int and type(end) is int and end == start + 3600,
            'WINDOW_TIME_INVALID')
    require(start <= now < end, 'WINDOW_EXPIRED_OR_CLOCK_REVERSED')
    return value


class Client:
    def __init__(self, lease):
        self.lease = lease
        self.deadline = time.monotonic() + lease['expires_epoch'] - time.time()
        self.allowed = {(AUTH, TOKEN_PATH), (REGISTRY, ROOT_PATH)}
        self.receipts = []
        self.context = ssl.create_default_context()
        self.context.minimum_version = ssl.TLSVersion.TLSv1_2

    def check_time(self):
        require(self.lease['started_epoch'] <= time.time() < self.lease['expires_epoch']
                and time.monotonic() < self.deadline, 'WINDOW_EXPIRED_OR_CLOCK_REVERSED')

    def allow_blob(self, kind, value):
        require(kind in ('manifests', 'blobs') and re.fullmatch(SHA, value), 'PATH_INVALID')
        path = PREFIX + kind + '/' + value
        self.allowed.add((REGISTRY, path))
        return path

    def get(self, host, path, token=None):
        self.check_time()
        require((host, path) in self.allowed, 'UNAPPROVED_ENDPOINT')
        require(token is None or host == REGISTRY, 'TOKEN_DESTINATION_INVALID')
        headers = {'Accept': ACCEPT, 'Accept-Encoding': 'identity',
                   'User-Agent': 'FDS-CICD-OID03-metadata/1.0'}
        if token is not None:
            require(isinstance(token, str) and 0 < len(token) <= 16384
                    and all(33 <= ord(c) <= 126 for c in token), 'TOKEN_FORMAT_INVALID')
            headers['Authorization'] = 'Bearer ' + token
        receipt = {'host': host, 'path': '/token' if host == AUTH else path, 'started_at': utc()}
        self.receipts.append(receipt)
        conn = http.client.HTTPSConnection(host, timeout=min(20, max(0.1, self.deadline - time.monotonic())),
                                            context=self.context)
        try:
            conn.request('GET', path, headers=headers)
            response = conn.getresponse()
            receipt['http_status'] = response.status
            if 300 <= response.status < 400:
                try:
                    redirect_host = urlsplit(response.getheader('Location', '')).hostname
                except ValueError:
                    redirect_host = None
                if redirect_host and re.fullmatch(r'[A-Za-z0-9.-]{1,253}', redirect_host):
                    receipt['redirect_host'] = redirect_host.lower()
                raise Stop('REDIRECT_STOPPED')
            require(response.status == 200, 'HTTP_STATUS_' + str(response.status))
            require(response.getheader('Content-Encoding', 'identity') == 'identity',
                    'CONTENT_ENCODING_UNSUPPORTED')
            maximum = 65536 if host == AUTH else LIMIT
            length = response.getheader('Content-Length')
            if length is not None:
                require(re.fullmatch(r'[0-9]{1,10}', length) and 0 < int(length) <= maximum,
                        'RESPONSE_SIZE_INVALID')
            chunks, size = [], 0
            while True:
                self.check_time()
                chunk = response.read(min(65536, maximum + 1 - size))
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
                require(size <= maximum, 'RESPONSE_TOO_LARGE')
            self.check_time()
            raw = b''.join(chunks)
            require(size > 0 and (length is None or size == int(length)), 'RESPONSE_SIZE_MISMATCH')
            if host == REGISTRY:
                receipt.update({'size': size, 'sha256': digest(raw), 'finished_at': utc()})
                expected = response.getheader('Docker-Content-Digest')
                if path.startswith(PREFIX + 'manifests/'):
                    require(isinstance(expected, str) and re.fullmatch(SHA, expected)
                            and expected == digest(raw), 'RESPONSE_DIGEST_MISMATCH')
                    receipt['response_digest'] = expected
            return raw
        finally:
            conn.close()


def collect(client, files):
    auth = document(client.get(AUTH, TOKEN_PATH))
    token = auth.get('token', auth.get('access_token'))
    require(isinstance(token, str) and token, 'TOKEN_MISSING')
    if 'token' in auth and 'access_token' in auth:
        require(auth['token'] == auth['access_token'], 'TOKEN_CONFLICT')
    root = client.get(REGISTRY, ROOT_PATH, token)
    obj = image_doc(root, INDEX | MANIFEST)
    if obj['mediaType'] in INDEX:
        files['index.json'] = root
        entries = obj.get('manifests')
        require(isinstance(entries, list) and 0 < len(entries) <= 256
                and all(isinstance(v, dict) for v in entries), 'INDEX_INVALID')
        selected = [v for v in entries if isinstance(v.get('platform'), dict)
                    and v['platform'].get('os') == 'linux'
                    and v['platform'].get('architecture') == 'amd64']
        require(len(selected) == 1, 'UNIQUE_LINUX_AMD64_REQUIRED')
        child = descriptor(selected[0], MANIFEST)
        raw = client.get(REGISTRY, client.allow_blob('manifests', child['digest']), token)
        match(child, raw)
        obj = image_doc(raw, MANIFEST)
        require(obj['mediaType'] == child['mediaType'], 'MANIFEST_MEDIA_TYPE_MISMATCH')
    else:
        raw = root
    files['manifest.json'] = raw
    config = descriptor(obj.get('config'), CONFIG)
    data = client.get(REGISTRY, client.allow_blob('blobs', config['digest']), token)
    match(config, data)
    conf = document(data)
    require(conf.get('os') == 'linux' and conf.get('architecture') == 'amd64', 'CONFIG_PLATFORM')
    files['config.json'] = data
    return {'root_digest': digest(root), 'manifest_digest': digest(raw),
            'config_digest': digest(data)}


def save_evidence(home_fd, folder, files, result):
    files = dict(files)
    files['result.json'] = encode(result)
    files['SHA256SUMS'] = ''.join(hashlib.sha256(v).hexdigest() + '  ' + k + '\n'
                                 for k, v in sorted(files.items())).encode()
    os.mkdir(folder, 0o700, dir_fd=home_fd)
    fd = os.open(folder, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=home_fd)
    try:
        for name, raw in files.items():
            write_new(fd, name, raw)
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode='w:gz') as archive:
            for name, raw in sorted(files.items()):
                item = tarfile.TarInfo(name)
                item.mode, item.size, item.mtime = 0o600, len(raw), int(time.time())
                archive.addfile(item, io.BytesIO(raw))
        packed = buffer.getvalue()
        write_new(fd, 'base-metadata.tar.gz', packed)
        write_new(fd, 'base-metadata.tar.gz.sha256',
                  (hashlib.sha256(packed).hexdigest() + '  base-metadata.tar.gz\n').encode())
    finally:
        os.close(fd)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--collect', action='store_true', required=True)
    parser.parse_args(argv)
    result = {'schema': 'P1-CICD-base-metadata-1', 'approval': APPROVAL, 'reference': REFERENCE,
              'owner': '이권욱', 'COLLECTION': 'FAIL', 'BASE_MANIFEST_CHECK': 'NOT_EXECUTED',
              'IMAGE_LAYERS': 'NOT_DOWNLOADED', 'IMAGE_SCAN': 'NOT_EXECUTED',
              'DELIVERY_GATE': 'NOT_VERIFIED', 'NETWORK_POLICY': 'NOT_MODIFIED',
              'NETWORK_ENFORCEMENT': 'NOT_VERIFIED', 'started_at': utc()}
    home_fd, client, files = None, None, {}
    previous = signal.getsignal(signal.SIGALRM)

    def expired(*_):
        raise Stop('WINDOW_EXPIRED')

    try:
        home_fd = open_home()
        lease = window(home_fd, int(time.time()))
        result['window'] = lease
        result['collector_sha256'] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        signal.signal(signal.SIGALRM, expired)
        signal.alarm(max(1, math.ceil(lease['expires_epoch'] - time.time())))
        client = Client(lease)
        result.update(collect(client, files))
        result['COLLECTION'] = 'PASS'
    except Stop as exc:
        result['error_code'] = str(exc)
    except KeyboardInterrupt:
        result['error_code'] = 'INTERRUPTED'
    except (OSError, ssl.SSLError, http.client.HTTPException):
        result['error_code'] = 'LOCAL_IO_OR_TLS_NETWORK_ERROR'
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)
    result['finished_at'] = utc()
    result['requests'] = client.receipts if client else []
    rc = 0 if result['COLLECTION'] == 'PASS' else 1
    try:
        if home_fd is not None:
            folder = 'FDS_CICD_Base_Metadata.' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '.' + secrets.token_hex(4)
            save_evidence(home_fd, folder, files, result)
            result['evidence_directory'] = HOME_PATH + '/' + folder
    except (OSError, Stop):
        result['EVIDENCE_WRITE'] = 'FAIL'
        rc = 1
    finally:
        if home_fd is not None:
            os.close(home_fd)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return rc


if __name__ == '__main__':
    raise SystemExit(main())
