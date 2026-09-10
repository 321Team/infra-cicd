import copy
import unittest

import ca_readiness as ca


def complete_record():
    # Synthetic declarations only; this is not a real CA asset record.
    return {'schema': 'P1-CICD-01-ca-preparation-1.0', 'template_only': False, 'owner': '이권욱',
            'csr_sha256': ca.CSR_SHA256, 'csr_spki_sha256': ca.SPKI_SHA256,
            'approved_ca_status': 'PRESENT', 'inventory_record_reference': 'fixture:inventory',
            'checked_at': '2026-09-10T00:00:00Z', 'approved_ca_fingerprint_sha256': 'a' * 64,
            'approved_ca_record_reference': 'fixture:ca', 'ca_validity_review_reference': 'fixture:validity',
            'selection': 'REUSE_APPROVED_CA', 'custody': {
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

    def test_unknown_inventory_cannot_select_new_ca(self):
        record = complete_record()
        record.update(approved_ca_status='UNKNOWN', selection='INITIALIZE_AFTER_CUSTODY_REVIEW')
        self.assertEqual(ca.evaluate(record)['preparation_inputs'], 'INCOMPLETE')

    def test_existing_ca_cannot_be_replaced_by_initialization(self):
        record = complete_record()
        record['selection'] = 'INITIALIZE_AFTER_CUSTODY_REVIEW'
        self.assertIn('REUSE_EXISTING_CA_REQUIRED', ca.evaluate(record)['reasons'])

    def test_confirmed_absence_requires_consistent_asset_fields(self):
        record = complete_record()
        record.update(approved_ca_status='ABSENT_CONFIRMED', selection='INITIALIZE_AFTER_CUSTODY_REVIEW')
        self.assertEqual(ca.evaluate(record)['preparation_inputs'], 'INCOMPLETE')
        record.update(approved_ca_fingerprint_sha256=None, approved_ca_record_reference=None)
        self.assertEqual(ca.evaluate(record)['preparation_inputs'], 'COMPLETE_FOR_REVIEW')

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
