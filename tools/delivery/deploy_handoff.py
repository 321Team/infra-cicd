"""Private Runner socket rendezvous; never a public token endpoint."""
import os
from pathlib import Path
import socket
import stat
import struct
import time

import deploy_binding as b
import deploy_claim as claim
import deploy_credential as credential
import deploy_native_probe as native


def socket_path(home, execution):
    identity = b.identity(execution)
    b.need(identity['attempt'] == 1 and identity['run_id'] < 10**20,
           'HANDOFF_EXECUTION_INVALID')
    return Path(home) / 'FDS_DeploySockets' / ('r' + str(identity['run_id']) + '.sock')


def private_root(path):
    fd = claim.open_root(path)
    os.close(fd)


def peer_checked(sock):
    b.need(os.name == 'posix' and hasattr(socket, 'SO_PEERCRED'), 'HANDOFF_PLATFORM_INVALID')
    _, uid, _ = struct.unpack('3i', sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
    b.need(uid == os.geteuid(), 'HANDOFF_PEER_INVALID')


class Receiver:
    def __init__(self, home, binding, expires_at, remaining):
        self.binding, self.expiry, self.remaining = binding, expires_at, remaining
        self.path = socket_path(home, binding['execution'])
        self.listener = self.connection = self.channel = None
        self.inode = None
        self.reviewed = False

    def open(self):
        private_root(self.path.parent)
        # Existing files/symlinks and abandoned sockets are never unlinked here.
        b.need(not os.path.lexists(self.path), 'HANDOFF_PATH_EXISTS')
        self.listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.listener.bind(str(self.path))
        info = self.path.lstat()
        self.inode = (info.st_dev, info.st_ino)
        os.chmod(self.path, 0o600)
        self.listener.listen(1)
        self.listener.settimeout(min(120, self.remaining()))
        self.connection, _ = self.listener.accept()
        peer_checked(self.connection)
        self.channel = native.Channel(self.connection.fileno(), self.connection.fileno())
        self.channel.send({'stage': 'deploy-ready', 'binding': self.binding,
                           'expires_at': self.expiry}, min(10, self.remaining()))
        return self

    def candidate(self, expected_uid):
        obj = self.channel.recv(min(30, self.remaining()))
        b.need(set(obj) == {'stage', 'token', 'sa_uid', 'status_expires_at'}
               and obj['stage'] == 'candidate' and obj['sa_uid'] == expected_uid,
               'HANDOFF_CANDIDATE_INVALID')
        credential.claims_checked(obj['token'], expected_uid)
        self.uid = expected_uid
        return obj

    def review(self, token, audience, timeout):
        b.need(not self.reviewed, 'HANDOFF_REVIEW_REUSED')
        self.reviewed = True
        deadline = time.monotonic() + timeout
        self.channel.send({'stage': 'review', 'token': token, 'audience': audience,
                           'timeout': timeout}, min(timeout, self.remaining()))
        obj = self.channel.recv(min(deadline - time.monotonic(), self.remaining()))
        b.need(set(obj) == {'stage', 'response'} and obj['stage'] == 'review-result',
               'HANDOFF_REVIEW_INVALID')
        credential.review_checked(obj['response'], token, self.uid)
        # This acknowledges transfer/review only; the Workflow owns rollout outcome.
        self.channel.send({'stage': 'review-received'},
                          min(deadline - time.monotonic(), self.remaining()))
        return obj['response']

    def close(self):
        if self.channel is not None:
            self.channel.clear()
        for sock in (self.connection, self.listener):
            if sock is not None:
                sock.close()
        if self.inode is not None:
            private_root(self.path.parent)
            info = self.path.lstat()
            b.need(stat.S_ISSOCK(info.st_mode) and
                   (info.st_dev, info.st_ino) == self.inode, 'HANDOFF_SOCKET_CHANGED')
            self.path.unlink()
            self.inode = None


def relay(cp1, runner, expected_binding, window):
    """Both channels must already be authenticated/pinned SSH endpoints."""
    candidate = request = response = None
    try:
        ready = runner.recv(min(30, window.remaining()))
        b.need(set(ready) == {'stage', 'binding', 'expires_at'}
               and ready['stage'] == 'deploy-ready' and ready['binding'] == expected_binding,
               'HANDOFF_BINDING_MISMATCH')
        approval_window = native.Window(ready['expires_at'])
        def budget(limit=10):
            return min(limit, window.remaining(), approval_window.remaining())
        b.need(cp1.ready['sa_uid'] == expected_binding['sa_uid'], 'HANDOFF_SA_MISMATCH')
        b.need(budget(900) >= 640, 'HANDOFF_WINDOW_TOO_SHORT')
        cp1.send({'action': 'issue-once'}, budget())
        candidate = cp1.recv(budget(30))
        b.need(set(candidate) == {'stage', 'token', 'sa_uid', 'status_expires_at'}
               and candidate['stage'] == 'candidate'
               and candidate['sa_uid'] == expected_binding['sa_uid'] == cp1.ready['sa_uid'],
               'HANDOFF_SA_MISMATCH')
        claims = credential.claims_checked(candidate['token'], candidate['sa_uid'])
        b.need(claims['iat'] < native.expiry(candidate['status_expires_at']).timestamp()
               <= claims['exp'], 'HANDOFF_EXPIRY_INVALID')
        runner.send(candidate, budget())
        request = runner.recv(budget())
        b.need(set(request) == {'stage', 'token', 'audience', 'timeout'}
               and request['stage'] == 'review' and request['token'] == candidate['token']
               and request['audience'] == b.AUDIENCE, 'HANDOFF_REVIEW_MISMATCH')
        timeout = native.a.timeout_checked(request['timeout'])
        deadline = time.monotonic() + timeout
        cp1.send(request, budget(timeout))
        response = cp1.recv(budget(deadline - time.monotonic()))
        b.need(set(response) == {'stage', 'response'} and response['stage'] == 'review-result',
               'HANDOFF_RESPONSE_INVALID')
        credential.review_checked(response['response'], candidate['token'], candidate['sa_uid'])
        runner.send(response, budget(deadline - time.monotonic()))
        b.need(runner.recv(budget()) == {'stage': 'review-received'}, 'HANDOFF_ACK_INVALID')
        candidate = request = response = None
        # CP1 is released now, not held until the potentially long rollout finishes.
        cp1.send({'action': 'finish'}, budget())
        done = cp1.recv(budget())
        b.need(set(done) == {'stage', 'token_issuance', 'credential'}
               and done['stage'] == 'done'
               and done['token_issuance'] == 'ISSUED_AND_CP1_REVIEWED', 'HANDOFF_CP1_CLOSE_INVALID')
        from transfer_native_probe import safe_credential
        observed = safe_credential(done['credential'])
        b.need(observed['service_account_uid'] == expected_binding['sa_uid'], 'HANDOFF_CP1_UID_CHANGED')
        return {'HANDOFF': 'REVIEW_RESPONSE_RECEIVED', 'DEPLOY': 'CHECK_WORKFLOW_RESULT',
                'cp1_credential': observed, 'DELIVERY_GATE': 'NOT_VERIFIED'}
    finally:
        candidate = request = response = None
