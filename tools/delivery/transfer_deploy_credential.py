"""Ansible broker: pinned CP1 TokenRequest/review to existing Runner Workflow.

Default validates local non-secret inputs only. Explicit --execute enables SSH
and issuance; it does not dispatch a workflow or establish new SSH trust.
"""
import argparse
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess

import deploy_binding as b
import deploy_handoff as handoff
import deploy_native_probe as native
import transfer_native_probe as probe


BRIDGE_MAIN = r'''
import socket, struct
sock = upstream = downstream = message = None
try:
    digest, source, scope_raw = sys.argv[1:]
    identity('runner')
    root = stage(sys.stdin.buffer.readline(1024 * 1024 + 2), digest, source, 'runner')
    sys.path.insert(0, str(root))
    import deploy_native_probe as n
    n.private_channel_checked()
    scope = json.loads(scope_raw)
    run = scope['execution']['run_id']
    require(type(run) is int and 0 < run < 10**20 and scope['execution']['attempt'] == 1
            and scope['execution']['workflow_sha'] == source)
    parent = Path.home() / 'FDS_DeploySockets'
    info = safe(parent, regular=False)
    require(info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o700)
    path = parent / ('r' + str(run) + '.sock')
    info = path.lstat()
    require(stat.S_ISSOCK(info.st_mode) and info.st_uid == os.getuid()
            and stat.S_IMODE(info.st_mode) == 0o600)
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(10)
    sock.connect(str(path))
    require(struct.unpack('3i', sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))[1]
            == os.getuid())
    upstream, downstream = n.Channel(0, 1), n.Channel(sock.fileno(), sock.fileno())
    upstream.send({'stage': 'bridge-ready', 'source_sha': source, 'binding': scope})
    message = downstream.recv(10)
    require(set(message) == {'stage', 'binding', 'expires_at'}
            and message['stage'] == 'deploy-ready' and message['binding'] == scope)
    upstream.send(message)
    message = upstream.recv(30)
    downstream.send(message)
    message = downstream.recv(10)
    upstream.send(message)
    message = upstream.recv(10)
    downstream.send(message)
    message = downstream.recv(10)
    require(message == {'stage': 'review-received'})
    upstream.send(message)
except Exception:
    print('{"stage":"error","code":"DEPLOY_BRIDGE_UNCONFIRMED"}', flush=True)
finally:
    message = None
    for channel in (upstream, downstream):
        if channel is not None:
            channel.clear()
    if sock is not None:
        sock.close()
'''


class Bridge(probe.Session):
    """Reuse drift/close checks, but use a distinct, non-probe SSH entrypoint."""
    def __init__(self, bundle, digest, source, scope, key):
        self.checks = probe.loader()
        ssh = shutil.which('ssh')
        b.need(ssh is not None and Path(ssh).is_absolute(), 'SSH_MISSING')
        self.checks['safe'](ssh)
        b.need(os.access(ssh, os.X_OK), 'SSH_NOT_EXECUTABLE')
        self.known = Path.home() / '.ssh/known_hosts'
        self.known_bytes = self.checks['read'](self.known)
        self.key = Path(key)
        info = self.checks['safe'](self.key)
        b.need(not info.st_mode & 0o077, 'PRIVATE_KEY_PERMISSIONS_INVALID')
        self.key_info = probe.snapshot(info)
        options = ['-F', '/dev/null', '-T', '-o', 'BatchMode=yes',
                   '-o', 'StrictHostKeyChecking=yes', '-o', 'UserKnownHostsFile=' + str(self.known),
                   '-o', 'UpdateHostKeys=no', '-o', 'VerifyHostKeyDNS=no',
                   '-o', 'ForwardAgent=no', '-o', 'ClearAllForwardings=yes',
                   '-o', 'ControlMaster=no', '-o', 'ControlPath=none',
                   '-o', 'ConnectionAttempts=1', '-o', 'ConnectTimeout=10',
                   '-o', 'LogLevel=ERROR', '-o', 'IdentitiesOnly=yes', '-i', str(self.key)]
        command = shlex.join(['/usr/bin/python3', '-I', '-B', '-c',
                              probe.BOOTSTRAP_LIBRARY + BRIDGE_MAIN,
                              digest, source, json.dumps(scope, separators=(',', ':'))])
        env = {k: v for k, v in os.environ.items()
               if k not in ('SSH_ASKPASS', 'SSH_ASKPASS_REQUIRE', 'DISPLAY')}
        self.process = subprocess.Popen(
            [ssh] + options + ['server01_cicd-runner@10.250.10.20', command],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            bufsize=0, env=env)
        try:
            self.channel = native.Channel(self.process.stdout.fileno(), self.process.stdin.fileno())
            self.channel.send_raw(bundle[:-1], 30)
            b.need(self.recv(30) == {'stage': 'bridge-ready', 'source_sha': source, 'binding': scope},
                   'DEPLOY_BRIDGE_IDENTITY_INVALID')
        except Exception:
            self.close()
            raise


