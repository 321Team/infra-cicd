"""Loopback wheel-only index: all upstream traffic uses the scoped HTTPS client."""
from email.parser import BytesParser
import html
from http.server import BaseHTTPRequestHandler
import io
import re
import threading
from socketserver import UnixStreamServer
import zipfile

from supply_transport import FILES, PYPI, Stop, document, require, sha, url_path

WHEEL = r'[A-Za-z0-9_.+!-]+\.whl'
NAME = r'[A-Za-z0-9][A-Za-z0-9._-]*'


def normalize(value):
    return re.sub(r'[-_.]+', '-', value).lower()


def wheel_metadata(raw):
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        entries = archive.infolist()
        require(len(entries) <= 20000 and len({x.filename for x in entries}) == len(entries),
                'WHEEL_ENTRIES_INVALID')
        require(all(not x.filename.startswith('/') and '\\' not in x.filename and
                    '..' not in x.filename.split('/') and not x.flag_bits & 1 and
                    (x.external_attr >> 16) & 0o170000 != 0o120000 for x in entries),
                'WHEEL_PATH_OR_LINK_INVALID')
        require(sum(x.file_size for x in entries) <= 512 * 1024**2, 'WHEEL_UNPACKED_SIZE_LIMIT')
        metadata = [x for x in entries if re.fullmatch(r'[^/]+\.dist-info/METADATA', x.filename)]
        require(len(metadata) == 1 and metadata[0].file_size <= 2 * 1024**2,
                'WHEEL_METADATA_INVALID')
        obj = BytesParser().parsebytes(archive.read(metadata[0]))
    require(len(obj.get_all('Name', [])) == len(obj.get_all('Version', [])) == 1,
            'WHEEL_IDENTITY_INVALID')
    name, version = obj['Name'], obj['Version']
    require(re.fullmatch(NAME, name) and re.fullmatch(r'[A-Za-z0-9_.+!-]+', version),
            'WHEEL_IDENTITY_INVALID')
    for dep in obj.get_all('Requires-Dist', []):
        # Never let pip see a dependency URL (including file/VCS references).
        require('@' not in dep and '://' not in dep and '\\' not in dep and
                '\n' not in dep and '\r' not in dep and
                re.match(NAME + r'(?:\s|\[|\(|[<>=!~;]|$)', dep), 'DEPENDENCY_URL_OR_FORMAT')
    return normalize(name), version


class WheelIndex:
    def __init__(self, client, socket_path):
        self.client = client
        self.socket_path = socket_path
        self.projects = {}
        self.files = {}
        self.cache = {}
        self.error = None

    def page(self, name):
        if name in self.projects:
            return self.projects[name]
        require(len(self.projects) < 256, 'PROJECT_BUDGET_EXCEEDED')
        raw = self.client.get(PYPI, '/simple/' + name + '/', 32 * 1024**2)
        obj = document(raw)
        require(normalize(obj.get('name', '')) == name and isinstance(obj.get('files'), list),
                'PYPI_PROJECT_MISMATCH')
        require(len(obj['files']) <= 100000, 'PYPI_FILE_COUNT_LIMIT')
        links = []
        for item in obj['files']:
            filename = item.get('filename', '')
            if not re.fullmatch(WHEEL, filename):
                continue  # Source distributions are never exposed to pip.
            require(normalize(filename.split('-')[0]) == name, 'WHEEL_PROJECT_MISMATCH')
            digest = item.get('hashes', {}).get('sha256', '')
            require(re.fullmatch(r'[0-9a-f]{64}', digest), 'PYPI_HASH_REQUIRED')
            path = url_path(item.get('url'), FILES)
            require(path.startswith('/packages/') and '?' not in path and
                    path.rsplit('/', 1)[1] == filename, 'WHEEL_URL_INVALID')
            key = '/files/' + digest + '/' + filename
            record = (path, digest, name)
            require(key not in self.files or self.files[key] == record, 'WHEEL_RECORD_CONFLICT')
            self.files[key] = record
            self.client.allowed_files.add(path)
            attrs = ''
            requires_python = item.get('requires-python')
            if requires_python:
                require(isinstance(requires_python, str), 'PYPI_REQUIRES_PYTHON_INVALID')
                attrs += ' data-requires-python="' + html.escape(requires_python, quote=True) + '"'
            if item.get('yanked'):
                attrs += ' data-yanked="upstream-yanked"'
            links.append('<a href="' + key + '#sha256=' + digest + '"' + attrs + '>' + filename + '</a>')
        result = ('<!doctype html><html><body>' + '\n'.join(links) + '</body></html>').encode()
        self.projects[name] = result
        return result

    def wheel(self, key):
        require(key in self.files, 'WHEEL_NOT_LISTED')
        path, digest, project = self.files[key]
        if key not in self.cache:
            raw = self.client.get(FILES, path, 64 * 1024**2)
            require(sha(raw) == digest, 'WHEEL_HASH_MISMATCH')
            name, _ = wheel_metadata(raw)
            require(name == project, 'WHEEL_METADATA_PROJECT_MISMATCH')
            require(sum(len(x) for x in self.cache.values()) + len(raw) <= 256 * 1024**2,
                    'WHEEL_CACHE_BUDGET_EXCEEDED')
            self.cache[key] = raw
        return self.cache[key]

    def __enter__(self):
        parent = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_GET(self):
                try:
                    parent.client.check()
                    require(parent.error is None, 'INDEX_ALREADY_FAILED')
                    match = re.fullmatch(r'/simple/([a-z0-9][a-z0-9-]*)/', self.path)
                    data = parent.page(match[1]) if match else parent.wheel(self.path)
                    self.send_response(200)
                    self.send_header('Content-Type', 'text/html' if match else 'application/octet-stream')
                    self.send_header('Content-Length', str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                except Exception as exc:
                    parent.error = str(exc) if isinstance(exc, Stop) else 'INDEX_REQUEST_FAILED'
                    self.send_error(502, 'SUPPLY_STOPPED')

        self.server = UnixStreamServer(str(self.socket_path), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=21)


def make_lock(directory, roots):
    extras = {}
    for root in roots:
        match = re.fullmatch('(' + NAME + r')(\[[A-Za-z0-9_,.-]+\])?==([A-Za-z0-9_.+!-]+)', root)
        require(match, 'ROOT_REQUIREMENT_INVALID')
        extras[normalize(match[1])] = (match[2] or '', match[3])
    records = {}
    for path in sorted(directory.iterdir()):
        require(path.is_file() and not path.is_symlink() and re.fullmatch(WHEEL, path.name),
                'WHEELHOUSE_MEMBER_INVALID')
        raw = path.read_bytes()
        name, version = wheel_metadata(raw)
        require(name not in records, 'DUPLICATE_PROJECT_WHEEL')
        records[name] = {'name': name, 'version': version, 'file': path.name, 'sha256': sha(raw)}
    require(0 < len(records) <= 256, 'WHEELHOUSE_COUNT_INVALID')
    for name, (_, version) in extras.items():
        require(name in records and records[name]['version'] == version, 'ROOT_VERSION_MISMATCH')
    lock = ''.join(name + extras.get(name, ('', None))[0] + '==' + row['version'] +
                   ' --hash=sha256:' + row['sha256'] + '\n' for name, row in sorted(records.items()))
    return lock.encode(), list(records.values())
