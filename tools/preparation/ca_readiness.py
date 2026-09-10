#!/usr/bin/env python3
"""Check declared CA preparation inputs. Never inspect keys, sign or initialize a CA."""
import argparse
import json
import re

from input_json import read_record

CSR_SHA256 = '97473d44f37140dfe04c83a29f6fec17560bb6f7b29a543fd1fa06cce91ca890'
SPKI_SHA256 = '636f85ce3ae0d5e3ea49e50fca2b8ec1e5c132f990d7867e2ad59d0a2e333638'
SERVICE_HOSTS = {'cicd-runner01', 'harbor01', 'edge01', 'ansible01', 'mgmt-gw01',
                 'k8s-api-lb01', 'k8s-control01', 'k8s-control02', 'k8s-control03',
                 'k8s-worker01', 'k8s-worker02', 'k8s-worker03', 'db01', 'dns01', 'dns02'}


def reference(value):
    return isinstance(value, str) and bool(value.strip()) and len(value) <= 500


def evaluate(record):
    reasons = []
    def need(ok, code):
        if not ok:
            reasons.append(code)
    if not isinstance(record, dict):
        record = {}
    need(record.get('schema') == 'P1-CICD-01-ca-preparation-1.1', 'SCHEMA')
    need(record.get('template_only') is False, 'TEMPLATE_OR_UNCONFIRMED_INPUT')
    need(record.get('owner') == '이권욱', 'CA_OWNER')
    need(record.get('csr_sha256') == CSR_SHA256, 'EXISTING_CSR_REFERENCE')
    need(record.get('csr_spki_sha256') == SPKI_SHA256, 'EXISTING_KEY_REFERENCE')
    need(reference(record.get('inventory_record_reference')), 'CA_INVENTORY_RECORD')
    need(reference(record.get('checked_at')), 'INVENTORY_CHECK_TIME')
    need(record.get('selection') == 'REPLACE_WITH_NEW_ROOT', 'NEW_ROOT_REQUIRED')
    need(reference(record.get('replacement_decision_reference')), 'REPLACEMENT_DECISION')
    need(reference(record.get('retirement_plan_reference')), 'RETIREMENT_PLAN')
    need(record.get('legacy_ca_status') == 'RETIREMENT_REQUIRED', 'LEGACY_RETIREMENT_INTENT')
    need(not any(field in record for field in ('approved_ca_status',
             'approved_ca_fingerprint_sha256', 'approved_ca_record_reference',
             'ca_validity_review_reference')), 'LEGACY_SCHEMA_FIELDS')
    legacy_fingerprint = record.get('legacy_ca_fingerprint_sha256')
    need(legacy_fingerprint is None or
         (isinstance(legacy_fingerprint, str) and re.fullmatch(r'[0-9a-f]{64}', legacy_fingerprint)),
         'LEGACY_CA_FINGERPRINT_FORMAT')
    env = record.get('custody')
    env = env if isinstance(env, dict) else {}
    host = env.get('host')
    need(isinstance(host, str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.-]{0,252}', host)
         and host.lower().split('.')[0] not in SERVICE_HOSTS, 'CUSTODY_HOST')
    need(env.get('role') == 'OFFLINE_ADMIN_ENVIRONMENT', 'CUSTODY_ROLE')
    for field in ('asset_reference', 'encryption_reference', 'backup_reference',
                  'access_control_reference', 'offline_procedure_reference'):
        need(reference(env.get(field)), 'CUSTODY_' + field.upper())
    for field in ('encrypted_storage_confirmed', 'separate_encrypted_backup_confirmed',
                  'administrator_access_confirmed', 'offline_signing_confirmed'):
        need(env.get(field) is True, 'CUSTODY_' + field.upper())
    return {'preparation_inputs': 'COMPLETE_FOR_REVIEW' if not reasons else 'INCOMPLETE',
            'reasons': reasons, 'basis': 'DECLARED_INPUTS_ONLY',
            'follow_up': ['LEGACY_CA_IDENTIFICATION_PENDING'] if legacy_fingerprint is None else [],
            'ca_asset_independently_verified': False, 'key_accessed': False,
            'ca_initialized': False, 'certificate_issued': False,
            'legacy_ca_retirement': 'NOT_VERIFIED',
            'delivery_gate': 'NOT_VERIFIED'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--record', required=True)
    args = parser.parse_args(argv)
    try:
        result = evaluate(read_record(args.record))
    except (OSError, ValueError, TypeError, RecursionError):
        print(json.dumps({'preparation_inputs': 'INPUT_ERROR', 'delivery_gate': 'NOT_VERIFIED'}))
        return 3
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['preparation_inputs'] == 'COMPLETE_FOR_REVIEW' else 2


if __name__ == '__main__':
    raise SystemExit(main())
