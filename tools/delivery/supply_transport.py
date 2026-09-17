"""Scope 03 only: fixed HTTPS endpoints, immutable deadline, private new files."""
import gzip
import hashlib
import http.client
import io
import json
import os
from pathlib import Path
import pwd
import re
import socket
import ssl
import stat
import tarfile
import time
from urllib.parse import urlsplit

from supply_inputs import MANIFEST_RAW, CONFIG_RAW, MANIFEST_DIGEST, CONFIG_DIGEST, SOURCE_SHA

HOME = Path('/home/server01_cicd-runner')
SCOPE = 'P1-CICD-OID03-20260917-03'
LEASE = 'FDS_CICD_OID03_20260917_03.json'
AUTH = 'auth.docker.io'
REGISTRY = 'registry-1.docker.io'
CDN = 'production.cloudfront.docker.com'
PYPI = 'pypi.org'
FILES = 'files.pythonhosted.org'
HOSTS = [AUTH, REGISTRY, CDN, PYPI, FILES]
TOKEN_PATH = '/token?service=registry.docker.io&scope=repository%3Alibrary%2Fpython%3Apull'
PREFIX = '/v2/library/python/blobs/'
HEX = r'[0-9a-f]{64}'


class Stop(Exception):
    pass


def require(ok, code):
    if not ok:
        raise Stop(code)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def encode(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode()


def document(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, 'DUPLICATE_JSON_KEY')
            result[key] = value
        return result
    obj = json.loads(raw, object_pairs_hook=pairs,
                     parse_constant=lambda _: require(False, 'NONFINITE_JSON'))
    require(isinstance(obj, dict), 'JSON_OBJECT_REQUIRED')
    return obj


def write_new(path, raw):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def idle():
    # An observation, not a scheduler reservation. Repeated before network/commands.
    for entry in Path('/proc').iterdir():
        if not entry.name.isdecimal():
            continue
        try:
            if entry.stat().st_uid == 1000:
                raw = (entry / 'cmdline').read_bytes()
                require(not any(Path(arg.decode(errors='replace')).name == 'Runner.Worker'
                                for arg in raw.split(b'\0') if arg), 'RUNNER_JOB_ACTIVE')
        except (FileNotFoundError, ProcessLookupError):
            continue


def preflight():
    require(os.getuid() == os.geteuid() == 1000 and
            pwd.getpwuid(1000).pw_name == 'server01_cicd-runner' and
            pwd.getpwuid(1000).pw_dir == str(HOME) and
            socket.gethostname().split('.')[0] == 'cicd-runner01', 'TARGET_MISMATCH')
    info = HOME.lstat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == 1000 and
            stat.S_IMODE(info.st_mode) == 0o700, 'HOME_OWNER_OR_MODE_MISMATCH')
    require(not any(os.environ.get(k) for k in (
        'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'http_proxy', 'https_proxy', 'all_proxy',
        'SSL_CERT_FILE', 'SSL_CERT_DIR', 'REQUESTS_CA_BUNDLE', 'CURL_CA_BUNDLE')),
        'PROXY_OR_CA_OVERRIDE_REQUIRES_REVIEW')
    idle()
    free = os.statvfs(HOME)
    require(free.f_bavail * free.f_frsize >= 4 * 1024**3, 'FOUR_GIB_FREE_REQUIRED')


