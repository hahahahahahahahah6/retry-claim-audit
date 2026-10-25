"""Command line interface."""
import argparse
import json
import sys
from . import __version__
from .audit import audit


class Parser(argparse.ArgumentParser):
    def error(self, message):
        self.print_usage(sys.stderr)
        self.exit(1, '{}: error: {}\n'.format(self.prog, message))


def main(argv=None):
    parser = Parser(prog='retry-claim-audit', description='Offline audit of documented retry claims against Python source.')
    parser.add_argument('path', nargs='?', default='.')
    parser.add_argument('--json', action='store_true', dest='json_output')
    parser.add_argument('--verbose', action='store_true')
    parser.add_argument('--ignore', action='append', default=[], metavar='GLOB')
    parser.add_argument('--fail-on', choices=['MEDIUM', 'HIGH'], default='MEDIUM')
    parser.add_argument('--version', action='version', version=__version__)
    args = parser.parse_args(argv)
    try:
        report = audit(args.path, args.ignore)
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    if args.json_output:
        print(json.dumps(report, indent=2))
    else:
        for finding in report['findings']:
            print('{} {}'.format(finding['severity'], finding['rule']))
            for claim in finding['claims']:
                print('  {}:{}: {}'.format(claim['path'], claim['line'], claim['text']))
            for evidence in finding['evidence']:
                print('  ' + json.dumps(evidence, sort_keys=True))
        if args.verbose:
            for claim in report['verified_claims']:
                print('VERIFIED ' + claim['family'])
            for error in report['errors']:
                print('WARNING ' + json.dumps(error, sort_keys=True))
        if not report['findings']:
            print('PASS')
    return 2 if any(f['severity'] == 'HIGH' or args.fail_on == 'MEDIUM' for f in report['findings']) else 0


if __name__ == '__main__':
    sys.exit(main())
