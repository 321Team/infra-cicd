"""Offline diagnostics only; unchanged fail-closed authorization and writes."""
import json,unittest
from test_delivery_operational import ContractHarness,SENSITIVE
from delivery_verifier import public_provider_diagnostic

class FailureDiagnosticsTests(unittest.TestCase):
    def test_provider_failure_identifies_role_and_fixed_code_before_claim(self):
        for role,code in [('platform','PLATFORM_READBACK_ACCESS_INVALID'),('approval','SOURCE_COMMENT_UNVERIFIED'),('producer','ACTIONS_RUN_NOT_SUCCESSFUL'),('objects','OBJECT_DRIFT')]:
            with self.subTest(role=role):
                f=ContractHarness()
                def deny(*args):raise RuntimeError(code)
                f.providers.hooks[role]=deny
                result=f.run()
                self.assertEqual('PREFLIGHT_DENIED',result['error_code'])
                self.assertEqual({'role':role,'code':code},result['verification_detail'])
                self.assertEqual([],f.storage.claims)
                self.assertEqual(0,f.sender_calls)
    def test_secret_or_hostile_exception_text_is_never_serialized(self):
        class Hostile(Exception):
            def __str__(self):raise AssertionError('MUST_NOT_STRINGIFY')
        for error in (RuntimeError(SENSITIVE),Hostile(SENSITIVE),RuntimeError({'secret':SENSITIVE}),RuntimeError('HTTP_FORBIDDEN',SENSITIVE)):
            self.assertEqual('PROVIDER_UNAVAILABLE',public_provider_diagnostic(error))
            f=ContractHarness()
            def deny(*args):raise error
            f.providers.hooks['approval']=deny
            result=f.run()
            self.assertNotIn(SENSITIVE,json.dumps(result))
            self.assertEqual(0,f.sender_calls)
    def test_normal_session_path_is_unchanged(self):
        result=ContractHarness().run()
        self.assertEqual('SESSION_COMPLETED',result['status'])
        self.assertNotIn('verification_detail',result)
