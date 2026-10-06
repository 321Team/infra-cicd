"""Run explicitly selected prechecks. stdout contains safe JSON observations only."""
import argparse
import datetime as dt
import getpass
import hashlib
import json
import socket
import sys
import time
import warnings

import deploy_cp1_access as a
import deploy_credential as c
import deploy_transport as t


def runner_tls(ca_path, expected_hash, *, timeout=10):
    a.host_checked('runner')
    timeout = a.timeout_checked(timeout)
    _, context = a.ca_checked(ca_path, expected_hash)
    deadline = time.monotonic() + timeout

    def budget():
        remaining = deadline - time.monotonic()
        a.need(remaining > 0, 'TLS_DEADLINE_EXCEEDED')
        return remaining

    try:
        # Numeric IPv4 only: no DNS, proxy, HTTP request or token.
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as raw:
            raw.settimeout(budget())
            raw.connect((t.HOST, t.PORT))
            raw.settimeout(budget())
            with context.wrap_socket(raw, server_hostname=t.HOST,
                                     do_handshake_on_connect=False) as secured:
                secured.settimeout(budget())
                secured.do_handshake()
                budget()
                leaf = secured.getpeercert(binary_form=True)
                a.need(type(leaf) is bytes and leaf, 'TLS_LEAF_UNCONFIRMED')
                result = {
                    'TLS_HANDSHAKE': 'PASS', 'api_server': t.API_SERVER,
                    'hostname_verified': t.HOST, 'ca_sha256': expected_hash,
                    'leaf_der_sha256': hashlib.sha256(leaf).hexdigest(),
                    'tls_version': secured.version(),
                    'source_ip': secured.getsockname()[0],
                    'runner_token_authentication': 'NOT_VERIFIED',
                    'DEPLOY': 'NOT_EXECUTED', 'DELIVERY_GATE': 'NOT_VERIFIED'}
        budget()
        a.ca_checked(ca_path, expected_hash)
        budget()
        return result
    except a.AccessError:
        raise
    except Exception:
        raise a.AccessError('RUNNER_TLS_FAILED') from None


def cp1_review(backend):
    # Reject getpass fallback to echoing stdin. Never read a token from argv/env/file.
    a.need(sys.stdin.isatty() and sys.stderr.isatty(), 'INTERACTIVE_TTY_REQUIRED')
    uid = backend.service_account_uid()
    token = None
    credential = None
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', getpass.GetPassWarning)
            token = getpass.getpass('이미 발급된 단기 SA token (표시되지 않음): ')
        credential = c.Credential(token, uid, backend)
        credential.close()
        result = credential.observation()
        result.update({'CP1_TOKEN_REVIEW': 'PASS', 'DEPLOY': 'NOT_EXECUTED',
                       'TOKEN_ISSUED': False, 'input_reference_cleanup': 'LOCAL_REFERENCE_DROPPED'})
        return result
    finally:
        if credential is not None:
            credential.close()
        token = None  # Logical reference release, not physical memory zeroization.


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('runner-tls', 'cp1-review', 'cp1-negative-test'))
    parser.add_argument('--ca', required=True, help='Existing public PEM CA, absolute safe path')
    parser.add_argument('--ca-sha256', required=True, help='Separately confirmed public CA SHA256')
    args = parser.parse_args()
    try:
        if args.mode == 'runner-tls':
            result = runner_tls(args.ca, args.ca_sha256)
        else:
            backend = a.CP1Access(args.ca, args.ca_sha256)
            if args.mode == 'cp1-review':
                result = cp1_review(backend)
            else:
                result = {'probes': [backend.negative_test(name) for name in a.NAMES],
                          'DEPLOY': 'NOT_EXECUTED', 'DELIVERY_GATE': 'NOT_VERIFIED'}
        result['checked_at'] = dt.datetime.now(dt.timezone.utc).isoformat()
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
        return 0
    except (a.AccessError, c.CredentialError) as exc:
        print('STOP: ' + str(exc), file=sys.stderr)
        return 64
    except Exception:
        print('STOP: ACCESS_PRECHECK_FAILED', file=sys.stderr)
        return 64


if __name__ == '__main__':
    raise SystemExit(main())