def window(now):
    require(not (HOME / (LEASE + '.completed')).exists(), 'SCOPE_ALREADY_COMPLETED')
    scope = {'scope': SCOPE, 'owner': '이권욱', 'hosts': HOSTS,
             'manifest': MANIFEST_DIGEST, 'requirements_source': SOURCE_SHA, 'duration_seconds': 3600}
    value = dict(scope, started_epoch=now, expires_epoch=now + 3600)
    try:
        write_new(HOME / LEASE, encode(value))
    except FileExistsError:
        fd = os.open(HOME / LEASE, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            require(stat.S_ISREG(info.st_mode) and info.st_uid == 1000 and
                    stat.S_IMODE(info.st_mode) == 0o600 and info.st_size <= 8192,
                    'WINDOW_FILE_INVALID')
            value = document(stream.read(8193))
    require(set(value) == set(scope) | {'started_epoch', 'expires_epoch'} and
            all(value[k] == v for k, v in scope.items()), 'WINDOW_SCOPE_MISMATCH')
    start, end = value['started_epoch'], value['expires_epoch']
    require(type(start) is int and type(end) is int and end == start + 3600 and
            start <= now < end, 'WINDOW_EXPIRED_OR_CLOCK_REVERSED')
    return value


def url_path(url, host):
    require(isinstance(url, str) and 0 < len(url) <= 16384 and
            all(33 <= ord(c) <= 126 for c in url) and '\\' not in url and '#' not in url,
            'URL_INVALID')
    try:
        p = urlsplit(url)
        ok = (p.scheme == 'https' and p.hostname == host and
              p.netloc.lower() in (host, host + ':443') and p.port in (None, 443) and
              p.username is None and p.password is None and not p.fragment and
              p.path.startswith('/') and not p.path.startswith('//'))
    except ValueError:
        ok = False
    require(ok, 'URL_HOST_OR_PORT_INVALID')
    return p.path + ('?' + p.query if p.query else '')


class Client:
    def __init__(self, lease):
        self.lease = lease
        self.deadline = time.monotonic() + lease['expires_epoch'] - time.time()
        self.receipts = []
        self.allowed_files = set()
        self.total = 0
        self.context = ssl.create_default_context()
        self.context.minimum_version = ssl.TLSVersion.TLSv1_2
        self.layers = {PREFIX + x['digest']: x for x in document(MANIFEST_RAW)['layers']}

    def check(self):
        require(self.lease['started_epoch'] <= time.time() < self.lease['expires_epoch']
                and time.monotonic() < self.deadline, 'WINDOW_EXPIRED_OR_CLOCK_REVERSED')
        idle()

    def get(self, host, path, maximum, token=None, cdn=False):
        self.check()
        allowed = ((host == AUTH and path == TOKEN_PATH) or
                   (host == REGISTRY and path in self.layers) or
                   (host == CDN and cdn) or
                   (host == PYPI and re.fullmatch(r'/simple/[a-z0-9][a-z0-9-]*/', path)) or
                   (host == FILES and path in self.allowed_files))
        require(allowed, 'ENDPOINT_NOT_ALLOWED')
        require(token is None or host == REGISTRY, 'TOKEN_DESTINATION_INVALID')
        require(len(self.receipts) < 2048, 'REQUEST_BUDGET_EXCEEDED')
        headers = {'Accept-Encoding': 'identity', 'User-Agent': 'FDS-CICD-supply/1.0'}
        headers['Accept'] = ('application/vnd.pypi.simple.v1+json' if host == PYPI
                             else 'application/octet-stream')
        if token is not None:
            require(isinstance(token, str) and 0 < len(token) <= 16384 and
                    all(33 <= ord(c) <= 126 for c in token), 'TOKEN_INVALID')
            headers['Authorization'] = 'Bearer ' + token
        receipt = {'host': host, 'path': '[CDN_URL_REDACTED]' if host == CDN else
                   ('/token' if host == AUTH else path), 'started_epoch': time.time()}
        self.receipts.append(receipt)
        conn = http.client.HTTPSConnection(host, context=self.context,
                    timeout=min(20, max(.1, self.deadline - time.monotonic())))
        try:
            conn.request('GET', path, headers=headers)
            response = conn.getresponse()
            receipt['status'] = response.status
            if host == REGISTRY and response.status == 307:
                target = url_path(response.getheader('Location'), CDN)
                conn.close()
                return self.get(CDN, target, maximum, cdn=True)
            require(not 300 <= response.status < 400, 'REDIRECT_STOPPED')
            require(response.status == 200, 'HTTP_STATUS_' + str(response.status))
            require(response.getheader('Content-Encoding', 'identity') == 'identity',
                    'ENCODING_UNSUPPORTED')
            length = response.getheader('Content-Length')
            require(length is None or (re.fullmatch(r'[0-9]{1,10}', length) and
                    0 < int(length) <= maximum), 'RESPONSE_SIZE_INVALID')
            data = bytearray()
            while True:
                self.check()
                chunk = response.read(min(65536, maximum + 1 - len(data)))
                if not chunk:
                    break
                data.extend(chunk)
                self.total += len(chunk)
                require(len(data) <= maximum and self.total <= 1024**3, 'BYTE_BUDGET_EXCEEDED')
            self.check()
            require(data and (length is None or len(data) == int(length)), 'RESPONSE_TRUNCATED')
            if host != AUTH:
                receipt.update(size=len(data), sha256=sha(data))
            receipt['finished_epoch'] = time.time()
            return bytes(data)
        finally:
            conn.close()


def base_archive(client, path):
    require('sha256:' + sha(MANIFEST_RAW) == MANIFEST_DIGEST and
            'sha256:' + sha(CONFIG_RAW) == CONFIG_DIGEST, 'EMBEDDED_METADATA_CHANGED')
    manifest, config = document(MANIFEST_RAW), document(CONFIG_RAW)
    auth = document(client.get(AUTH, TOKEN_PATH, 65536))
    token = auth.get('token', auth.get('access_token'))
    require(token, 'TOKEN_MISSING')
    require('token' not in auth or 'access_token' not in auth or
            auth['token'] == auth['access_token'], 'TOKEN_CONFLICT')
    def add(archive, name, raw):
        item = tarfile.TarInfo(name)
        item.size, item.mode, item.mtime = len(raw), 0o600, 0
        archive.addfile(item, io.BytesIO(raw))
    # Explicitly new file. Incomplete archives are retained as failure evidence.
    with path.open('xb') as out, tarfile.open(fileobj=out, mode='w') as archive:
        add(archive, 'oci-layout', encode({'imageLayoutVersion': '1.0.0'}))
        add(archive, 'index.json', encode({'schemaVersion': 2, 'manifests': [{
            'mediaType': manifest['mediaType'], 'digest': MANIFEST_DIGEST,
            'size': len(MANIFEST_RAW), 'platform': {'os': 'linux', 'architecture': 'amd64'}}]}))
        add(archive, 'blobs/sha256/' + MANIFEST_DIGEST[7:], MANIFEST_RAW)
        add(archive, 'blobs/sha256/' + CONFIG_DIGEST[7:], CONFIG_RAW)
        for layer, expected_diff in zip(manifest['layers'], config['rootfs']['diff_ids'], strict=True):
            raw = client.get(REGISTRY, PREFIX + layer['digest'], layer['size'], token)
            require(len(raw) == layer['size'] and 'sha256:' + sha(raw) == layer['digest'],
                    'LAYER_HASH_OR_SIZE_MISMATCH')
            hasher, size = hashlib.sha256(), 0
            with gzip.GzipFile(fileobj=io.BytesIO(raw)) as uncompressed:
                while chunk := uncompressed.read(65536):
                    client.check()
                    size += len(chunk)
                    require(size <= 512 * 1024**2, 'DIFFID_SIZE_LIMIT')
                    hasher.update(chunk)
            require('sha256:' + hasher.hexdigest() == expected_diff, 'LAYER_DIFFID_MISMATCH')
            add(archive, 'blobs/sha256/' + layer['digest'][7:], raw)
    with path.open('rb') as stream:
        archive_sha = hashlib.file_digest(stream, 'sha256').hexdigest()
    write_new(path.with_suffix(path.suffix + '.sha256'),
              (archive_sha + '  ' + path.name + '\n').encode())
    return archive_sha
