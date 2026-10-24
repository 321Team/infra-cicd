"""SYNTHETIC runtime process facts only; no server/app/credential execution."""
import os
import shlex
import sys
from pathlib import Path
from unittest.mock import Mock, patch
import unittest
import yaml

import delivery_fds_prepared_environment as prepared
import delivery_fds_operating as operating
import delivery_fds_site_inputs as profile
import delivery_job as job
import delivery_job_site as declaration

ROOT = Path(__file__).resolve().parents[2]


class PreparedEnvironmentTests(unittest.TestCase):
    def facts(self):
        return (patch.object(sys, 'prefix', prepared.RUNTIME_ROOT),
                patch.object(sys, 'version_info', (3,12,15)),
                patch.object(yaml, '__file__', prepared.RUNTIME_ROOT+'/lib/python3.12/site-packages/yaml/__init__.py'))

    def test_observed_paths_reuse_profile_but_do_not_create_native_inputs_or_authorize_dispatch(self):
        with patch.object(Path,'open',side_effect=AssertionError('NO_IO')):
            paths = prepared.configuration_paths(); status = prepared.selection_status()
            report = operating.configuration_status()
        self.assertEqual(profile.SOURCE_COMMIT, prepared.SOURCE_ROOT.rsplit('/',1)[1])
        self.assertEqual({'source_root','store_root','rendered_name','contract_name'},set(paths))
        self.assertNotIn('input_root',paths)
        self.assertFalse(status['input_template_is_run']); self.assertIsNone(status['actual_native_run_id'])
        self.assertFalse(status['execution_authorized']); self.assertFalse(status['live_checked'])
        self.assertEqual('UNCONFIGURED',report['runtime_site']); self.assertIsNone(declaration.SITE)
        paths['source_root']='modified';status['execution_authorized']=True
        self.assertEqual(prepared.SOURCE_ROOT,prepared.configuration_paths()['source_root'])
        self.assertFalse(prepared.selection_status()['execution_authorized'])

    def test_system_interpreter_or_wrong_python_abi_stops_before_loading_parser(self):
        for prefix,version in (('/usr',(3,12,15)),(prepared.RUNTIME_ROOT,(3,11,9)),
                               (prepared.RUNTIME_ROOT,(3,14,7))):
            with patch.object(sys,'prefix',prefix),patch.object(sys,'version_info',version), \
                 patch.object(prepared,'yaml_module',side_effect=AssertionError('NO_PARSER')) as parser:
                with self.assertRaises(prepared.PreparedEnvironmentError): prepared.check_interpreter()
            parser.assert_not_called()

    def test_private_runtime_uses_the_existing_exact_parser_version_validator(self):
        prefix,version,module=self.facts()
        with prefix,version,module,patch.object(yaml,'__version__','6.0.3'):
            self.assertIsNone(prepared.check_interpreter())

    def test_old_parser_or_outside_module_path_never_falls_back(self):
        for module_path,yaml_version in (('/usr/lib/python3.12/site-packages/yaml/__init__.py','6.0.3'),
             (prepared.RUNTIME_ROOT+'/lib/../outside/yaml.py','6.0.3'),
             ('yaml/__init__.py','6.0.3'),(None,'6.0.3'),
             (prepared.RUNTIME_ROOT+'/lib/python3.12/site-packages/yaml/__init__.py','6.0.1')):
            with patch.object(sys,'prefix',prepared.RUNTIME_ROOT),patch.object(sys,'version_info',(3,12,15)), \
                 patch.object(yaml,'__version__',yaml_version),patch.object(yaml,'__file__',module_path):
                with self.assertRaises(prepared.PreparedEnvironmentError) as caught: prepared.check_interpreter()
            self.assertEqual('P2_PRIVATE_RUNTIME_REJECTED',caught.exception.args[0])
            self.assertIsNone(caught.exception.__context__)

    def script(self):
        w=yaml.load((ROOT/'.github/workflows/p2-rosa-delivery.yml').read_text(encoding='utf-8'),Loader=yaml.BaseLoader)
        argv=shlex.split(w['jobs']['delivery']['steps'][2]['run'].splitlines()[-1])
        self.assertEqual([prepared.RUNTIME_PYTHON,'-I','-B','-c'],argv[:4])
        return argv[4]

    def test_real_workflow_snippet_blocks_job_before_any_driver_action_on_environment_failure(self):
        script=self.script();old=list(sys.path)
        try:
            with patch.object(prepared,'check_interpreter',side_effect=prepared.PreparedEnvironmentError('P2_PRIVATE_RUNTIME_REJECTED')), \
                 patch.object(job,'main',side_effect=AssertionError('NO_JOB')) as main:
                with self.assertRaises(prepared.PreparedEnvironmentError):exec(compile(script,'workflow','exec'),{})
            main.assert_not_called()
        finally:sys.path[:]=old

    def test_real_workflow_snippet_preserves_explicit_request_after_environment_check(self):
        script=self.script();old=list(sys.path);order=[]
        try:
            with patch.object(prepared,'check_interpreter',side_effect=lambda:order.append('environment')), \
                 patch.object(job,'main',side_effect=lambda args:order.append(tuple(args)) or 0), \
                 patch.dict(os.environ,{'P2_JOB_COMMAND':'deploy'}):
                with self.assertRaises(SystemExit) as caught:exec(compile(script,'workflow','exec'),{})
            self.assertEqual(0,caught.exception.code)
            self.assertEqual(['environment',('deploy','--execute')],order)
            self.assertIsNone(declaration.SITE)
        finally:sys.path[:]=old


if __name__=='__main__':unittest.main()
