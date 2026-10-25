"""Offline explicit-adoption checks; synthetic native context/clock/API only.

Real FDS constants are expected CODE choices, never live environment evidence.
No credential lookup, login, issuance, network or host write is permitted here.
"""
import contextlib
import runpy
import io
import json
from dataclasses import replace
from pathlib import PurePosixPath
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import delivery_fds_run_20261025 as active
import delivery_fds_observed_baseline as baseline
import delivery_fds_operating as operating
import delivery_fds_operating_policy as policy
import delivery_fds_producer_inputs as producer
import delivery_fds_site_inputs as profile
import delivery_job as job
import delivery_job_site as default_site
import delivery_site as site
from delivery_token_request import TokenRequestIssuer
from test_delivery_job import CONTEXT


class ActiveRunTests(unittest.TestCase):
    def setUp(self):
        self.blockers=[]
        for target in ('socket.socket','socket.create_connection','socket.getaddrinfo',
                       'subprocess.run','getpass.getpass'):
            p=patch(target,side_effect=AssertionError('NO_LIVE_IO'))
            self.blockers.append(p.start());self.addCleanup(p.stop)
        p=patch.object(TokenRequestIssuer,'issue',side_effect=AssertionError('NO_ISSUANCE'))
        self.issue=p.start();self.addCleanup(p.stop)
        p=patch.object(site,'Path',PurePosixPath)
        p.start();self.addCleanup(p.stop)
        self.mono=Mock(return_value=1000.0)
        self.wall=Mock(return_value=active.STARTS_AT+20)

    def inputs(self,context=CONTEXT,command='deploy',**changes):
        return active.selected_inputs(context,command,monotonic=changes.get('monotonic',self.mono),
                                      wall=changes.get('wall',self.wall))

    def test_import_and_explicit_gate_are_inert_and_do_not_enable_generic_default(self):
        with patch.object(active.time,'time',side_effect=AssertionError('NO_IMPORT_CLOCK')), \
             patch.object(active.time,'monotonic',side_effect=AssertionError('NO_IMPORT_CLOCK')):
            isolated=runpy.run_path(active.__file__,run_name='SYNTHETIC_IMPORT_ONLY')
            declaration=isolated['declare_site']()
            gate=job.workflow_gate(declaration=declaration,context=CONTEXT,
                                  revision=lambda:CONTEXT.workflow_sha)
        self.assertTrue(gate['dispatch_configured'])
        self.assertFalse(gate['execution_authorized'])
        self.assertIsNone(default_site.SITE)
        for block in self.blockers:block.assert_not_called()
        self.issue.assert_not_called()

    def test_actual_context_derives_only_native_input_path_without_new_runtime_values(self):
        value=self.inputs()
        b=value['config'].binding
        self.assertEqual(active.RUN_ID,b.run_id)
        self.assertEqual('deploy',b.action)
        self.assertTrue(str(value['config'].input_root).endswith('/'+CONTEXT.run_id+'-1'))
        self.assertEqual(active.PLATFORM_PIN.sha256,b.platform_receipt_sha256)
        self.assertEqual(producer.SUPPLY_SHA256,b.supply_receipt_sha256)
        self.assertEqual(baseline.METADATA_PIN,value['baseline_grant'].original_pin)
        self.assertEqual(baseline.SNAPSHOT_ROOT,value['baseline_root'])
        self.assertEqual(baseline.SNAPSHOT_FILE,value['baseline_file'])
        self.assertEqual(baseline.PREVIOUS_IMAGES,value['selection'].producer.facts.previous_images)
        self.assertEqual(profile.SUBJECT,value['selection'].issuer_grant.principal)
        self.assertEqual(value['selection'].approval.original_pin,value['finish_scope'].original_pin)
        self.assertEqual(1600,value['deadline'])
        self.assertTrue(all(route.local_use_until==2260 for route in value['routes']))
        self.assertEqual(active.SOURCE_REPOSITORIES,tuple(r.repository for r in value['routes']))
        for g in (value['selection'].approval,value['selection'].issuer_grant,
                  value['selection'].producer.grant,value['baseline_grant'],value['finish_scope']):
            self.assertEqual((b,policy.CONTROL_PIN,active.STARTS_AT,policy.CONSUMPTION_END),
                             (g.binding,g.control_pin,g.starts_at,g.expires_at))
        self.issue.assert_not_called()

    def test_static_target_groups_are_expectations_not_new_observation_or_receipt(self):
        value=self.inputs()
        self.assertIsNone(value['selection'].baseline)
        self.assertEqual(profile.SUBJECT_UID,value['selection'].target.uid)
        self.assertEqual(('system:authenticated','system:serviceaccounts','system:serviceaccounts:ci-cd'),
                         value['selection'].subject.groups)
        self.assertNotIn('receipt',value)
        self.assertNotIn('credential',value)
        self.assertNotEqual(active.PLATFORM_PIN.sha256,baseline.SNAPSHOT_SHA256)

    def test_wrong_action_attempt_and_first_start_day_are_rejected_before_io(self):
        cases=[(CONTEXT,'restore'),(CONTEXT,'preflight'),(CONTEXT,'status'),
               (replace(CONTEXT,attempt=2),'deploy'),(None,'deploy')]
        for context,command in cases:
            with self.subTest(command=command),self.assertRaises((active.ActiveRunError,job.JobError)):
                self.inputs(context,command)
        for when in (active.STARTS_AT-1,policy.CONSUMPTION_END,float('nan'),True,
                     active.datetime.fromisoformat('2026-10-26T00:00:00+09:00').timestamp()):
            with self.subTest(when=when),self.assertRaises(active.ActiveRunError):
                self.inputs(wall=lambda:when)
        self.issue.assert_not_called()

    def test_private_supplier_uses_same_binding_context_originals_and_known_helper_route(self):
        provider=Mock()
        sources=[{'repository':r.owner+'/'+r.name,'token':'SYNTHETIC_SOURCE_TOKEN_0123456789'}
                 for r in active.SOURCE_REPOSITORIES]
        with patch.object(operating,'build_observed_login_supplier',return_value='SYNTHETIC') as build:
            result=active.build_supplier(CONTEXT,login_provider=provider,source_tokens=sources,
                                         operation_deadline=1500,monotonic=self.mono,wall=self.wall)
        self.assertEqual('SYNTHETIC',result)
        args,kw=build.call_args
        self.assertEqual(self.inputs()['config'].binding,args[0].binding)
        self.assertIs(CONTEXT,kw['context'])
        self.assertEqual(active.SOURCE_REPOSITORIES,kw['repositories'])
        self.assertIs(sources,kw['source_tokens'])
        self.assertEqual((1500,1560),(kw['operation_deadline'],kw['cleanup_deadline']))
        provider.close.assert_not_called()

    def test_expired_original_input_deadline_cannot_be_renewed_by_supplier_construction(self):
        with patch.object(operating,'build_observed_login_supplier') as build:
            with self.assertRaises(active.ActiveRunError):
                active.build_supplier(CONTEXT,login_provider=Mock(),source_tokens=[],
                    operation_deadline=999,monotonic=self.mono,wall=self.wall)
        build.assert_not_called()

    def test_unsupported_native_command_cannot_publish_ready_or_read_credentials(self):
        with patch.object(operating,'assemble_observed_native_job') as assemble:
            for command in ('preflight','restore'):
                with self.assertRaises(policy.OperatingPolicyError):
                    active.declare_site().build(CONTEXT,command)
        assemble.assert_not_called()
        self.issue.assert_not_called()

    def native_run(self):
        return {'id':int(CONTEXT.run_id),'head_sha':CONTEXT.workflow_sha,'head_branch':'main',
            'event':'workflow_dispatch','run_attempt':1,'status':'in_progress',
            'path':'.github/workflows/p2-rosa-delivery.yml','actor':{'id':277643063},
            'repository':{'id':1362221024,'owner':{'id':315682664}},
            'created_at':active.datetime.fromtimestamp(active.STARTS_AT+1,active.timezone.utc).isoformat()}

    def test_issuer_uses_authenticated_native_metadata_not_caller_shape_as_authority(self):
        raw=self.native_run()
        with patch.object(active,'_github',return_value=json.dumps(raw).encode()), \
             patch.object(active.time,'time',return_value=active.STARTS_AT+20):
            c=active._actual_context(CONTEXT.run_id,CONTEXT.workflow_sha)
        self.assertEqual(CONTEXT,c)
        for key,val in (('status','completed'),('run_attempt',2),('actor',{'id':1}),
                        ('head_sha','0'*40),('path','.github/workflows/other.yml')):
            with self.subTest(key=key),patch.object(active,'_github',return_value=json.dumps(raw|{key:val}).encode()), \
                 self.assertRaises(active.ActiveRunError):
                active._actual_context(CONTEXT.run_id,CONTEXT.workflow_sha)

    def run_issuer(self,supplier,*,provider=None,build_error=None):
        provider=provider or Mock()
        source='SYNTHETIC_SOURCE_TOKEN_0123456789'
        def gh(args):return b'GWONUK\n' if args[0]=='api' else source.encode()
        fake_pwd=SimpleNamespace(getpwuid=lambda uid:SimpleNamespace(pw_name='server01_ansible'))
        stream=io.StringIO()
        with patch.dict('sys.modules',{'pwd':fake_pwd}),patch.object(active.sys,'platform','linux'), \
             patch.object(active.os,'geteuid',return_value=1000,create=True), \
             patch.object(active.socket,'gethostname',return_value='ansible01'), \
             patch.object(active,'_actual_context',return_value=CONTEXT), \
             patch.object(active,'selected_inputs',return_value={}), \
             patch.object(active,'_github',side_effect=gh), \
             patch.object(active,'read_login_provider',return_value=provider), \
             patch.object(active,'build_supplier',return_value=supplier,side_effect=build_error), \
             contextlib.redirect_stdout(stream):
            rc=active.issuer_main(['--run-id',CONTEXT.run_id,'--workflow-sha',CONTEXT.workflow_sha])
        self.assertNotIn(source,stream.getvalue())
        return rc,json.loads(stream.getvalue()),provider

    def test_issuer_forward_is_not_job_acceptance_and_borrowed_provider_transfers_once(self):
        supplier=Mock()
        supplier.send.return_value={'credential_forwarded':True,'actor_server_revocation':'ACKNOWLEDGED'}
        supplier.status.return_value=supplier.send.return_value
        supplier.take_receipt.return_value=object()
        supplier.take_login_metadata.return_value={'oauth_object_name':'SYNTHETIC_HASHED_NAME','expires_in':900}
        supplier.close.return_value=True
        rc,body,provider=self.run_issuer(supplier)
        self.assertEqual(0,rc)
        self.assertEqual('FORWARDED_NOT_JOB_ACCEPTANCE',body['status'])
        supplier.send.assert_called_once_with(include_original=True)
        supplier.close.assert_called_once()
        provider.close.assert_not_called()

    def test_constructor_failure_closes_caller_provider_and_suppresses_error_text(self):
        rc,body,provider=self.run_issuer(Mock(),build_error=RuntimeError('SYNTHETIC_PASSWORD'))
        self.assertEqual(2,rc);self.assertEqual('STOPPED',body['status'])
        self.assertNotIn('SYNTHETIC_PASSWORD',json.dumps(body))
        provider.close.assert_called_once()

    def test_send_status_receipt_errors_still_take_recovery_handoff_and_close(self):
        for failed in ('send','status','take_receipt'):
            s=Mock();s.send.return_value={};s.status.return_value={}
            getattr(s,failed).side_effect=RuntimeError('SYNTHETIC_SECRET')
            s.take_login_metadata.return_value={'oauth_object_name':'SYNTHETIC_HASH'}
            s.close.return_value=True
            rc,body,_=self.run_issuer(s)
            self.assertEqual(2,rc)
            self.assertEqual('SYNTHETIC_HASH',body['private_login_recovery_metadata']['oauth_object_name'])
            self.assertNotIn('SYNTHETIC_SECRET',json.dumps(body))
            s.take_login_metadata.assert_called_once();s.close.assert_called_once()

    def test_cancel_during_cleanup_preserves_cancel_and_independent_later_close(self):
        s=Mock();s.send.return_value={};s.status.return_value={}
        s.take_receipt.side_effect=KeyboardInterrupt('SYNTHETIC_SECRET')
        s.take_login_metadata.return_value=None;s.close.return_value=True
        rc,body,_=self.run_issuer(s)
        self.assertEqual(130,rc);self.assertEqual('STOPPED',body['status'])
        s.take_login_metadata.assert_called_once();s.close.assert_called_once()


if __name__=='__main__':unittest.main()
