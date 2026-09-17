"""Run bundled pip against a local HTTP view of the scoped Unix socket index."""
import http.client
from http.server import BaseHTTPRequestHandler, HTTPServer
import os
import socket
import subprocess
import sys
import threading


def main():
    socket_path, *args = sys.argv[1:]
    if len(args) < 2 or args[0] != 'download' or '@INDEX@' not in args:
        return 2

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            conn = http.client.HTTPConnection('localhost', timeout=30)
            try:
                conn.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                conn.sock.settimeout(30)
                conn.sock.connect(socket_path)
                conn.request('GET', self.path)
                response = conn.getresponse()
                self.send_response(response.status)
                for key in ('Content-Type', 'Content-Length'):
                    value = response.getheader(key)
                    if value is not None:
                        self.send_header(key, value)
                self.end_headers()
                total = 0
                while chunk := response.read(65536):
                    total += len(chunk)
                    if total > 64 * 1024**2:
                        raise ValueError('RELAY_LIMIT')
                    self.wfile.write(chunk)
            finally:
                conn.close()

    server = HTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = 'http://127.0.0.1:' + str(server.server_port) + '/simple/'
    env = {'PATH': os.environ.get('PATH', '/usr/local/bin:/usr/bin:/bin'),
           'HOME': os.environ.get('HOME', '/tmp'), 'PIP_CONFIG_FILE': os.devnull,
           'LANG': 'C.UTF-8'}
    try:
        return subprocess.run([sys.executable, '-I', '-m', 'pip', '--isolated',
            '--disable-pip-version-check', '--no-cache-dir'] +
            [url if x == '@INDEX@' else x for x in args], env=env, check=False).returncode
    finally:
        server.shutdown()
        server.server_close()


if __name__ == '__main__':
    raise SystemExit(main())
