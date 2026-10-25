"""SYNTHETIC native context/files only. No actual host, credentials or dispatch.

POSIX file cases run in private HOME test dirs on hosted Linux. Windows tests
only metadata, no-I/O declaration and explicit non-POSIX refusal.
"""
from dataclasses import replace
import contextlib
import io
import os
import stat
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import delivery_adapter as core
import delivery_fds_operating as operating
import delivery_fds_operating_policy as policy
import delivery_fds_prepared_environment as prepared
import delivery_fds_run_inputs as placement
import delivery_fds_site_inputs as profile
import delivery_job as job
import delivery_job_rendezvous as rendezvous
from delivery_fds_private_baseline import PrivateBaselineFile
import test_delivery_fds_private_baseline as fixture
import test_delivery_fds_baseline_archive as archive_fixture
from test_delivery_job import CONTEXT


class NativePlacementTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture.PrivateBaselineTests('runTest'); self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.files = placement.RunInputFiles('/SYNTHETIC_BASELINE_ROOT', self.f.file)
        self.inputs = self.f.d.f.args | {'selection':self.f.d.selected,
            'finish_scope':self.f.d.finish, 'baseline_grant':self.f.grant,
            'baseline_root':archive_fixture.ROOT, 'baseline_file':self.f.file}
        self.inputs.pop('context')
        self.select = Mock(return_value=self.inputs)

    def declared(self):
        return operating.declare_observed_native_job(select_inputs=self.select, input_files=self.files)

    def test_metadata_and_native_path_are_inert_and_reject_future_or_rerun_context(self):
        with patch.object(os, 'open', side_effect=AssertionError('NO_IO')):
            self.assertEqual(prepared.ROOT+'/inputs/'+CONTEXT.run_id+'-1',
                             placement.run_input_root(CONTEXT))
            self.assertIsNone(placement.files_checked(self.files))
            for bad in (None, replace(CONTEXT, run_id='FUTURE_PENDING'),
                        replace(CONTEXT, run_id='../123'), replace(CONTEXT, attempt=2)):
                with self.assertRaises((placement.RunInputError, job.JobError)):
                    placement.run_input_root(bad)

    def test_bad_metadata_is_rejected_without_calling_arbitrary_str_or_filesystem(self):
        foreign = Mock(); foreign.__str__ = Mock(side_effect=AssertionError('FOREIGN_STR'))
        for changes in ({'baseline_source_root':foreign}, {'baseline_source_root':'relative'},
                        {'baseline_source_root':'/x/../y'}, {'baseline_source_root':'/x//y'},
                        {'baseline_source_root':'/x\x00'}, {'baseline_file':{}},
                        {'baseline_file':replace(self.f.file, name='../x')},
                        {'baseline_file':replace(self.f.file, sha256='bad')}):
            with self.subTest(changes=tuple(changes)), self.assertRaises(placement.RunInputError):
                placement.files_checked(replace(self.files, **changes))
        foreign.__str__.assert_not_called()

    def test_declaration_status_and_gate_do_not_prepare_or_consume_selector(self):
        with patch.object(placement, 'prepare_run_inputs') as copy:
            value = self.declared()
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(0, job.main(['status'], declaration=value))
            self.assertFalse(job.workflow_gate(declaration=value, context=CONTEXT,
                revision=lambda:CONTEXT.workflow_sha)['execution_authorized'])
            copy.assert_not_called(); self.select.assert_not_called()

    def test_actual_context_prepares_only_after_assembly_and_before_owned_builder(self):
        order=[]; original=operating.assemble_observed_native_job
        def assemble(**args):
            order.append('assemble'); return original(**args)
        def copy(config, context, *, files):
            self.assertIs(config, self.inputs['config']); self.assertIs(context, CONTEXT)
            self.assertIs(files, self.files); order.append('copy')
        def invoke(inner, context, command):
            order.append('inner'); self.addCleanup(inner._parent._plan.close)
            self.addCleanup(inner._owner.close); return 'SYNTHETIC_RESULT'
        with patch.object(operating, 'assemble_observed_native_job', side_effect=assemble), \
             patch.object(placement, 'prepare_run_inputs', side_effect=copy), \
             patch.object(rendezvous.OwnedRendezvousBuilder, '__call__', invoke):
            value=self.declared(); self.assertEqual('SYNTHETIC_RESULT', value.build(CONTEXT,'deploy'))
            with self.assertRaises(policy.OperatingPolicyError): value.build(CONTEXT,'deploy')
        self.assertEqual(['assemble','copy','inner'],order)
        self.f.d.f.issue.assert_not_called()

    def test_file_selection_mismatch_or_bad_scope_never_prepares(self):
        for changes in ({'baseline_file':replace(self.f.file,name='other.tgz')},
                        {'finish_scope':None}):
            self.select.return_value=self.inputs|changes
            with patch.object(placement,'prepare_run_inputs') as copy, \
                 patch.object(rendezvous.OwnedRendezvousBuilder,'__call__') as invoke:
                with self.assertRaises(policy.OperatingPolicyError): self.declared().build(CONTEXT,'deploy')
                copy.assert_not_called();invoke.assert_not_called()

    def test_placement_failure_closes_owned_plan_owner_consumes_and_redacts(self):
        for failure,kind in ((OSError('PRIVATE_PATH'),policy.OperatingPolicyError),
                             (KeyboardInterrupt('PRIVATE'),KeyboardInterrupt),
                             (SystemExit(7),SystemExit)):
            captured=[]; original=operating.assemble_observed_native_job
            def assemble(**args):
                value=original(**args);captured.append(value);return value
            with patch.object(operating,'assemble_observed_native_job',side_effect=assemble), \
                 patch.object(placement,'prepare_run_inputs',side_effect=failure) as copy, \
                 patch.object(rendezvous.OwnedRendezvousBuilder,'__call__') as invoke:
                value=self.declared()
                with self.assertRaises(kind) as caught: value.build(CONTEXT,'deploy')
                self.assertIsNone(caught.exception.__context__)
                self.assertNotIn('PRIVATE',str(caught.exception))
                if kind is SystemExit:self.assertEqual(130,caught.exception.code)
                self.assertTrue(captured[0].build._parent._plan._closed)
                self.assertTrue(captured[0].build._owner._closed)
                invoke.assert_not_called()
                with self.assertRaises(policy.OperatingPolicyError):value.build(CONTEXT,'deploy')
                copy.assert_called_once()

    @unittest.skipIf(os.name=='posix','non-POSIX guard only')
    def test_non_posix_cannot_fall_back_to_unsafe_copy(self):
        with patch.object(placement.store,'root_open') as opened:
            with self.assertRaises(placement.RunInputError):
                placement.prepare_run_inputs(self.inputs['config'],CONTEXT,files=self.files)
            opened.assert_not_called()


