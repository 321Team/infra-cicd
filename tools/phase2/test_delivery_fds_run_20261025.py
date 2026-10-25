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
import warnings
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
                     active.datetime.fromisoformat('2026-10-27T00:00:00+09:00').timestamp()):
            with self.subTest(when=when),self.assertRaises(active.ActiveRunError):
                self.inputs(wall=lambda:when)
        self.issue.assert_not_called()

    def test_current_manual_restart_is_finite_and_keeps_original_control_and_baseline(self):
        self.assertEqual('FDS-P2-DELIVERY-20261026-A1', active.RUN_ID)
        self.assertEqual(6399779574, active.APPROVAL_PIN.comment_id)
        self.assertEqual('2026-10-26', active.datetime.fromtimestamp(active.STARTS_AT,active.KST).date().isoformat())
        self.assertEqual(policy.CONSUMPTION_END,active.EXPIRES_AT)
        self.assertEqual('6531ba8e301858c0e180c377d06908f895158a70',policy.CONTROL_PIN.commit)
        self.assertEqual(6395884121,baseline.METADATA_PIN.comment_id)
        # Boundary includes room for READY, token cap and cleanup; no late start.
        with self.assertRaises(active.ActiveRunError):
            self.inputs(wall=lambda:active.EXPIRES_AT-active.HANDOFF_SECONDS-900-active.CLEANUP_SECONDS)
        for block in self.blockers:block.assert_not_called()
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

    def run_issuer(self,supplier,*,provider=None,build_error=None,overrides=(),expect_source=True):
        provider=provider or Mock()
        source='SYNTHETIC_SOURCE_TOKEN_0123456789'
        def gh(args):
            self.assertEqual('api',args[0])
            return b'GWONUK\n'
        fake_pwd=SimpleNamespace(getpwuid=lambda uid:SimpleNamespace(pw_name='server01_ansible'))
        stream=io.StringIO()
        with patch.dict('sys.modules',{'pwd':fake_pwd}),patch.object(active.sys,'platform','linux'), \
             patch.object(active.os,'geteuid',return_value=1000,create=True), \
             patch.object(active.socket,'gethostname',return_value='ansible01'), \
             patch.object(active,'_actual_context',return_value=CONTEXT), \
             patch.object(active,'selected_inputs',return_value={}), \
             patch.object(active,'_github',side_effect=gh), \
             patch.object(active,'read_source_token',return_value=source) as source_input, \
             patch.object(active,'read_login_provider',return_value=provider) as login_input, \
             patch.object(active,'build_supplier',return_value=supplier,side_effect=build_error) as build, \
             contextlib.ExitStack() as extra, \
             contextlib.redirect_stdout(stream):
            for target,kw in overrides:extra.enter_context(patch(target,**kw))
            rc=active.issuer_main(['--run-id',CONTEXT.run_id,'--workflow-sha',CONTEXT.workflow_sha])
        if not expect_source:
            source_input.assert_not_called();login_input.assert_not_called();build.assert_not_called()
        if build.called:
            deadline=source_input.call_args.kwargs['deadline']
            self.assertEqual(deadline,login_input.call_args.kwargs['deadline'])
            self.assertEqual(deadline,build.call_args.kwargs['operation_deadline'])
        self.assertNotIn(source,stream.getvalue())
        return rc,json.loads(stream.getvalue()),provider

    def test_source_credential_requires_explicit_tty_input_and_never_calls_cli_token(self):
        source='SYNTHETIC_SOURCE_TOKEN_0123456789'
        with patch.object(active.sys,'stdin',SimpleNamespace(isatty=lambda:True)), \
             patch.object(active.getpass,'getpass',return_value=source) as prompt, \
             patch.object(active,'_github') as gh:
            got=active.read_source_token(deadline=100,monotonic=Mock(side_effect=[1,2]))
        self.assertEqual(source,got);prompt.assert_called_once();gh.assert_not_called()

    def test_source_input_rejects_nontty_expiry_and_backwards_clock_before_login(self):
        for tty,deadline,clocks in ((False,100,[1]),(True,1,[1]),
                                    (True,float('nan'),[1]),(True,10,[1,10]),(True,10,[2,1])):
            with self.subTest(tty=tty,deadline=deadline,clocks=clocks), \
                 patch.object(active.sys,'stdin',SimpleNamespace(isatty=lambda:tty)), \
                 patch.object(active.getpass,'getpass',return_value='SYNTHETIC_SOURCE_TOKEN_0123456789'), \
                 self.assertRaises(active.ActiveRunError):
                active.read_source_token(deadline=deadline,monotonic=Mock(side_effect=clocks))

    def test_source_echo_fallback_invalid_input_and_exception_text_are_not_exposed(self):
        def echo(prompt):
            warnings.warn('SYNTHETIC_SECRET',active.getpass.GetPassWarning)
            return 'SYNTHETIC_SOURCE_TOKEN_0123456789'
        for effect in (echo,RuntimeError('SYNTHETIC_SECRET'),lambda p:''):
            with patch.object(active.sys,'stdin',SimpleNamespace(isatty=lambda:True)), \
                 patch.object(active.getpass,'getpass',side_effect=effect), \
                 self.assertRaises(active.ActiveRunError) as caught:
                active.read_source_token(deadline=10,monotonic=lambda:1)
            self.assertEqual('FDS_SOURCE_TERMINAL_INPUT_FAILED',str(caught.exception))

    def test_source_input_cancel_is_preserved_without_cli_fallback(self):
        with patch.object(active.sys,'stdin',SimpleNamespace(isatty=lambda:True)), \
             patch.object(active.getpass,'getpass',side_effect=KeyboardInterrupt()), \
             self.assertRaises(KeyboardInterrupt):
            active.read_source_token(deadline=10,monotonic=lambda:1)

    def test_cli_auth_token_command_is_refused_before_subprocess(self):
        with self.assertRaises(active.ActiveRunError):
            active._github(['auth','token','--hostname','github.com'])
        self.blockers[3].assert_not_called()

    def test_local_cli_metadata_uses_get_and_does_not_prompt_or_print(self):
        with patch.object(active.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout=b'GWONUK')) as run:
            self.assertEqual(b'GWONUK',active._github(['api','--hostname','github.com','user']))
        args,kw=run.call_args
        self.assertEqual(['/usr/bin/gh','api','--method','GET','--hostname','github.com','user'],args[0])
        self.assertEqual(active.subprocess.DEVNULL,kw['stdin'])
        self.assertEqual(active.subprocess.PIPE,kw['stdout'])
        self.assertEqual(active.subprocess.DEVNULL,kw['stderr'])

    def test_issuer_host_uid_account_and_metadata_user_rejected_before_secret_input(self):
        changes=(('delivery_fds_run_20261025.socket.gethostname',{'return_value':'other'}),
                 ('delivery_fds_run_20261025.os.geteuid',{'return_value':0}),
                 ('pwd.getpwuid',{'return_value':SimpleNamespace(pw_name='other')}),
                 ('delivery_fds_run_20261025._github',{'return_value':b'other\n'}))
        for target,kw in changes:
            with self.subTest(target=target):
                rc,body,_=self.run_issuer(Mock(),overrides=((target,kw),),expect_source=False)
            self.assertEqual(2,rc);self.assertEqual('STOPPED',body['status'])
            self.issue.assert_not_called()

    def test_native_creation_age_and_authority_expiry_are_not_extended(self):
        for when in (active.STARTS_AT+7200,active.EXPIRES_AT):
            with patch.object(active,'_github',return_value=json.dumps(self.native_run()).encode()), \
                 patch.object(active.time,'time',return_value=when), \
                 self.assertRaises(active.ActiveRunError):
                active._actual_context(CONTEXT.run_id,CONTEXT.workflow_sha)

    def test_ca_change_and_insufficient_grant_time_reject_before_io(self):
        with patch.object(active,'GITHUB_CA_SHA256','0'*64),self.assertRaises(active.ActiveRunError):
            self.inputs()
        with patch.object(active,'EXPIRES_AT',active.STARTS_AT+1000),self.assertRaises(active.ActiveRunError):
            self.inputs()

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
