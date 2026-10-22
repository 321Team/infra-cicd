"""Public ready callbacks and SYNTHETIC memory frames; no runtime/issuer/host."""
import contextlib
import copy
from dataclasses import replace
import io
import pickle
import threading
import unittest
from unittest.mock import Mock, patch

import delivery_crosshost_ingress as ingress
import delivery_job as job
import delivery_job_handoff as handoff
import delivery_job_parent as parent
import delivery_job_site as declaration
import test_delivery_crosshost_ingress as fixture
from test_delivery_job import CONTEXT


class ParentTests(unittest.TestCase):
    setUp = fixture.IngressTests.setUp

    def builder(self, **changes):
        self.channels = []
        self.publish = Mock()
        def open_channel(request, deadline):
            raw = ingress.encode_frame(request, rosa_token=self.f.credential.token,
                source_tokens=[{'repository': self.s.repo.owner+'/'+self.s.repo.name, 'token': fixture.fixture.TOKEN}])
            memory = fixture.MemoryChannel(raw, self.peer)
            self.channels.append(memory)
            return memory.channel
        self.open = Mock(side_effect=open_channel)
        args = dict(config=self.f.config(), plan=self.f.plan(), finish_slot=self.f.slot,
            expected_peer=self.peer, routes=self.routes, deadline=self.deadline, wire=self.s.wire,
            publish_ready=self.publish, open_channel=self.open, monotonic=lambda:self.f.h.mono)
        return parent.ParentJobBuilder(**(args | changes))

    def test_exact_job_public_ready_then_channel_bundle_handoff_once(self):
        builder=self.builder();marker=object()
        with patch.object(handoff,'build_owned_invocation',return_value=marker) as build:
            self.assertIs(marker,builder(CONTEXT,'deploy'))
        ready,deadline=self.publish.call_args.args
        self.assertEqual((ready,deadline),self.open.call_args.args)
        self.assertEqual(CONTEXT.run_id,ready['target']['run_id'])
        self.assertNotIn(fixture.fixture.TOKEN,str(ready));self.assertNotIn(self.f.credential.token,str(ready))
        self.assertEqual(self.deadline,build.call_args.kwargs['handoff_deadline'])
        self.assertEqual(1,self.channels[0].close_calls)
        with self.assertRaisesRegex(parent.ParentBuildError,'CONSUMED'):builder(CONTEXT,'deploy')
        self.assertEqual(1,self.publish.call_count);self.assertEqual(1,self.open.call_count)

    def test_shape_context_action_slot_or_plan_rejected_before_publication(self):
        cases=[(dict(config=None),CONTEXT,'deploy'),(dict(plan=None),CONTEXT,'deploy'),
               (dict(finish_slot=None),CONTEXT,'deploy'),
               (dict(plan=replace(self.f.plan(),approval=replace(self.f.plan().approval,
                    binding=replace(self.f.h.binding,route_host='other.invalid')))),CONTEXT,'deploy'),
               ({},replace(CONTEXT,attempt=True),'deploy'),({},None,'deploy'),
               ({},CONTEXT,'status'),({},CONTEXT,'gate'),({},CONTEXT,'restore'),({},CONTEXT,True),
               (dict(expected_peer=None),CONTEXT,'deploy'),(dict(routes=()),CONTEXT,'deploy'),
               (dict(deadline=self.f.h.mono),CONTEXT,'deploy')]
        with patch.object(handoff,'build_owned_invocation') as build:
            for changes,ctx,command in cases:
                builder=self.builder(**changes)
                with self.assertRaises(parent.ParentBuildError):builder(ctx,command)
                self.publish.assert_not_called();self.open.assert_not_called()
        build.assert_not_called()

    def test_publisher_cannot_rewrite_request_for_open_or_receive(self):
        def publish(request,deadline):
            request['target']['run_id']='456';request['nonce']='0'*64
            request['repositories'].append('synthetic/other')
        builder=self.builder(publish_ready=publish)
        with patch.object(handoff,'build_owned_invocation',return_value='SYNTHETIC'):
            self.assertEqual('SYNTHETIC',builder(CONTEXT,'preflight'))
        opened=self.open.call_args.args[0]
        self.assertEqual(CONTEXT.run_id,opened['target']['run_id'])
        self.assertEqual(1,len(opened['repositories']));self.assertNotEqual('0'*64,opened['nonce'])

    def test_publish_or_open_failure_closes_nonce_receiver_and_no_handoff(self):
        original=ingress.CrossHostIngress
        for stage in ('publish','open'):
            receivers=[]
            def create(**args):
                value=original(**args);receivers.append(value);return value
            def bad(*args):raise ValueError(fixture.fixture.TOKEN)
            builder=self.builder(**({'publish_ready':bad} if stage=='publish' else {'open_channel':bad}))
            with patch.object(ingress,'CrossHostIngress',side_effect=create),patch.object(handoff,'build_owned_invocation') as build:
                with self.assertRaisesRegex(parent.ParentBuildError,'^P2_PARENT_BUILD_FAILED$'):builder(CONTEXT,'deploy')
                build.assert_not_called()
            with self.assertRaisesRegex(ingress.IngressError,'CONSUMED'):receivers[0].request()
            with self.assertRaisesRegex(parent.ParentBuildError,'CONSUMED'):builder(CONTEXT,'deploy')
            if stage=='publish':self.open.assert_not_called()

    def test_late_publish_prevents_open_and_late_open_closes_transferred_channel(self):
        def late_publish(*a):self.f.h.mono=self.deadline
        builder=self.builder(publish_ready=late_publish)
        with self.assertRaisesRegex(parent.ParentBuildError,'DEADLINE_EXCEEDED'):builder(CONTEXT,'deploy')
        self.open.assert_not_called()
        self.f.h.mono=100;self.deadline=105;builder=self.builder();opening=self.open.side_effect
        def late_open(*args):
            channel=opening(*args);self.f.h.mono=105;return channel
        self.open.side_effect=late_open
        with self.assertRaisesRegex(parent.ParentBuildError,'DEADLINE_EXCEEDED'):builder(CONTEXT,'deploy')
        self.assertEqual(1,self.channels[0].close_calls)

    def test_borrowed_invalid_channel_is_not_arbitrarily_closed(self):
        borrowed=Mock();builder=self.builder(open_channel=lambda *a:borrowed)
        with self.assertRaisesRegex(parent.ParentBuildError,'CHANNEL_INVALID'):builder(CONTEXT,'deploy')
        borrowed.close.assert_not_called()

    def test_wrong_peer_does_not_call_handoff_and_closes_owned_channel(self):
        builder=self.builder();opening=self.open.side_effect
        def wrong(*args):
            result=opening(*args);self.channels[-1].peer=replace(self.peer,principal='other');return result
        self.open.side_effect=wrong
        with patch.object(handoff,'build_owned_invocation') as build,self.assertRaisesRegex(parent.ParentBuildError,'PEER_REJECTED'):
            builder(CONTEXT,'deploy')
        build.assert_not_called();self.assertEqual(0,self.channels[-1].read_calls)
        self.assertEqual(1,self.channels[-1].close_calls)

    def test_handoff_failure_has_fixed_code_no_retained_bundle_credentials(self):
        original=ingress.ReceivedCredentials.build_invocation;bundles=[]
        def capture(bundle,*a,**kw):bundles.append(bundle);return original(bundle,*a,**kw)
        builder=self.builder()
        with patch.object(ingress.ReceivedCredentials,'build_invocation',new=capture),patch.object(handoff,'build_owned_invocation',side_effect=ValueError(fixture.fixture.TOKEN)):
            with self.assertRaisesRegex(parent.ParentBuildError,'^INGRESS_INVOCATION_REJECTED$'):builder(CONTEXT,'deploy')
        self.assertIsNone(bundles[0]._rosa);self.assertIsNone(bundles[0]._sources)
        self.assertEqual(1,self.channels[0].close_calls)

    def test_interrupt_from_ready_does_not_echo_secret_or_open(self):
        for exception,args in ((KeyboardInterrupt(fixture.fixture.TOKEN),()),(SystemExit(fixture.fixture.TOKEN),(130,))):
            builder=self.builder(publish_ready=Mock(side_effect=exception))
            with self.assertRaises(type(exception)) as caught:builder(CONTEXT,'deploy')
            self.assertEqual(args,caught.exception.args);self.open.assert_not_called()

    def test_status_and_gate_do_not_call_explicit_builder_or_channel(self):
        builder=self.builder();site=job.JobSite(fixture.RUNNER,builder)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(0,job.main(['status'],declaration=site))
            self.assertEqual(0,job.main(['gate'],declaration=site,context=CONTEXT,revision=lambda:CONTEXT.workflow_sha))
        self.assertFalse(builder._used);self.publish.assert_not_called();self.open.assert_not_called()
        self.assertIsNone(declaration.SITE)

    def test_one_parent_cannot_be_built_by_two_threads(self):
        entered=threading.Event();release=threading.Event();results=[]
        def publish(*args):entered.set();self.assertTrue(release.wait(3))
        builder=self.builder(publish_ready=publish)
        def run():
            try:results.append(builder(CONTEXT,'deploy'))
            except BaseException as exc:results.append(exc)
        with patch.object(handoff,'build_owned_invocation',return_value='SYNTHETIC'):
            thread=threading.Thread(target=run);thread.start();self.assertTrue(entered.wait(3))
            try:
                with self.assertRaisesRegex(parent.ParentBuildError,'CONSUMED'):builder(CONTEXT,'deploy')
                self.open.assert_not_called()
            finally:release.set();thread.join(3)
        self.assertFalse(thread.is_alive());self.assertEqual(['SYNTHETIC'],results)

    def test_parent_repr_copy_pickle_do_not_expose_or_clone_capability(self):
        builder=self.builder()
        self.assertNotIn(str(self.f.config().input_root),repr(builder))
        for operation in (copy.copy,copy.deepcopy,pickle.dumps):
            with self.assertRaises(ingress.IngressError):operation(builder)


if __name__=='__main__':unittest.main()
