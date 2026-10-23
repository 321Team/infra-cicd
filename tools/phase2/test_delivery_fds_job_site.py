"""SYNTHETIC plans/UIDs/sources only. Never operating SITE, issuer or Runtime."""
import contextlib
import io
import os
from pathlib import Path
import socket
import sys
import unittest
from unittest.mock import Mock,patch

import delivery_fds_job_site as assembly
import delivery_fds_site_inputs as fds
import delivery_job as job
import delivery_job_parent as parent
import delivery_job_rendezvous as rendezvous
import delivery_job_site as declaration
import delivery_job_sources as sources
import delivery_runtime_site as runtime
import test_delivery_fds_existing_inputs as existing_fixture
from test_delivery_job import CONTEXT,RUNNER


class AssemblyTests(unittest.TestCase):
    def setUp(self):
        self.f=existing_fixture.ExistingProfileTests('runTest');self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.clock=Mock(side_effect=AssertionError('NO_CLOCK_AT_CONSTRUCTION'))
        self.wall=Mock(side_effect=AssertionError('NO_WALL_AT_CONSTRUCTION'))
        repo=self.f.plan.approval.original_pin.repository
        self.wire=sources.SourceRoleWiring(binding=self.f.config.binding,
            routes=tuple(sources.RoleRoute(role,repo,repo) for role in sources.ROLES+('baseline',)),
            wall=self.wall,monotonic=self.clock)
        a=self.f.args()
        self.args={k:a[k] for k in ('config','plan','finish_slot','expected_peer','routes','deadline')}
        self.publish=Mock(side_effect=AssertionError('NO_PUBLISH_AT_CONSTRUCTION'))
        self.args.update(runner=RUNNER,wire=self.wire,expected_helper_uid=1001,
                         monotonic=self.clock,publish_ready=self.publish)

    def build(self,**kw):return assembly.assemble_existing_job_site(**(self.args|kw))

    def test_exact_native_components_same_binding_peer_clock_deadline_and_borrowed_plan(self):
        with patch.object(Path,'open',side_effect=AssertionError('NO_FILE')), \
             patch.object(socket,'socket',side_effect=AssertionError('NO_SOCKET')):
            value=self.build()
        self.assertIs(type(value),job.JobSite);self.assertIs(type(value.build),rendezvous.OwnedRendezvousBuilder)
        builder,owner=value.build._parent,value.build._owner
        self.assertIs(type(builder),parent.ParentJobBuilder);self.assertIs(builder._plan,self.f.plan)
        self.assertIs(builder._wire,self.wire);self.assertIs(builder._clock,self.clock)
        self.assertIs(owner._clock,self.clock);self.assertIs(builder._peer,self.args['expected_peer'])
        self.assertEqual(self.args['deadline'],owner._deadline);self.assertEqual(1001,owner._uid)
        self.assertEqual(owner.publish_ready,builder._publish);self.assertEqual(owner.open_channel,builder._open)
        self.assertIsNone(owner._listener);self.clock.assert_not_called();self.wall.assert_not_called()
        self.publish.assert_not_called();self.assertIsNone(declaration.SITE)

    def test_materializer_is_inert_and_wrapped_in_existing_prepared_validation(self):
        materialize=Mock(side_effect=AssertionError('NOT_ISSUED'))
        value=self.build(plan=None,materialize_plan=materialize)
        self.addCleanup(value.build._parent._plan.close)
        self.assertIs(type(value.build._parent._plan),runtime.PreparedAuthorityPlan)
        self.assertEqual(self.f.config.binding,value.build._parent._plan.binding)
        materialize.assert_not_called();self.clock.assert_not_called()

    def test_xor_and_missing_required_inputs_fail_before_owner_construction(self):
        for kw in (dict(plan=None),dict(materialize_plan=Mock()),dict(runner=None),
                   dict(expected_helper_uid=None),dict(expected_helper_uid=0),
                   dict(expected_helper_uid=True),dict(expected_helper_uid=0xFFFFFFFF),
                   dict(expected_peer=None),dict(wire=None),dict(publish_ready=None),
                   dict(monotonic=None),dict(deadline=float('nan'))):
            with self.subTest(kw=tuple(kw)),patch.object(rendezvous,'UnixJobRendezvous') as create:
                with self.assertRaises(assembly.SiteAssemblyError):self.build(**kw)
                create.assert_not_called()

    def test_four_role_layout_foreign_wire_and_another_clock_never_publish(self):
        repo=self.f.plan.approval.original_pin.repository
        four=sources.SourceRoleWiring(binding=self.f.config.binding,
            routes=tuple(sources.RoleRoute(role,repo,repo) for role in sources.ROLES),
            wall=self.wall,monotonic=self.clock)
        for kw in (dict(wire=four),dict(wire=Mock()),dict(monotonic=lambda:1000)):
            with self.subTest(kw=tuple(kw)),self.assertRaises(assembly.SiteAssemblyError):self.build(**kw)
        self.publish.assert_not_called();self.clock.assert_not_called()

    def test_status_and_gate_never_create_listener_or_consume_materializer(self):
        materialize=Mock(side_effect=AssertionError('NO_MATERIALIZE'))
        value=self.build(plan=None,materialize_plan=materialize)
        self.addCleanup(value.build._parent._plan.close)
        with patch.object(socket,'socket',side_effect=AssertionError('NO_SOCKET')),contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(0,job.main(['status'],declaration=value))
            self.assertEqual(0,job.main(['gate'],declaration=value,context=CONTEXT,revision=lambda:CONTEXT.workflow_sha))
        self.assertFalse(value.build._used);self.assertFalse(value.build._owner._published)
        materialize.assert_not_called();self.publish.assert_not_called();self.clock.assert_not_called()

    def test_full_plan_failure_does_not_close_borrowed_slot_or_wire(self):
        with patch.object(self.args['finish_slot'],'close',create=True) as close_slot, \
             patch.object(self.wire,'close',create=True) as close_wire:
            with self.assertRaises(assembly.SiteAssemblyError):self.build(plan=self.f.f.plan)
            close_slot.assert_not_called();close_wire.assert_not_called()
        self.assertFalse(self.wire._used)

    def test_wrapper_failure_closes_internal_prepared_but_not_caller_objects(self):
        made=[];original=runtime.PreparedAuthorityPlan
        # Observe actual internal objects without substituting their exact type.
        original_init=original.__init__
        def observe(obj,*args,**kw):original_init(obj,*args,**kw);made.append(obj)
        with patch.object(original,'__init__',new=observe), \
             patch.object(rendezvous,'OwnedRendezvousBuilder',side_effect=ValueError('SYNTHETIC_SECRET')):
            with self.assertRaises(assembly.SiteAssemblyError) as caught:
                self.build(plan=None,materialize_plan=Mock())
        self.assertNotIn('SYNTHETIC_SECRET',str(caught.exception));self.assertEqual(1,len(made))
        self.assertTrue(made[0]._closed);self.assertIsNone(made[0]._materialize)
        self.assertFalse(self.wire._used);self.clock.assert_not_called()

    def test_late_site_failure_keeps_borrowed_full_plan_and_fixed_error(self):
        made=[];original=parent.ParentJobBuilder.__init__
        def observe(value,*args,**kw):original(value,*args,**kw);made.append(value)
        with patch.object(parent.ParentJobBuilder,'__init__',new=observe), \
             patch.object(job.JobSite,'check',side_effect=ValueError('SYNTHETIC_SECRET')):
            with self.assertRaisesRegex(assembly.SiteAssemblyError,'CONFIGURATION_INVALID') as caught:
                self.build()
        self.assertEqual(1,len(made));self.assertIs(made[0]._plan,self.f.plan)
        self.assertNotIn('SYNTHETIC_SECRET',str(caught.exception))
        self.assertFalse(self.wire._used);self.clock.assert_not_called()

    def test_interrupt_and_owner_cleanup_failure_do_not_expose_callback_text(self):
        for error in (KeyboardInterrupt('SYNTHETIC_SECRET'),SystemExit('SYNTHETIC_SECRET')):
            with patch.object(assembly.existing,'connect_existing_parent',side_effect=error):
                with self.assertRaises(type(error)) as caught:self.build()
            self.assertNotIn('SYNTHETIC_SECRET',str(caught.exception))
        with patch.object(assembly.existing,'connect_existing_parent',side_effect=ValueError('SECRET')), \
             patch.object(rendezvous.UnixJobRendezvous,'close',return_value=False):
            with self.assertRaisesRegex(assembly.SiteAssemblyError,'CLEANUP_FAILED'):self.build()


