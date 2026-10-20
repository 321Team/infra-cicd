"""Synthetic TLS/API replies only. No runtime, credential issuance or grant."""
import copy
from dataclasses import replace
import json
import socket
import ssl
import unittest
from unittest.mock import Mock, patch

import delivery_adapter as core
import delivery_transport as transport
import delivery_platform_readback as platform
from delivery_object_reader import BearerCredential
from delivery_verifier import Progress, ReadRequest, PlatformEvidence, Provenance, Verifier, VerificationError
from test_delivery_adapter import fixture
from test_delivery_transport import CA, TOKEN, FakeSocket, response


class PlatformReaderTests(unittest.TestCase):
    def setUp(self):
        binding, _, _ = fixture()
        self.binding = replace(binding, ca_sha256=core.digest(CA),
                               platform_receipt_sha256=core.digest(b'SYNTHETIC_ISSUANCE_RECEIPT'))
        self.credential, self.progress = BearerCredential(TOKEN), Progress()
        self.clock = 100.0
        self.request = ReadRequest(self.binding, self.credential, self.progress, 110.0)
        self.expected = platform.ExpectedSubject('system:serviceaccount:fds-app:synthetic-delivery',
            'SYNTHETIC_SUBJECT_UID', ('system:authenticated', 'system:serviceaccounts', 'system:serviceaccounts:fds-app'))
        self.queue, self.sockets = [], []
        self.context = Mock(verify_mode=ssl.CERT_REQUIRED, check_hostname=True, keylog_filename=None)
        self.context.wrap_socket.side_effect = lambda sock, **kw:sock
        for name in ('getaddrinfo', 'create_connection'):
            p=patch.object(socket,name,side_effect=AssertionError('NETWORK_FORBIDDEN'));p.start();self.addCleanup(p.stop)
        p=patch.object(socket,'socket',side_effect=self.make_socket);self.socket_factory=p.start();self.addCleanup(p.stop)
        with patch.object(transport.ssl,'SSLContext',return_value=self.context):
            self.reader=self.build()

    def build(self, **changes):
        args=dict(credential=self.credential,expected_subject=self.expected,progress=lambda:self.progress,
                  ca_pem=CA,endpoint_ip='192.0.2.10',monotonic=lambda:self.clock)
        args.update(changes)
        return platform.PlatformObservationReader(self.binding,**args)

    def make_socket(self,*args):
        if not self.queue:raise AssertionError('UNEXPECTED_SOCKET')
        item=self.queue.pop(0);sock=item if isinstance(item,FakeSocket) else FakeSocket(item)
        self.sockets.append(sock);return sock

    def objects(self):
        subject={'apiVersion':'authentication.k8s.io/v1','kind':'SelfSubjectReview','status':{'userInfo':
            {'username':self.expected.username,'uid':self.expected.uid,'groups':list(self.expected.groups)}}}
        cv={'apiVersion':'config.openshift.io/v1','kind':'ClusterVersion','metadata':{'name':'version'},
            'spec':{'clusterID':self.binding.cluster_id}}
        ns={'apiVersion':'v1','kind':'Namespace','metadata':{'name':'fds-app','uid':self.binding.namespace_uid},
            'status':{'phase':'Active'}}
        access=[]
        for capability in platform.CAPABILITIES:
            value=json.loads(platform.access_body(capability))
            # Match the real Kubernetes omitempty response for the core group.
            if value['spec']['resourceAttributes']['group']=='':
                del value['spec']['resourceAttributes']['group']
            value['status']={'allowed':True};access.append(value)
        return [subject,cv,ns,*access,copy.deepcopy(cv),copy.deepcopy(ns),copy.deepcopy(subject)]

    def enqueue(self, objects=None):
        self.queue=[]
        for obj in objects if objects is not None else self.objects():
            self.queue.append(response(core.encoded(obj),201 if obj['kind'].startswith('SelfSubject') else 200))

    def test_all_18_exact_requests_use_same_token_tls_and_close(self):
        self.enqueue();value=self.reader.inspect(self.request)
        self.assertIs(value.request,self.request);self.assertIs(value.credential,self.credential)
        self.assertEqual(18,len(value.raw_responses));self.assertEqual(18,len(self.sockets))
        self.assertEqual(platform.CAPABILITIES,value.required_capabilities)
        for s in self.sockets:
            self.assertEqual(('192.0.2.10',6443),s.connected);self.assertTrue(s.closed)
            self.assertIn(('Authorization: Bearer '+TOKEN+'\r\n').encode(),s.sent)
            self.assertNotIn(b'Impersonate-',s.sent);self.assertNotIn(b'PATCH ',s.sent)
        self.assertTrue(all(c.kwargs['server_hostname']=='api.rosa.example.com' for c in self.context.wrap_socket.call_args_list))
        self.assertFalse(value.execution_authorized);self.assertFalse(value.full_platform_authenticated)
        self.assertFalse(value.least_privilege_verified)
        self.assertNotIn(TOKEN,repr(value)+repr(self.reader)+repr(self.credential))

    def test_constructor_has_no_io_or_invented_identity(self):
        self.socket_factory.assert_not_called()
        for subject in (None,{},replace(self.expected,username='PENDING'),replace(self.expected,uid=''),
                        replace(self.expected,username='system:anonymous'),replace(self.expected,groups=('system:unauthenticated',)),
                        replace(self.expected,groups=('x','x'))):
            with self.subTest(subject=type(subject)),self.assertRaises(platform.PlatformReadbackError):self.build(expected_subject=subject)
        self.socket_factory.assert_not_called()

    def test_named_namespace_scopes_and_get_patch_only(self):
        self.enqueue();self.reader.inspect(self.request)
        bodies=[bytes(s.sent).split(b'\r\n\r\n',1)[1] for s in self.sockets[3:15]]
        self.assertEqual(12,len(bodies));self.assertEqual(platform.ACCESS_BODIES,frozenset(bodies))
        for raw in bodies:
            attrs=json.loads(raw)['spec']['resourceAttributes']
            self.assertEqual('fds-app',attrs['namespace']);self.assertNotIn('*',attrs.values())
            self.assertIn(attrs['verb'],('get','patch'));self.assertTrue(attrs['name'])

    def test_arbitrary_paths_methods_bodies_fail_before_socket(self):
        calls=[('GET','/api/v1/secrets',b''),('POST','/apis/authentication.k8s.io/v1/tokenreviews',platform.SUBJECT_BODY),
               ('POST','/api/v1/namespaces/fds-app/serviceaccounts/synthetic/token',b'{}'),
               ('DELETE',platform.NAMESPACE_PATH,b''),('POST',platform.SUBJECT_PATH,b'{}')]
        wrong=json.loads(platform.access_body(platform.CAPABILITIES[0]));wrong['spec']['resourceAttributes']['namespace']=''
        calls.append(('POST',platform.ACCESS_PATH,core.encoded(wrong)))
        for method,path,body in calls:
            with self.subTest(method=method,path=path),self.assertRaises(Exception):self.reader.exchange(self.request,method,path,body)
        self.socket_factory.assert_not_called()
        with self.assertRaisesRegex(platform.PlatformReadbackError,'^PLATFORM_READ_REQUEST_REQUIRED$'):self.reader(self.request)

    def test_subject_uid_username_groups_and_shape_fail_first(self):
        for field,value in [('username','other'),('uid','other'),('groups',[]),('groups',['x','x']),('groups',[1])]:
            objs=self.objects();objs[0]['status']['userInfo'][field]=value;self.enqueue(objs)
            self.sockets=[]
            with self.subTest(field=field),self.assertRaises(platform.PlatformReadbackError):self.reader.inspect(self.request)
            self.assertEqual(1,len(self.sockets))

    def test_namespace_uid_name_phase_or_deletion_mismatch(self):
        for index in (2,16):
            for field,value in [('uid','other'),('name','rosa-app'),('deletionTimestamp','2026-01-01T00:00:00Z'),('deletionTimestamp',False)]:
                objs=self.objects();objs[index]['metadata'][field]=value;self.enqueue(objs)
                with self.subTest(index=index,field=field),self.assertRaises(platform.PlatformReadbackError):self.reader.inspect(self.request)
        objs=self.objects();objs[2]['status']['phase']='Terminating';self.enqueue(objs)
        with self.assertRaises(platform.PlatformReadbackError):self.reader.inspect(self.request)

    def test_cluster_or_final_identity_change_not_cached(self):
        for index in (1,15,17):
            objs=self.objects()
            if index==17:objs[index]['status']['userInfo']['uid']='REPLACED'
            else:objs[index]['spec']['clusterID']='REPLACED'
            self.enqueue(objs)
            with self.subTest(index=index),self.assertRaises(platform.PlatformReadbackError):self.reader.inspect(self.request)

    def test_access_deny_no_opinion_conflict_or_evaluation_error_rejected(self):
        for status in ({'allowed':False},{'allowed':False,'denied':False},{'allowed':True,'denied':True},
                       {'allowed':1},{'allowed':True,'denied':0},{'allowed':True,'evaluationError':'SYNTHETIC_ERROR'}):
            objs=self.objects();objs[3]['status']=status;self.enqueue(objs)
            with self.subTest(status=status),self.assertRaisesRegex(platform.PlatformReadbackError,'^PLATFORM_READBACK_ACCESS_DENIED$'):
                self.reader.inspect(self.request)

    def test_access_echo_missing_or_widened_is_not_allow_evidence(self):
        for change in ('namespace','name','group','verb'):
            objs=self.objects();objs[3]['spec']['resourceAttributes'][change]='*';self.enqueue(objs)
            with self.subTest(change=change),self.assertRaises(platform.PlatformReadbackError):self.reader.inspect(self.request)
        objs=self.objects();objs[3]['spec']['user']='system:admin';self.enqueue(objs)
        with self.assertRaises(platform.PlatformReadbackError):self.reader.inspect(self.request)

    def test_core_group_omitted_or_empty_only_noncore_missing_denied(self):
        self.enqueue();self.reader.inspect(self.request)
        objs=self.objects()
        for obj in objs[3:15]:
            obj['spec']['resourceAttributes'].setdefault('group','')
        self.enqueue(objs);self.reader.inspect(self.request)
        for field,index in [('group',7),('namespace',3),('name',3),('version',3)]:
            objs=self.objects();objs[index]['spec']['resourceAttributes'].pop(field,None);self.enqueue(objs)
            with self.subTest(field=field),self.assertRaises(platform.PlatformReadbackError):self.reader.inspect(self.request)

    def test_http_errors_and_wrong_review_code_never_become_success(self):
        for status in (200,301,401,403,404,429,500):
            self.queue=[response(core.encoded(self.objects()[0]),status)]
            with self.subTest(status=status),self.assertRaises(platform.PlatformReadbackError) as e:self.reader.inspect(self.request)
            self.assertNotIn(TOKEN,str(e.exception));self.assertIsNone(e.exception.__cause__)
        self.queue=[response(b'{"a":1,"a":2}',201)]
        with self.assertRaises(platform.PlatformReadbackError):self.reader.inspect(self.request)

    def test_binding_credential_progress_deadline_rejected_before_io(self):
        for request in (replace(self.request,binding=replace(self.binding,run_id='other')),
                        replace(self.request,credential=BearerCredential(TOKEN)),replace(self.request,progress=Progress(responses=(b'other',))),
                        replace(self.request,deadline=100)):
            with self.assertRaises(platform.PlatformReadbackError):self.reader.inspect(request)
        self.socket_factory.assert_not_called()

    def test_shared_deadline_and_progress_rechecked_after_io(self):
        for kind in ('deadline','progress'):
            self.clock,self.progress=100,Progress();self.reader._last=None;self.enqueue()
            sock=FakeSocket(self.queue.pop(0));recv=sock.recv
            def consume(n):
                data=recv(n)
                if kind=='deadline':self.clock=111
                else:self.progress=Progress(responses=(b'changed',))
                return data
            sock.recv=consume;self.queue.insert(0,sock);self.sockets=[]
            with self.subTest(kind=kind),self.assertRaises(platform.PlatformReadbackError):self.reader.inspect(self.request)
            self.assertEqual(1,len(self.sockets));self.assertTrue(sock.closed)

    def test_fresh_reads_after_success_and_no_authority_promotion(self):
        self.enqueue();first=self.reader.inspect(self.request)
        self.enqueue();second=self.reader.inspect(self.request)
        self.assertIsNot(first,second);self.assertEqual(36,len(self.sockets))
        verifier=Verifier(providers=None,credential=self.credential,progress=lambda:self.progress,budget_seconds=10,
                          wall=lambda:100,monotonic=lambda:self.clock)
        with self.assertRaisesRegex(VerificationError,'^EVIDENCE_INVALID$'):
            verifier.read(lambda r:first,PlatformEvidence,self.request)

    def test_clock_reversal_and_unexpected_raw_exceptions_masked(self):
        self.enqueue();self.reader.inspect(self.request);self.clock=99
        with self.assertRaisesRegex(platform.PlatformReadbackError,'^PLATFORM_READBACK_CLOCK_INVALID$'):self.reader.inspect(self.request)
        self.reader._last=None;self.clock=100;self.reader._exchange=Mock(side_effect=RuntimeError(TOKEN))
        with self.assertRaisesRegex(platform.PlatformReadbackError,'^PLATFORM_READBACK_UNAVAILABLE$') as e:self.reader.inspect(self.request)
        self.assertNotIn(TOKEN,str(e.exception));self.assertIsNone(e.exception.__cause__)

    def issuance(self, request):
        return PlatformEvidence(request,self.binding,
            Provenance('synthetic:issuance','synthetic-revision',self.expected.username,'synthetic:issuer-authority'),
            self.credential,self.credential.fingerprint,'synthetic:issuer','synthetic:audience',900,
            1000,100,CA,'192.0.2.10',b'SYNTHETIC_ISSUANCE_RECEIPT')

    def checked_adapter(self, resolver=None):
        return platform.ReadbackCheckedPlatformAdapter(reader=self.reader,
            issuer_resolver=self.issuance if resolver is None else resolver,wall=lambda:self.clock)

    def test_platform_port_connects_original_issuance_and_fresh_reads(self):
        from delivery_provider import AuthorityAdapters,OperationalProviders
        self.enqueue();resolver=Mock(side_effect=self.issuance);adapter=self.checked_adapter(resolver)
        unused=Mock(side_effect=AssertionError('NOT_PART_OF_PLATFORM_READBACK'))
        providers=OperationalProviders(self.binding,self.credential,lambda:self.progress,
            rendered=b'SYNTHETIC',contract=b'SYNTHETIC',validator=unused,
            adapters=AuthorityAdapters(unused,adapter,unused,unused,unused),diagnostic_sink=unused,
            wall=lambda:100,monotonic=lambda:self.clock)
        with patch.object(transport.ssl,'SSLContext',return_value=self.context):
            value=providers.platform(self.request)
        self.assertEqual(2,resolver.call_count);self.assertIs(value.request,self.request)
        self.assertIs(value.credential,self.credential);self.assertEqual(18,len(self.sockets));unused.assert_not_called()

    def test_issuer_absent_json_or_observation_not_platform_authority(self):
        with self.assertRaises(platform.PlatformReadbackError):
            platform.ReadbackCheckedPlatformAdapter(reader=self.reader,issuer_resolver=None)
        for value in (None,{'state':'ACTIVE'},{'expires_at':1000}):
            with self.subTest(value=value),self.assertRaises(platform.PlatformReadbackError):
                self.checked_adapter(lambda r:value)(self.request)
        self.socket_factory.assert_not_called()

    def test_issuer_proof_binding_handle_target_ca_principal_and_receipt_checked(self):
        good=self.issuance(self.request)
        cases=[replace(good,binding=replace(self.binding,run_id='other')),
               replace(good,request=replace(self.request,deadline=109)),
               replace(good,credential=BearerCredential(TOKEN)),replace(good,credential_fingerprint='f'*64),
               replace(good,endpoint_ip='192.0.2.11'),replace(good,ca_pem=CA+b'x'),
               replace(good,provenance=replace(good.provenance,principal='other')),
               replace(good,issuer='PENDING'),replace(good,audience=''),replace(good,raw_receipt=b'other')]
        for value in cases:
            with self.subTest(value=value.provenance.principal),self.assertRaises(platform.PlatformReadbackError):
                self.checked_adapter(lambda r:value)(self.request)
        self.socket_factory.assert_not_called()

    def test_issuer_ttl_and_fresh_replacement_or_failure_denied(self):
        good=self.issuance(self.request)
        for value in (replace(good,requested_seconds=901),replace(good,requested_seconds=True),
                      replace(good,issued_at=101),replace(good,expires_at=100),
                      replace(good,expires_at=1001),replace(good,expires_at=float('inf'))):
            with self.subTest(expiry=value.expires_at),self.assertRaises(platform.PlatformReadbackError):
                self.checked_adapter(lambda r:value)(self.request)
        self.socket_factory.assert_not_called()
        self.enqueue();resolver=Mock(side_effect=[good,replace(good,provenance=replace(good.provenance,revision='replaced'))])
        with self.assertRaisesRegex(platform.PlatformReadbackError,'^PLATFORM_ISSUER_CHANGED$'):
            self.checked_adapter(resolver)(self.request)
        self.enqueue();resolver=Mock(side_effect=[good,RuntimeError(TOKEN)])
        with self.assertRaisesRegex(platform.PlatformReadbackError,'^PLATFORM_ISSUER_UNVERIFIED$') as e:
            self.checked_adapter(resolver)(self.request)
        self.assertNotIn(TOKEN,str(e.exception));self.assertIsNone(e.exception.__cause__)

    def test_issuer_remaining_lifetime_narrows_same_request_io(self):
        self.enqueue();good=replace(self.issuance(self.request),expires_at=105)
        resolver=Mock(return_value=good);adapter=self.checked_adapter(resolver)
        sock=FakeSocket(self.queue.pop(0));recv=sock.recv
        def expired(n):
            data=recv(n);self.clock=106;return data
        sock.recv=expired;self.queue.insert(0,sock)
        with self.assertRaises(platform.PlatformReadbackError):adapter(self.request)
        self.assertEqual(1,len(self.sockets));self.assertEqual(1,resolver.call_count)
        self.assertTrue(sock.closed)

    def test_issuer_delay_or_progress_change_does_not_start_api_reads(self):
        for kind in ('deadline','progress'):
            self.clock,self.progress=100,Progress();self.reader._last=None
            def resolver(r):
                value=self.issuance(r)
                if kind=='deadline':self.clock=111
                else:self.progress=Progress(responses=(b'changed',))
                return value
            with self.subTest(kind=kind),self.assertRaises(platform.PlatformReadbackError):
                self.checked_adapter(resolver)(self.request)
        self.socket_factory.assert_not_called()

    def test_local_close_prevents_reuse_without_revoking_external_handle(self):
        resolver=Mock(side_effect=self.issuance);adapter=self.checked_adapter(resolver)
        adapter.close();adapter.close()
        self.assertIsNone(self.reader._token);self.assertIsNone(self.reader.credential)
        self.assertIsNone(adapter.resolver);self.assertEqual(TOKEN,self.credential.token)
        with self.assertRaisesRegex(platform.PlatformReadbackError,'^PLATFORM_READBACK_CLOSED$'):adapter(self.request)
        with self.assertRaisesRegex(platform.PlatformReadbackError,'^PLATFORM_READBACK_CLOSED$'):self.reader.inspect(self.request)
        resolver.assert_not_called();self.socket_factory.assert_not_called()


if __name__=='__main__':unittest.main()
