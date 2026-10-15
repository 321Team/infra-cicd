"""Fail-closed operational CLI boundary. No production execution path yet."""
import argparse
import json


class SafeParser(argparse.ArgumentParser):
    def error(self, message):
        # argparse normally echoes unknown arguments, possibly including secrets.
        self.exit(2, 'CLI_ARGUMENTS_REJECTED\n')


def main(argv=None):
    parser = SafeParser(description='P2 operational adapters are unconfigured; no execution.')
    parser.add_argument('command', choices=('status', 'deploy', 'restore'))
    parser.parse_args(argv)
    # Deliberately no input JSON, credential/env/file/plugin or store-root option.
    # Even a syntactically valid deploy/restore request performs no preparation.
    print(json.dumps({'schema': 'P2-CICD-operational-boundary-1',
                      'status': 'BLOCKED', 'error_code': 'OPERATIONAL_PROVIDER_UNCONFIGURED',
                      'execution_authorized': False, 'runtime': 'NOT_RUN',
                      'sender_calls': 0, 'claim_acquired': False}, sort_keys=True))
    return 2


if __name__ == '__main__':
    raise SystemExit(main())