def scope_checked(raw, digest, source):
    b.need(type(digest) is str and b.gate.SHA256.fullmatch(digest) and b.sha(raw) == digest,
           'HANDOFF_SCOPE_HASH_MISMATCH')
    scope = b.read(raw)
    b.need(set(scope) == {'execution', 'record_sha256', 'plan_sha256', 'sa_uid'},
           'HANDOFF_SCOPE_FIELDS_INVALID')
    execution = b.identity(scope['execution'])
    b.need(execution['workflow_sha'] == source and execution['attempt'] == 1,
           'HANDOFF_SOURCE_MISMATCH')
    for field in ('record_sha256', 'plan_sha256'):
        b.need(type(scope[field]) is str and b.gate.SHA256.fullmatch(scope[field]),
               'HANDOFF_SCOPE_HASH_INVALID')
    b.need(type(scope['sa_uid']) is str and native.c.UID.fullmatch(scope['sa_uid']),
           'HANDOFF_SCOPE_UID_INVALID')
    return scope


def execute(bundle, digest, source, scope, authorization, cp1_key, runner_key):
    cp1 = runner = None
    result = {'HANDOFF': 'NOT_CONFIRMED', 'TOKEN_ISSUANCE': 'NOT_CONFIRMED',
              'DEPLOY': 'CHECK_WORKFLOW_AND_CLAIM', 'ENDPOINT_CLEANUP': 'NOT_CONFIRMED',
              'error_code': 'DEPLOY_HANDOFF_UNCONFIRMED', 'DELIVERY_GATE': 'NOT_VERIFIED'}
    try:
        window = native.Window(authorization)
        cp1 = probe.Session('cp1', bundle, digest, source, probe.CP1_CA, authorization,
                            native.Channel, key=cp1_key)
        runner = Bridge(bundle, digest, source, scope, runner_key)
        result = handoff.relay(cp1, runner, scope, window)
        runner.finish()
        cp1.finish()
    except Exception:
        result.update(HANDOFF='NOT_CONFIRMED', error_code='DEPLOY_HANDOFF_UNCONFIRMED')
    finally:
        for endpoint in (runner, cp1):
            if endpoint is not None:
                try:
                    endpoint.close()
                except Exception:
                    result.update(HANDOFF='NOT_CONFIRMED', ENDPOINT_CLEANUP='NOT_CONFIRMED')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('bundle', 'bundle-sha256', 'source-sha', 'binding', 'binding-sha256'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--authorization-expires')
    parser.add_argument('--cp1-key')
    parser.add_argument('--runner-key')
    args = parser.parse_args()
    try:
        checks = probe.loader()
        checks['identity']('ansible')
        raw = checks['read'](args.bundle)
        # Same audited canonical source bundle validation as the native probe.
        checks['stage'](raw, args.bundle_sha256, args.source_sha, 'ansible')
        scope = scope_checked(checks['read'](args.binding), args.binding_sha256, args.source_sha)
        if not args.execute:
            print('{"HANDOFF":"LOCAL_INPUTS_VALIDATED","TOKEN_ISSUED":false,"DEPLOY":"NOT_EXECUTED"}')
            return 0
        b.need(args.authorization_expires and args.cp1_key and args.runner_key,
               'EXPLICIT_HANDOFF_WINDOW_AND_KEYS_REQUIRED')
        result = execute(raw, args.bundle_sha256, args.source_sha, scope,
                         args.authorization_expires, args.cp1_key, args.runner_key)
        result.update({'binding': scope, 'source_sha': args.source_sha,
                       'bundle_sha256': args.bundle_sha256,
                       'SOURCE_APPROVAL': 'OPERATOR_PINNED_NOT_VERIFIED_IN_TOOL',
                       'broker_input_reference_cleanup': 'LOCAL_REFERENCE_DROPPED',
                       'server_revocation': 'NOT_PERFORMED'})
        print(json.dumps(result, sort_keys=True, indent=2))
        return 0 if result['HANDOFF'] == 'REVIEW_RESPONSE_RECEIVED' else 1
    except Exception:
        print('{"HANDOFF":"NOT_CONFIRMED","error_code":"DEPLOY_HANDOFF_PREPARATION_FAILED"}')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
