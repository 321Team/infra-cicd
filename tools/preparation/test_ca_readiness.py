import unittest

import ca_readiness as ca


def complete_record():
    # Synthetic declarations only; this is not a real CA asset record.
    return {'schema': 'P1-CICD-01-ca-preparation-1.1', 'template_only': False, 'owner': '이권욱',
            'csr_sha256': ca.CSR_SHA256, 'csr_spki_sha256': ca.SPKI_SHA256,
            'legacy_ca_status': 'RETIREMENT_REQUIRED', 'inventory_record_reference': 'fixture:inventory',
            'checked_at': '2026-09-10T00:00:00Z', 'legacy_ca_fingerprint_sha256': 'a' * 64,
            'replacement_decision_reference': 'fixture:replacement-decision',
            'retirement_plan_reference': 'fixture:retirement-plan',
            'selection': 'REPLACE_WITH_NEW_ROOT', 'custody': {
                'host': 'fixture-offline-admin', 'role': 'OFFLINE_ADMIN_ENVIRONMENT',
                'asset_reference': 'fixture:asset', 'encryption_reference': 'fixture:encryption',
                'backup_reference': 'fixture:backup', 'access_control_reference': 'fixture:access',
                'offline_procedure_reference': 'fixture:offline',
                'encrypted_storage_confirmed': True, 'separate_encrypted_backup_confirmed': True,
                'administrator_access_confirmed': True, 'offline_signing_confirmed': True}}


class CAReadinessTests(unittest.TestCase):
    def test_complete_declarations_never_mean_issuance_or_asset_verified(self):
        result = ca.evaluate(complete_record())
        self.assertEqual(result['preparation_inputs'], 'COMPLETE_FOR_REVIEW')
        for key in ('ca_asset_independently_verified', 'key_accessed', 'ca_initialized', 'certificate_issued'):
            self.assertIs(result[key], False)
        self.assertEqual(result['delivery_gate'], 'NOT_VERIFIED')
        self.assertEqual(result['legacy_ca_retirement'], 'NOT_VERIFIED')

    def test_old_schema_and_mixed_old_fields_are_rejected(self):
        record = complete_record()
        record['schema'] = 'P1-CICD-01-ca-preparation-1.0'
        self.assertIn('SCHEMA', ca.evaluate(record)['reasons'])
        record = complete_record(); record['approved_ca_status'] = 'PRESENT'
        self.assertIn('LEGACY_SCHEMA_FIELDS', ca.evaluate(record)['reasons'])

    def test_reuse_or_absence_rule_cannot_override_replacement(self):
        for selection in ('REUSE_APPROVED_CA', 'INITIALIZE_AFTER_CUSTODY_REVIEW', None):
            with self.subTest(selection=selection):
                record = complete_record(); record['selection'] = selection
                self.assertIn('NEW_ROOT_REQUIRED', ca.evaluate(record)['reasons'])

    def test_replacement_and_retirement_records_required(self):
        for field in ('replacement_decision_reference', 'retirement_plan_reference', 'legacy_ca_status'):
            record = complete_record(); record[field] = None
            self.assertEqual(ca.evaluate(record)['preparation_inputs'], 'INCOMPLETE')

    def test_unknown_old_fingerprint_does_not_require_false_absence_or_completed_retirement(self):
        record = complete_record(); record['legacy_ca_fingerprint_sha256'] = None
        result = ca.evaluate(record)
        self.assertEqual(result['preparation_inputs'], 'COMPLETE_FOR_REVIEW')
        self.assertEqual(result['follow_up'], ['LEGACY_CA_IDENTIFICATION_PENDING'])
        self.assertEqual(result['legacy_ca_retirement'], 'NOT_VERIFIED')

    def test_bad_old_fingerprint_and_claimed_retirement_are_not_accepted(self):
        for fingerprint in (True, 'unknown', 'z' * 64):
            record = complete_record(); record['legacy_ca_fingerprint_sha256'] = fingerprint
            self.assertIn('LEGACY_CA_FINGERPRINT_FORMAT', ca.evaluate(record)['reasons'])
        record = complete_record(); record['legacy_ca_status'] = 'RETIRED'
        self.assertIn('LEGACY_RETIREMENT_INTENT', ca.evaluate(record)['reasons'])

    def test_encryption_backup_and_offline_each_required(self):
        for field in ('encrypted_storage_confirmed', 'separate_encrypted_backup_confirmed',
                      'administrator_access_confirmed', 'offline_signing_confirmed'):
            for bad in (None, False, 'true', 1):
                with self.subTest(field=field, bad=bad):
                    record = complete_record(); record['custody'][field] = bad
                    self.assertEqual(ca.evaluate(record)['preparation_inputs'], 'INCOMPLETE')

    def test_known_service_hosts_and_fqdns_rejected(self):
        for host in ('harbor01', 'CICD-RUNNER01.fds.internal', 'ansible01', 'k8s-control01'):
            with self.subTest(host=host):
                record = complete_record(); record['custody']['host'] = host
                self.assertIn('CUSTODY_HOST', ca.evaluate(record)['reasons'])

    def test_existing_csr_cannot_be_silently_replaced(self):
        record = complete_record(); record['csr_sha256'] = 'b' * 64
        self.assertIn('EXISTING_CSR_REFERENCE', ca.evaluate(record)['reasons'])

    def test_template_and_malformed_custody_block_without_echo(self):
        record = complete_record(); record.update(template_only=True, custody='SENTINEL_SECRET')
        result = ca.evaluate(record)
        self.assertEqual(result['preparation_inputs'], 'INCOMPLETE')
        self.assertNotIn('SENTINEL', str(result))


if __name__ == '__main__':
    unittest.main()
