"""Code-configured CLI dispatcher; standalone operation is still unconfigured.

No input-file, token, environment, plugin, authority-flag or store-root loader.
Only reviewed Python site code can pass an OperationalApplication in memory.
"""
import argparse
import json


class SafeParser(argparse.ArgumentParser):
    def error(self, message):
        # argparse normally echoes unknown arguments, possibly including secrets.
        self.exit(2, 'CLI_ARGUMENTS_REJECTED\n')


def main(argv=None, *, application=None):
    parser = SafeParser(description='P2 delivery; requires reviewed code configuration.')
    parser.add_argument('command', choices=('status', 'preflight', 'deploy', 'restore'))
    args = parser.parse_args(argv)
    if application is not None:
        from delivery_provider import OperationalApplication
        if type(application) is not OperationalApplication:
            print(json.dumps({'status': 'BLOCKED', 'error_code': 'CLI_CONFIGURATION_REJECTED',
                              'execution_authorized': False}, sort_keys=True))
            return 2
        result = application.status() if args.command == 'status' else application.run(args.command)
        print(json.dumps(result, sort_keys=True))
        return 0 if result['status'] in ('PREFLIGHT_OBSERVED', 'SESSION_COMPLETED') else 2
    # Deliberately no input JSON, credential/env/file/plugin or store-root option.
    # Even a syntactically valid deploy/restore request performs no preparation.
    print(json.dumps({'schema': 'P2-CICD-operational-boundary-1',
                      'status': 'BLOCKED', 'error_code': 'OPERATIONAL_PROVIDER_UNCONFIGURED',
                      'execution_authorized': False, 'runtime': 'NOT_RUN',
                      'sender_calls': 0, 'claim_acquired': False}, sort_keys=True))
    return 2


if __name__ == '__main__':
    raise SystemExit(main())