@unittest.skipUnless(os.name=='posix','actual POSIX files/FD/durability require Linux')
class PosixRunInputTests(unittest.TestCase):
    def setUp(self):
        self.f=fixture.PrivateBaselineTests('runTest');self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.tmp=tempfile.TemporaryDirectory(prefix='p46-run-inputs-',dir=Path.home())
        self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.parent=self.root/'inputs';self.parent.mkdir(mode=0o700)
        self.template=self.parent/'prepared-SYNTHETIC';self.template.mkdir(mode=0o700)
        self.baseline=self.root/'baseline-source';self.baseline.mkdir(mode=0o700)
        self.data=(b'SYNTHETIC_RENDERED',b'SYNTHETIC_CONTRACT',b'SYNTHETIC_OPAQUE_BASELINE')
        for p,raw in ((self.template/'rendered.yaml',self.data[0]),
                      (self.template/'contract.yaml',self.data[1]),
                      (self.baseline/'baseline.tgz',self.data[2])):
            p.write_bytes(raw);p.chmod(0o600)
        for obj,attr,value in ((prepared,'ROOT',str(self.root)),
                (prepared,'INPUT_TEMPLATE_ROOT',str(self.template)),
                (profile,'RENDERED_SHA256',core.digest(self.data[0])),
                (profile,'CONTRACT_SHA256',core.digest(self.data[1]))):
            p=patch.object(obj,attr,value);p.start();self.addCleanup(p.stop)
        self.files=placement.RunInputFiles(str(self.baseline),
            PrivateBaselineFile('baseline.tgz',core.digest(self.data[2])))
        self.output=Path(placement.run_input_root(CONTEXT))
        b=replace(self.f.d.f.config.binding,rendered_sha256=core.digest(self.data[0]),
                  contract_sha256=core.digest(self.data[1]))
        self.config=replace(self.f.d.f.config,binding=b,input_root=self.output,
            rendered_name='rendered.yaml',contract_name='contract.yaml',store_root=self.root/'state')

    def prepare(self,**changes):
        return placement.prepare_run_inputs(changes.pop('config',self.config),CONTEXT,
                                           files=changes.pop('files',self.files),**changes)

    def test_three_exact_files_are_private_durable_and_no_claim_or_archive_extraction(self):
        self.assertIsNone(self.prepare())
        self.assertEqual(0o700,self.output.stat().st_mode&0o777)
        self.assertEqual({'rendered.yaml','contract.yaml','baseline.tgz'},set(os.listdir(self.output)))
        for name,raw in zip(('rendered.yaml','contract.yaml','baseline.tgz'),self.data):
            p=self.output/name;self.assertEqual(raw,p.read_bytes());self.assertEqual(0o600,p.stat().st_mode&0o777)
        self.assertFalse(self.config.store_root.exists())
        self.assertEqual(self.data[2],(self.baseline/'baseline.tgz').read_bytes())

    def test_existing_partial_directory_and_symlink_are_never_reused(self):
        self.output.mkdir(mode=0o700);(self.output/'partial').write_bytes(b'SYNTHETIC_KEEP')
        with self.assertRaises(placement.RunInputError):self.prepare()
        self.assertEqual(b'SYNTHETIC_KEEP',(self.output/'partial').read_bytes())
        (self.output/'partial').unlink();self.output.rmdir();self.output.symlink_to(self.baseline,target_is_directory=True)
        with self.assertRaises(placement.RunInputError):self.prepare()
        self.assertTrue(self.output.is_symlink())

    def test_source_hash_mode_hardlink_symlink_fifo_and_directory_mode_fail_before_mkdir(self):
        p=self.baseline/'baseline.tgz'
        for mode in ('hash','filemode','hardlink','symlink','fifo','directorymode'):
            if p.exists() or p.is_symlink():p.unlink()
            other=self.baseline/'other'
            if other.exists():other.unlink()
            p.write_bytes(self.data[2]);p.chmod(0o600)
            if mode=='hash':p.write_bytes(b'SYNTHETIC_CHANGED')
            elif mode=='filemode':p.chmod(0o644)
            elif mode=='hardlink':other.hardlink_to(p)
            elif mode=='symlink':p.rename(other);p.symlink_to(other)
            elif mode=='fifo':p.unlink();os.mkfifo(p,0o600)
            elif mode=='directorymode':self.baseline.chmod(0o755)
            with self.subTest(mode=mode),self.assertRaises(placement.RunInputError):self.prepare()
            self.assertFalse(self.output.exists())
            self.baseline.chmod(0o700)

    def test_wrong_native_root_or_duplicate_names_fails_before_file_io(self):
        for config,files in ((replace(self.config,input_root=self.parent/'other'),self.files),
                (self.config,replace(self.files,baseline_file=replace(self.files.baseline_file,name='rendered.yaml')))):
            with patch.object(placement.store,'root_open') as opened,self.assertRaises(placement.RunInputError):
                self.prepare(config=config,files=files)
            opened.assert_not_called()

    def test_baseline_source_equal_to_or_below_target_fails_before_source_io(self):
        for source in (self.output, self.output/'staging'):
            with self.subTest(source=source), \
                 patch.object(placement.store,'root_open') as opened, \
                 self.assertRaises(placement.RunInputError):
                self.prepare(files=replace(self.files,baseline_source_root=str(source)))
            opened.assert_not_called()

    def test_partial_write_failure_preserves_partial_files_but_closes_descriptors(self):
        real=placement.store.create_file;opened=[];root_open=placement.store.root_open;n=0
        def observe(*args):
            value=root_open(*args);opened.append(value[1]);return value
        def fail(parent,name,raw):
            nonlocal n
            n+=1
            if n==2:raise OSError('SYNTHETIC_PRIVATE_ENOSPC')
            return real(parent,name,raw)
        with patch.object(placement.store,'root_open',side_effect=observe), \
             patch.object(placement.store,'create_file',side_effect=fail),self.assertRaises(placement.RunInputError) as caught:
            self.prepare()
        self.assertIsNone(caught.exception.__context__)
        self.assertNotIn('SYNTHETIC_PRIVATE',str(caught.exception))
        self.assertEqual({'rendered.yaml'},set(os.listdir(self.output)))
        for fd in opened:
            with self.assertRaises(OSError):os.fstat(fd)
        with self.assertRaises(placement.RunInputError):self.prepare()

    def test_short_writes_use_existing_loop_and_fsync_failure_does_not_return_success(self):
        real=placement.store.os.write
        with patch.object(placement.store.os,'write',side_effect=lambda fd,raw:real(fd,raw[:2])):
            self.prepare()
        self.assertEqual(self.data[2],(self.output/'baseline.tgz').read_bytes())
        # A separate synthetic native RUN would select another directory.
        other=replace(CONTEXT,run_id='987654321')
        config=replace(self.config,input_root=placement.run_input_root(other))
        with patch.object(placement.os,'fsync',side_effect=OSError('PRIVATE_FSYNC')), \
             self.assertRaises(placement.RunInputError):
            placement.prepare_run_inputs(config,other,files=self.files)

    def test_root_open_interruption_closes_its_new_descriptor_before_it_can_be_returned(self):
        real_open=placement.store.os.open;opened=[]
        def observe(*args,**kwargs):
            fd=real_open(*args,**kwargs);opened.append(fd);return fd
        with patch.object(placement.store.os,'open',side_effect=observe), \
             patch.object(placement.store.os,'fsync',side_effect=KeyboardInterrupt('PRIVATE')) as sync, \
             self.assertRaises(KeyboardInterrupt) as caught:self.prepare()
        self.assertIsNone(caught.exception.__context__)
        sync.assert_called_once();self.assertEqual(1,len(opened))
        for fd in opened:
            with self.assertRaises(OSError):os.fstat(fd)

    def test_late_directory_fsync_failure_leaves_complete_files_but_never_success_or_reuse(self):
        real_sync=placement.os.fsync;failed=False
        def sync(fd):
            nonlocal failed
            if (not failed and stat.S_ISDIR(os.fstat(fd).st_mode) and self.output.exists()
                    and os.fstat(fd).st_ino==self.output.stat().st_ino
                    and len(os.listdir(self.output))==3):
                failed=True;raise OSError('SYNTHETIC_LATE_FSYNC')
            return real_sync(fd)
        with patch.object(placement.os,'fsync',side_effect=sync),self.assertRaises(placement.RunInputError):
            self.prepare()
        self.assertTrue(failed)
        self.assertEqual(3,len(os.listdir(self.output)))
        with self.assertRaises(placement.RunInputError):self.prepare()

    def test_replaced_named_target_does_not_redirect_writes_and_fails_before_ready(self):
        real=placement.store.create_file;moved=self.parent/'preserved-SYNTHETIC';n=0
        def write(parent,name,raw):
            nonlocal n
            result=real(parent,name,raw);n+=1
            if n==1:
                self.output.rename(moved);self.output.mkdir(mode=0o700)
            return result
        with patch.object(placement.store,'create_file',side_effect=write), \
             self.assertRaises(placement.RunInputError):self.prepare()
        self.assertEqual([],os.listdir(self.output))
        self.assertEqual({'rendered.yaml','contract.yaml','baseline.tgz'},set(os.listdir(moved)))

    def test_interrupt_after_first_file_closes_all_roots_and_preserves_partial_directory(self):
        real=placement.store.create_file;n=0
        def interrupt(parent,name,raw):
            nonlocal n
            n+=1
            if n==2:raise SystemExit(9)
            return real(parent,name,raw)
        with patch.object(placement.store,'create_file',side_effect=interrupt), \
             self.assertRaises(SystemExit) as caught:self.prepare()
        self.assertEqual(130,caught.exception.code);self.assertIsNone(caught.exception.__context__)
        self.assertEqual({'rendered.yaml'},set(os.listdir(self.output)))


if __name__=='__main__':unittest.main()
