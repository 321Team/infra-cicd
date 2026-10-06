"""Synthetic subprocess/TLS tests and local POSIX files; no CP1/token/cluster."""
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import warnings
from unittest.mock import MagicMock, Mock, patch

import capture_deploy_access as cli
import deploy_binding as b
import deploy_cp1_access as a
import deploy_credential as c
from test_deploy_credential import SA_UID, fixture
from test_deploy_binding import NOW

CA = b'PUBLIC_CA_MOCK'
CONFIG = {
    'clusters': [{'name': 'cluster', 'cluster': {'server': a.t.API_SERVER}}],
    'users': [{'name': 'admin', 'user': {
        'client-certificate-data': 'DATA+OMITTED', 'client-key-data': 'DATA+OMITTED'}}],
    'contexts': [{'name': 'admin-context', 'context': {'cluster': 'cluster', 'user': 'admin'}}],
    'current-context': 'admin-context',
}


def completed(obj=None, code=0, stderr=b''):
    raw = json.dumps(obj).encode() if obj is not None else b''
    return subprocess.CompletedProcess([], code, raw, stderr)


def status(details=True):
    obj = {'apiVersion': 'v1', 'kind': 'Status', 'status': 'Failure',
           'reason': 'Invalid', 'code': 422, 'message': 'PRIVATE_MESSAGE'}
    if details:
        obj['details'] = {'name': 'transaction-api', 'namespace': 'fds-app',
                          'group': 'apps', 'kind': 'deployments',
                          'causes': [{'message': 'PRIVATE_CAUSE'}]}
    return obj


def trace(raw, *, name='transaction-api', verb='PATCH', url=None, code=422, legacy=False):
    url = url or (a.t.API_SERVER + '/apis/apps/v1/namespaces/fds-app/deployments/'
                  + name + '?dryRun=All&fieldManager=kubectl-patch')
    prefix = ('I0000 "Request" verb=' + json.dumps(verb) + ' url=' + json.dumps(url)
              + ' headers={"Authorization":["Bearer PRIVATE_HEADER"]}\n'
              + 'I0000 "Response" status=' + json.dumps(str(code) + ' Fixture Status')
              + ' headers={"Private":["PRIVATE_RESPONSE_HEADER"]}\n')
    if legacy:
        body = b'I0000 Response Body: ' + raw + b'\n'
    else:
        body = ('I0000 "Response Body" body=' + json.dumps(raw.decode()) + '\n').encode()
    return prefix.encode() + body


def multiline_trace(raw, **kwargs):
    # Literal klog writeStringValue layout, including the ambiguous final LF.
    prefix = trace(b'', **kwargs).splitlines(keepends=True)[:2]
    chunks = raw.decode('utf-8').split('\n')
    if chunks[-1] == '':
        chunks.pop()
    return (b''.join(prefix) + b'I0000 "Response Body" body=<\n' +
            ''.join('\t' + row + '\n' for row in chunks).encode('utf-8') + b' >\n')


def body_log(obj, **kwargs):
    return trace(json.dumps(obj, separators=(',', ':')).encode(), **kwargs)