@unittest.skipUnless(sys.platform=='linux' and hasattr(os,'geteuid') and os.geteuid()>0,
                     'actual local rendezvous requires nonroot Linux')
class AssemblyLinuxTests(unittest.TestCase):
    def test_existing_profile_job_site_uses_actual_local_uid_parent_and_owned_invocation(self):
        import copy
        from dataclasses import replace
        import delivery_adapter as core
        from test_delivery_objects import owned
        import test_delivery_job_rendezvous as fixture
        f=fixture.RendezvousLinuxTests('runTest');f.setUp();self.addCleanup(f.doCleanups)
        self.assertEqual('ROLLOUT_OBSERVED',f.f.h.run()['status'])
        for i,key in enumerate(core.OBJECTS):
            path=core.path_for(key,f.f.h.binding);f.f.h.objects[path]=owned(f.f.h.objects[path],i)
        f.f.h.requests.clear()
        raw=tuple(core.encoded(f.f.h.objects[core.path_for(key,f.f.h.binding)]) for key in core.OBJECTS)
        pin=replace(f.f.pins['producer'],path='baseline',sha256=core.digest(b'SYNTHETIC_BASELINE'))
        grant=replace(f.f.grants['approval'],original_pin=pin,adoption_reference='synthetic:existing-owner-adoption')
        plan=replace(f.f.plan(),baseline=runtime.BaselineAdoption(grant,raw))
        b,issuer,subject=f.f.h.binding,plan.issuer.facts,plan.subject
        values=dict(API_ORIGIN=b.api_origin,CLUSTER_UUID=b.cluster_id,NAMESPACE_UID=b.namespace_uid,
            SUBJECT=subject.username,SUBJECT_UID=subject.uid,ISSUER=issuer.issuer,AUDIENCE=issuer.audience,
            CA_PEM=issuer.ca_pem,CA_PEM_SHA256=b.ca_sha256,SOURCE_COMMIT=b.source_commit,
            CONTRACT_SHA256=b.contract_sha256,RENDERED_SHA256=b.rendered_sha256,ROUTE_HOST=b.route_host,IMAGES=b.images)
        wire=sources.SourceRoleWiring(binding=b,routes=tuple(sources.RoleRoute(role,f.s.repo,f.s.repo)
            for role in sources.ROLES+('baseline',)),wall=lambda:f.f.h.now,monotonic=f.clock)
        with patch.multiple(fds,**values):
            value=assembly.assemble_existing_job_site(runner=RUNNER,config=f.f.config(),plan=plan,
                finish_slot=f.f.slot,expected_peer=f.peer,routes=f.routes,wire=wire,
                expected_helper_uid=os.geteuid(),deadline=f.deadline,monotonic=f.clock,publish_ready=f.sender)
            invocation=value.build(CONTEXT,'deploy')
        self.assertIs(type(invocation),job.RuntimeInvocation);self.assertIs(invocation.plan,plan)
        self.assertEqual(5,len(wire.roles));self.assertIsNotNone(invocation.source_session)
        self.assertTrue(invocation.close());self.assertTrue(value.build._owner._closed)
        self.assertIsNone(value.build._owner._listener);self.assertIsNone(declaration.SITE)
        self.assertEqual([],f.f.h.requests);self.assertEqual([],f.f.p.c.storage.claims)


if __name__=='__main__':unittest.main()
