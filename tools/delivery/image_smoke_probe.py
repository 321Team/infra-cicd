"""Standard-library probe executed inside the isolated final application image.

No business requests, DB health requests, credentials or external networking.
"""
import json
import os
from pathlib import Path
import stat
import sys
import time
import urllib.error
import urllib.request

PROFILES = {
    'transaction-api': (8000, 9091, b'transaction_save_success_total'),
    'fds-engine': (8001, 9090, b'fds_evaluated_total'),
}


def need(condition, code):
    if not condition:
        raise ValueError(code)


def readable_tree(root):
    need(not root.is_symlink() and root.is_dir(), 'APP_DIRECTORY_REQUIRED')
    files = []

    def failed(_error):
        raise ValueError('APP_DIRECTORY_NOT_READABLE')

    entries = 0
    for directory, directories, names in os.walk(root, onerror=failed, followlinks=False):
        for name in [*directories, *names]:
            path = Path(directory) / name
            entries += 1
            need(entries <= 10000 and not path.is_symlink(), 'APP_TREE_UNSUPPORTED')
        for name in names:
            path = Path(directory) / name
            need(stat.S_ISREG(path.stat().st_mode), 'APP_FILE_NOT_REGULAR')
            with path.open('rb') as stream:
                stream.read(1)  # Actual open/read as the runtime UID; not mode inference.
            files.append(path)
    need(root / 'run.py' in files and (root / 'app').is_dir(), 'APP_SOURCE_MISSING')
    return len(files)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def request(opener, port, path, timeout):
    try:
        response = opener.open('http://127.0.0.1:' + str(port) + path, timeout=timeout)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        body = response.read(256 * 1024 + 1)
        need(len(body) <= 256 * 1024, 'HTTP_RESPONSE_TOO_LARGE')
        return response.code, response.headers.get('Content-Type', ''), body


def listeners(profile, *, duration=30):
    main, metrics, counter = PROFILES[profile]
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    deadline = time.monotonic() + duration
    while time.monotonic() < deadline:
        try:
            def get(port, path):
                remaining = deadline - time.monotonic()
                need(remaining > 0, 'IMAGE_STARTUP_TIMEOUT')
                return request(opener, port, path, min(2, remaining))

            health = get(main, '/livez')
            business_metrics = get(main, '/metrics')
            dedicated = get(metrics, '/metrics')
            metrics_health = get(metrics, '/livez')
            if (health[0] == 200 and json.loads(health[2]) == {'status': 'ok'} and
                    business_metrics[0] == metrics_health[0] == 404 and
                    dedicated[0] == 200 and dedicated[1].startswith('text/plain') and
                    counter in dedicated[2]):
                return {'livez': 200, 'business_metrics': 404,
                        'dedicated_metrics': 200, 'metrics_livez': 404}
        except (OSError, ValueError, urllib.error.URLError):
            pass
        time.sleep(min(0.25, max(0, deadline - time.monotonic())))
    raise ValueError('IMAGE_STARTUP_OR_LISTENER_CONTRACT_FAILED')


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    try:
        need(len(args) == 1 and args[0] in PROFILES, 'SERVICE_UNSUPPORTED')
        need((os.geteuid(), os.getegid()) == (10001, 999), 'RUNTIME_IDENTITY_MISMATCH')
        for path in ('/', '/app', '/tmp', '/run', '/var/tmp'):
            if Path(path).exists():
                need(os.statvfs(path).f_flag & os.ST_RDONLY, 'ROOTFS_OR_TEMP_WRITABLE')
        count = readable_tree(Path('/app'))
        checks = listeners(args[0])
        print(json.dumps({'schema': 'P1-final-image-probe-1', 'service': args[0],
                          'uid': 10001, 'gid': 999, 'readable_files': count,
                          'rootfs': 'READ_ONLY', 'checks': checks}))
        return 0
    except (OSError, ValueError):
        print('FINAL_IMAGE_PROBE=FAIL', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