@unittest.skipUnless(os.name == 'posix', 'POSIX private-file tests')
class CP1Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='cicd-access-test-', dir=Path.home())
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.ca, self.config_path, self.binary = (
            self.root / name for name in ('ca.pem', 'config', 'kubectl'))
        for path, raw in ((self.ca, CA), (self.config_path, b'PRIVATE_KUBECONFIG'),
                          (self.binary, b'#!/bin/sh\nexit 1\n')):
            path.write_bytes(raw)
            path.chmod(0o700 if path == self.binary else 0o600)
        self.cfg, self.mono = copy.deepcopy(CONFIG), 0
        _, self.token, self.review = fixture()
        self.sa = {'apiVersion': 'v1', 'kind': 'ServiceAccount', 'automountServiceAccountToken': False,
                   'metadata': {'name': 'cicd-runner-sa', 'namespace': 'ci-cd', 'uid': SA_UID}}
        self.object = {'apiVersion': 'apps/v1', 'kind': 'Deployment',
                       'metadata': {'name': 'transaction-api', 'namespace': 'fds-app',
                                    'uid': SA_UID, 'resourceVersion': 'opaque:7'},
                       'spec': {'replicas': 3}}
        self.operation = None
        self.calls = []
        patches = [
            patch.object(a, 'host_checked'), patch.object(a.shutil, 'which', return_value=str(self.binary)),
            patch.dict(os.environ, {'KUBECONFIG': str(self.config_path), 'HTTPS_PROXY': 'http://other',
                                    'http_proxy': 'http://other', 'ALL_PROXY': 'http://other'}),
            patch.object(a.t, 'context_checked', return_value=Mock()),
            patch.object(a.subprocess, 'run', side_effect=self.run_command),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    def run_command(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        if 'config' in argv:
            return completed(self.cfg)
        if self.operation:
            return self.operation(argv, kwargs)
        if 'serviceaccount' in argv:
            return completed(self.sa)
        if 'create' in argv:
            return completed(self.review)
        if 'patch' in argv:
            return completed(code=1, stderr=body_log(status()))
        return completed(self.object)

    def backend(self):
        return a.CP1Access(str(self.ca), b.sha(CA), clock=lambda: self.mono)

    def test_tokenreview_uses_stdin_fixed_pinned_flags_and_no_proxy(self):
        backend = self.backend()
        credential = c.Credential(self.token, backend.service_account_uid(), backend,
                                  now=lambda: NOW, clock=lambda: self.mono)
        self.assertEqual(credential.token(), self.token)
        argv, kwargs = next(call for call in self.calls if 'create' in call[0])
        self.assertNotIn(self.token, ' '.join(argv))
        self.assertNotIn(self.token, json.dumps(kwargs['env']))
        self.assertEqual(json.loads(kwargs['input'])['spec'],
                         {'token': self.token, 'audiences': [b.AUDIENCE]})
        self.assertIn('--server=' + a.t.API_SERVER, argv)
        self.assertIn('--tls-server-name=' + a.t.HOST, argv)
        self.assertIn('--certificate-authority=' + str(self.ca), argv)
        self.assertIn('--v=0', argv)
        self.assertIn('--raw=/apis/authentication.k8s.io/v1/tokenreviews', argv)
        self.assertEqual(kwargs['env']['KUBECTL_KUBERC'], 'false')
        self.assertFalse(any(key.lower() in ('http_proxy', 'https_proxy', 'all_proxy')
                             for key in kwargs['env']))
        self.assertLessEqual(kwargs['timeout'], 10)
        credential.close()

    def test_named_sa_uid_must_be_current_and_not_deleting(self):
        for change in ({'uid': 'invalid'}, {'namespace': 'default'}, {'deletionTimestamp': 'today'}):
            self.sa['metadata'] = {'name': 'cicd-runner-sa', 'namespace': 'ci-cd', 'uid': SA_UID,
                                   **change}
            with self.subTest(change=change), self.assertRaisesRegex(
                    a.AccessError, '^SA_READ_FAILED$'):
                self.backend().service_account_uid()

    def test_sa_automount_enabled_is_not_accepted(self):
        self.sa['automountServiceAccountToken'] = True
        with self.assertRaisesRegex(a.AccessError, '^SA_READ_FAILED$'):
            self.backend().service_account_uid()

    def test_wrong_server_proxy_or_insecure_config_blocks_before_api(self):
        for values in ({'server': 'https://other'}, {'insecure-skip-tls-verify': True},
                       {'tls-server-name': 'other'}, {'proxy-url': 'http://other'}):
            self.cfg = copy.deepcopy(CONFIG)
            self.cfg['clusters'][0]['cluster'].update(values)
            with self.subTest(values=values), self.assertRaises(a.AccessError):
                self.backend()
        self.assertTrue(all('config' in argv for argv, _ in self.calls))

    def test_exec_token_and_impersonated_admin_config_are_rejected(self):
        for key in ('exec', 'auth-provider', 'token', 'tokenFile', 'as', 'as-groups'):
            self.cfg = copy.deepcopy(CONFIG)
            self.cfg['users'][0]['user'][key] = 'PRIVATE_VALUE'
            with self.subTest(key=key), self.assertRaisesRegex(
                    a.AccessError, '^CLIENT_AUTH_UNSUPPORTED$'):
                self.backend()

    def test_config_selection_and_missing_cert_are_rejected(self):
        for change in ('context', 'user', 'key'):
            self.cfg = copy.deepcopy(CONFIG)
            if change == 'context':
                self.cfg['current-context'] = 'other'
            elif change == 'user':
                self.cfg['users'].append(copy.deepcopy(self.cfg['users'][0]))
            else:
                del self.cfg['users'][0]['user']['client-key-data']
            with self.subTest(change=change), self.assertRaises(a.AccessError):
                self.backend()

    def test_config_file_change_blocks_without_api_or_retry(self):
        backend = self.backend()
        self.config_path.write_bytes(b'CHANGED_PRIVATE_KUBECONFIG')
        count = len(self.calls)
        for _ in range(2):
            with self.assertRaisesRegex(a.AccessError, '^TOKEN_REVIEW_FAILED$'):
                backend.review(self.token, b.AUDIENCE, 10)
        self.assertEqual(len(self.calls), count)

    def test_ca_file_change_or_symlink_is_rejected(self):
        backend = self.backend()
        self.ca.write_bytes(b'CHANGED_CA')
        with self.assertRaises(a.AccessError):
            backend.review(self.token, b.AUDIENCE, 10)
        self.ca.unlink()
        self.ca.symlink_to(self.config_path)
        with self.assertRaisesRegex(a.AccessError, '^UNSAFE_FILE$'):
            a.read_checked(self.ca)

    def test_world_writable_file_and_parent_fail(self):
        self.ca.chmod(0o622)
        with self.assertRaisesRegex(a.AccessError, '^UNSAFE_FILE$'):
            a.read_checked(self.ca)
        self.ca.chmod(0o600)
        self.root.chmod(0o722)
        with self.assertRaisesRegex(a.AccessError, '^UNSAFE_PARENT$'):
            a.read_checked(self.ca)
        self.root.chmod(0o700)

    def test_hardlink_and_oversized_file_fail(self):
        linked = self.root / 'linked'
        os.link(self.ca, linked)
        with self.assertRaisesRegex(a.AccessError, '^UNSAFE_FILE$'):
            a.read_checked(self.ca)
        linked.unlink()
        self.ca.write_bytes(b'x' * (a.MAX_BYTES + 1))
        with self.assertRaisesRegex(a.AccessError, '^FILE_SIZE_INVALID$'):
            a.read_checked(self.ca)

    def test_timeout_and_process_errors_are_static_and_latch(self):
        for error in (subprocess.TimeoutExpired(['PRIVATE_TOKEN'], 1),
                      RuntimeError(self.token)):
            backend = self.backend()
            self.operation = lambda *_: (_ for _ in ()).throw(error)
            with self.subTest(error=type(error)), self.assertRaisesRegex(
                    a.AccessError, '^TOKEN_REVIEW_FAILED$'):
                backend.review(self.token, b.AUDIENCE, 10)
            count = len(self.calls)
            with self.assertRaises(a.AccessError):
                backend.review(self.token, b.AUDIENCE, 10)
            self.assertEqual(len(self.calls), count)
            self.operation = None

    def test_budget_overrun_is_rejected_after_command(self):
        backend = self.backend()
        def delayed(*_):
            self.mono = 11
            return completed(self.review)
        self.operation = delayed
        with self.assertRaisesRegex(a.AccessError, '^TOKEN_REVIEW_FAILED$'):
            backend.review(self.token, b.AUDIENCE, 10)
        self.assertTrue(backend.blocked)

    def test_invalid_audience_or_timeout_never_posts(self):
        for audience, timeout in (('other', 10), (b.AUDIENCE, 11), (b.AUDIENCE, True),
                                  (b.AUDIENCE, float('nan'))):
            backend = self.backend()
            with self.subTest(audience=audience, timeout=timeout), self.assertRaises(a.AccessError):
                backend.review(self.token, audience, timeout)
        self.assertFalse(any('create' in argv for argv, _ in self.calls))

    def test_duplicate_json_and_command_failure_do_not_leak_body(self):
        for output in (subprocess.CompletedProcess([], 0, b'{"kind":"a","kind":"b"}', b''),
                       subprocess.CompletedProcess([], 1, b'', self.token.encode())):
            backend = self.backend()
            self.operation = lambda *_: output
            with self.subTest(output=output.returncode), self.assertRaisesRegex(
                    a.AccessError, '^TOKEN_REVIEW_FAILED$'):
                backend.review(self.token, b.AUDIENCE, 10)
            self.operation = None

    def test_arbitrary_write_commands_and_image_replacement_are_rejected_before_spawn(self):
        backend = self.backend()
        count = len(self.calls)
        for args in (['delete', 'serviceaccount', 'cicd-runner-sa'],
                     ['patch', 'deployment', 'transaction-api', '-p', '[{"op":"replace"}]']):
            with self.subTest(args=args), self.assertRaises(a.AccessError):
                backend.command(args, 10)
        self.assertEqual(len(self.calls), count)

    def test_negative_probe_is_fixed_dry_run_test_only_and_sanitizes_log(self):
        observed = self.backend().negative_test('transaction-api')
        argv, kwargs = next(call for call in self.calls if 'patch' in call[0])
        self.assertIn('--dry-run=server', argv)
        self.assertEqual(json.loads(argv[argv.index('-p') + 1]),
                         [{'op': 'test', 'path': '/metadata/uid', 'value': a.ZERO_UID}])
        self.assertIsNone(kwargs['input'])
        self.assertEqual(observed['status_projection']['code'], 422)
        self.assertEqual(observed['spec_observation'], 'UNCHANGED_AT_TWO_READS')
        self.assertEqual(observed['DEPLOY'], 'NOT_EXECUTED')
        self.assertNotIn('PRIVATE', json.dumps(observed))

    def test_negative_probe_unexpected_success_or_changed_spec_is_not_pass(self):
        for mode in ('success', 'change'):
            backend = self.backend()
            def operation(argv, _):
                if 'patch' in argv:
                    if mode == 'success':
                        return completed(self.object)
                    self.object['spec']['replicas'] += 1
                    return completed(code=1, stderr=body_log(status()))
                return completed(self.object)
            self.operation = operation
            with self.subTest(mode=mode), self.assertRaisesRegex(
                    a.AccessError, '^NEGATIVE_TEST_UNCONFIRMED$'):
                backend.negative_test('transaction-api')
            self.operation = None

    def test_negative_probe_unknown_status_or_scope_fails_closed(self):
        backend = self.backend()
        self.operation = lambda *_: completed(code=1, stderr=b'PRIVATE_ERROR')
        with self.assertRaises(a.AccessError):
            backend.negative_test('transaction-api')
        with self.assertRaises(a.AccessError):
            self.backend().negative_test('other')


    def test_negative_probe_collects_actual_multiline_status_without_raw_log(self):
        backend = self.backend()
        def operation(argv, _):
            if 'patch' in argv:
                return completed(code=1, stderr=multiline_trace(
                    json.dumps(status()).encode() + b'\n'))
            return completed(self.object)
        self.operation = operation
        observed = backend.negative_test('transaction-api')
        self.assertEqual(observed['status_body_hash_basis'], 'KLOG_NORMALIZED_TRAILING_LF')
        self.assertEqual(observed['spec_observation'], 'UNCHANGED_AT_TWO_READS')
        self.assertEqual(observed['DEPLOY'], 'NOT_EXECUTED')
        self.assertNotIn('PRIVATE', json.dumps(observed))


class ProjectionTests(unittest.TestCase):
    def test_details_missing_422_remains_projection_only(self):
        result = a.status_projection(body_log(status(False)), 'transaction-api')
        self.assertNotIn('details', result['status_projection'])
        self.assertEqual(result['transport_policy'], 'UNCHANGED')

    def test_legacy_body_format_and_409_are_supported(self):
        obj = status()
        obj.update(code=409, reason='Conflict')
        raw = json.dumps(obj).encode()
        result = a.status_projection(trace(raw, code=409, legacy=True), 'transaction-api')
        self.assertEqual(result['status_body_sha256'], b.sha(raw))
        self.assertEqual(result['status_projection']['code'], 409)

    def test_missing_duplicate_truncated_or_wrong_status_is_not_observation(self):
        malformed = [b'PRIVATE_HEADERS', body_log(status()) * 2,
                     body_log({**status(), 'code': 403}),
                     trace(b'{ [truncated 10 chars]'),
                     trace(b'{"kind":1,"kind":2}')]
        for raw in malformed:
            with self.subTest(raw=raw[:30]), self.assertRaises(a.AccessError):
                a.status_projection(raw, 'transaction-api')

    def test_arbitrary_details_do_not_enter_projection(self):
        obj = status()
        obj['details'].update(name='PRIVATE_IDENTITY', secret='PRIVATE_VALUE')
        result = a.status_projection(body_log(obj), 'transaction-api')
        self.assertEqual(result['status_projection']['details']['name'], 'MISMATCH')
        self.assertNotIn('PRIVATE', json.dumps(result))


    def test_truncated_or_nonjson_discovery_is_not_parsed_as_patch(self):
        for raw in (b'{"kind":"APIGroupList" [truncated 2000 chars]', b'NOT_JSON'):
            discovery = trace(raw, verb='GET', url=a.t.API_SERVER + '/apis', code=200)
            result = a.status_projection(discovery + body_log(status()), 'transaction-api')
            self.assertEqual(result['status_projection']['code'], 422)
            self.assertEqual(result['response_binding'], 'PATCH_REQUEST_HTTP_STATUS_BODY')
            self.assertNotIn('PRIVATE', json.dumps(result))

    def test_discovery_status_failure_is_not_accepted_as_patch_evidence(self):
        discovery = body_log(status(), verb='GET', url=a.t.API_SERVER + '/apis')
        with self.assertRaisesRegex(a.AccessError, '^PROBE_STATUS_UNCONFIRMED$'):
            a.status_projection(discovery, 'transaction-api')
        result = a.status_projection(discovery + body_log(status()), 'transaction-api')
        self.assertEqual(result['request_path'].split('/')[-1], 'transaction-api')

    def test_target_status_truncation_duplicate_keys_or_nonjson_still_stops(self):
        for raw in (b'{"kind":"Status" [truncated 99 chars]', b'NON_JSON',
                    b'{"kind":"Status","kind":"Status"}'):
            with self.subTest(raw=raw), self.assertRaises(a.AccessError):
                a.status_projection(trace(raw), 'transaction-api')

    def test_patch_url_host_path_or_dryrun_mismatch_stops(self):
        path = '/apis/apps/v1/namespaces/fds-app/deployments/transaction-api'
        urls = ['https://other:6443' + path + '?dryRun=All',
                a.t.API_SERVER + path.replace('fds-app', 'default') + '?dryRun=All',
                a.t.API_SERVER + path.replace('transaction-api', 'fds-engine') + '?dryRun=All',
                a.t.API_SERVER + path,
                a.t.API_SERVER + path + '?dryRun=All&dryRun=All',
                a.t.API_SERVER + path + '?dryRun=All&unknown=value',
                a.t.API_SERVER + path + '?dryRun=All#fragment']
        for url in urls:
            with self.subTest(url=url), self.assertRaisesRegex(
                    a.AccessError, '^PROBE_REQUEST_SCOPE_INVALID$'):
                a.status_projection(body_log(status(), url=url), 'transaction-api')

    def test_http_and_body_status_must_agree(self):
        for code in (200, 403, 409):
            with self.subTest(code=code), self.assertRaises(a.AccessError):
                a.status_projection(body_log(status(), code=code), 'transaction-api')

    def test_unassociated_body_or_incomplete_trace_never_passes(self):
        complete = body_log(status())
        lines = complete.splitlines(keepends=True)
        for raw in (lines[2], lines[0] + lines[2], lines[1] + lines[2],
                    lines[0] + lines[1], complete + lines[2]):
            with self.subTest(raw=raw[:30]), self.assertRaises(a.AccessError):
                a.status_projection(raw, 'transaction-api')

    def test_duplicate_patch_or_interleaved_request_is_ambiguous(self):
        complete = body_log(status())
        discovery = trace(b'{}', verb='GET', url=a.t.API_SERVER + '/apis', code=200)
        lines = complete.splitlines(keepends=True)
        for raw in (complete * 2, lines[0] + discovery + b''.join(lines[1:])):
            with self.subTest(raw=raw[:30]), self.assertRaises(a.AccessError):
                a.status_projection(raw, 'transaction-api')

    def test_pending_discovery_response_at_patch_start_stops(self):
        discovery = trace(b'{}', verb='GET', url=a.t.API_SERVER + '/apis', code=200)
        lines = discovery.splitlines(keepends=True)
        for prefix in (lines[0], lines[0] + lines[1]):
            with self.subTest(prefix=prefix), self.assertRaises(a.AccessError):
                a.status_projection(prefix + body_log(status()), 'transaction-api')

    def test_both_service_requests_are_bound_to_their_own_name(self):
        obj = status()
        obj['details']['name'] = 'fds-engine'
        result = a.status_projection(body_log(obj, name='fds-engine'), 'fds-engine')
        self.assertEqual(result['status_projection']['details']['name'], 'fds-engine')
        result = a.status_projection(body_log(obj), 'transaction-api')
        self.assertEqual(result['status_projection']['details']['name'], 'MISMATCH')

    def test_wrong_log_format_or_private_error_never_becomes_observation(self):
        complete = body_log(status())
        for raw in (complete.replace(b'"Request"', b'"Unknown Request"'),
                    complete.replace(b'"Response" status=', b'"Response" unsupported='),
                    b'PRIVATE_CREDENTIAL_ERROR'):
            with self.subTest(raw=raw[:30]), self.assertRaises(a.AccessError):
                a.status_projection(raw, 'transaction-api')




    def test_actual_klog_trailing_lf_status_block_is_collected(self):
        raw = json.dumps(status(), separators=(',', ':')).encode() + b'\n'
        observed = a.status_projection(multiline_trace(raw), 'transaction-api')
        self.assertEqual(observed['status_projection']['code'], 422)
        self.assertEqual(observed['status_body_sha256'], b.sha(raw))
        self.assertEqual(observed['status_body_hash_basis'], 'KLOG_NORMALIZED_TRAILING_LF')
        self.assertNotIn('PRIVATE', json.dumps(observed))

    def test_klog_cannot_prove_original_final_lf_and_hash_is_labeled(self):
        raw = json.dumps(status(), indent=2).encode()
        without_lf = a.status_projection(multiline_trace(raw), 'transaction-api')
        with_lf = a.status_projection(multiline_trace(raw + b'\n'), 'transaction-api')
        self.assertEqual(without_lf, with_lf)
        self.assertEqual(without_lf['status_body_sha256'], b.sha(raw + b'\n'))
        self.assertNotEqual(without_lf['status_body_sha256'], b.sha(raw))
        self.assertEqual(without_lf['status_body_hash_basis'], 'KLOG_NORMALIZED_TRAILING_LF')

    def test_multiline_discovery_is_consumed_before_quoted_or_multiline_target(self):
        raw = json.dumps(status(), separators=(',', ':')).encode() + b'\n'
        discovery = multiline_trace(b'{"kind":"APIGroupList"\n[truncated 2000 chars]\n',
                                    verb='GET', url=a.t.API_SERVER + '/apis', code=200)
        for target in (trace(raw), multiline_trace(raw)):
            observed = a.status_projection(discovery + target, 'transaction-api')
            self.assertEqual(observed['status_projection']['code'], 422)

    def test_discovery_block_rows_cannot_inject_log_request_markers(self):
        discovery = multiline_trace(
            b' >\nI0000 "Request" verb="PATCH" url="https://other"\n'
            b'I0000 "Response" status="200 Fake"\n'
            b'I0000 "Response Body" body=<\n',
            verb='GET', url=a.t.API_SERVER + '/apis', code=200)
        observed = a.status_projection(discovery + body_log(status()), 'transaction-api')
        self.assertEqual(observed['status_body_hash_basis'], 'LOG_BODY_DECODED')

    def test_missing_terminator_bad_indent_empty_truncated_and_duplicate_blocks_stop(self):
        raw = json.dumps(status()).encode() + b'\n'
        good = multiline_trace(raw)
        variants = [
            good[:-3], good.replace(b'\t{', b' {', 1),
            good.replace(b' >\n', b'\t>\n'),
            multiline_trace(b''), multiline_trace(raw + b'[truncated 10 chars]\n'),
            multiline_trace(b'{"kind":"Status","kind":"Status"}\n'), good + good,
        ]
        for data in variants:
            with self.subTest(data=data[:30]), self.assertRaises(a.AccessError):
                a.status_projection(data, 'transaction-api')

    def test_discovery_multiline_without_terminator_cannot_swallow_valid_target(self):
        discovery = multiline_trace(b'{"kind":"APIGroupList"}\n',
                                    verb='GET', url=a.t.API_SERVER + '/apis', code=200)
        with self.assertRaises(a.AccessError):
            a.status_projection(discovery[:-3] + body_log(status()), 'transaction-api')

    def test_multiline_realistic_url_and_409_status_are_bound(self):
        obj = status()
        obj.update(code=409, reason='Conflict')
        url = (a.t.API_SERVER + '/apis/apps/v1/namespaces/fds-app/deployments/transaction-api'
               '?dryRun=All&fieldManager=kubectl-patch&fieldValidation=Strict&timeout=9.5s')
        observed = a.status_projection(
            multiline_trace(json.dumps(obj).encode() + b'\n', url=url, code=409), 'transaction-api')
        self.assertEqual(observed['status_projection']['code'], 409)
        self.assertEqual(observed['response_binding'], 'PATCH_REQUEST_HTTP_STATUS_BODY')




class PrecheckTests(unittest.TestCase):
    def setUp(self):
        self.raw, self.secured, self.context = MagicMock(), MagicMock(), Mock()
        self.raw.__enter__.return_value = self.raw
        self.secured.__enter__.return_value = self.secured
        self.context.wrap_socket.return_value = self.secured
        self.secured.getpeercert.return_value = b'MOCK_LEAF_DER'
        self.secured.version.return_value = 'TLSv1.3'
        self.secured.getsockname.return_value = ('10.250.10.20', 40000)
        self.mono = 0
        patches = [patch.object(a, 'host_checked'),
                   patch.object(a, 'ca_checked', return_value=(CA, self.context)),
                   patch.object(cli.socket, 'socket', return_value=self.raw),
                   patch.object(cli.time, 'monotonic', side_effect=lambda: self.mono)]
        self.mocks = [p.start() for p in patches]
        for p in patches:
            self.addCleanup(p.stop)

    def test_runner_handshake_numeric_ip_and_hostname_without_http_or_token(self):
        result = cli.runner_tls('/safe/ca', b.sha(CA))
        self.raw.connect.assert_called_once_with(('10.1.93.109', 6443))
        self.context.wrap_socket.assert_called_once_with(
            self.raw, server_hostname='10.1.93.109', do_handshake_on_connect=False)
        self.secured.do_handshake.assert_called_once_with()
        self.secured.sendall.assert_not_called()
        self.assertEqual(result['TLS_HANDSHAKE'], 'PASS')
        self.assertEqual(result['runner_token_authentication'], 'NOT_VERIFIED')
        self.assertEqual(result['DEPLOY'], 'NOT_EXECUTED')
        self.assertEqual(self.mocks[1].call_count, 2)

    def test_tls_error_and_deadline_are_static_failures(self):
        self.secured.do_handshake.side_effect = RuntimeError('PRIVATE_TLS_MESSAGE')
        with self.assertRaisesRegex(a.AccessError, '^RUNNER_TLS_FAILED$'):
            cli.runner_tls('/safe/ca', b.sha(CA))
        self.secured.do_handshake.side_effect = lambda: setattr(self, 'mono', 11)
        with self.assertRaisesRegex(a.AccessError, '^TLS_DEADLINE_EXCEEDED$'):
            cli.runner_tls('/safe/ca', b.sha(CA))

    def test_non_tty_token_input_is_rejected_without_prompt_or_lookup(self):
        backend = Mock()
        with patch.object(cli.sys, 'stdin', Mock(isatty=lambda: False)), \
             patch.object(cli.getpass, 'getpass') as prompt:
            with self.assertRaisesRegex(a.AccessError, '^INTERACTIVE_TTY_REQUIRED$'):
                cli.cp1_review(backend)
        backend.service_account_uid.assert_not_called()
        prompt.assert_not_called()

    def test_cp1_review_cleans_local_credential_and_emits_no_token(self):
        _, token, review = fixture()
        backend = Mock()
        backend.service_account_uid.return_value = SA_UID
        backend.review.return_value = review
        actual = c.Credential(token, SA_UID, backend, now=lambda: NOW, clock=lambda: 0)
        with patch.object(cli.sys, 'stdin', Mock(isatty=lambda: True)), \
             patch.object(cli.sys, 'stderr', Mock(isatty=lambda: True)), \
             patch.object(cli.getpass, 'getpass', return_value=token), \
             patch.object(cli.c, 'Credential', return_value=actual):
            result = cli.cp1_review(backend)
        self.assertTrue(actual.closed)
        self.assertEqual(result['CP1_TOKEN_REVIEW'], 'PASS')
        self.assertEqual(result['local_credential_cleanup'], 'LOCAL_REFERENCE_DROPPED')
        self.assertEqual(result['server_revocation'], 'NOT_PERFORMED')
        self.assertFalse(result['TOKEN_ISSUED'])
        self.assertNotIn(token, json.dumps(result))

    def test_getpass_echo_fallback_warning_aborts_before_tokenreview(self):
        backend = Mock()
        backend.service_account_uid.return_value = SA_UID
        def fallback(_):
            warnings.warn('PRIVATE_PROMPT', cli.getpass.GetPassWarning)
            raise AssertionError('Must stop before fallback input')
        with patch.object(cli.sys, 'stdin', Mock(isatty=lambda: True)), \
             patch.object(cli.sys, 'stderr', Mock(isatty=lambda: True)), \
             patch.object(cli.getpass, 'getpass', side_effect=fallback):
            with self.assertRaises(cli.getpass.GetPassWarning):
                cli.cp1_review(backend)
        backend.review.assert_not_called()
