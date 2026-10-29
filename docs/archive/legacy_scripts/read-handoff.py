#!/usr/bin/env python3
"""Read a bootstrap report received from another machine, without extraction."""
import argparse
import json
from pathlib import Path
import sys
import zipfile

from handoff import read_report, summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive', type=Path)
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args()
    try:
        report = read_report(args.archive)
        print(json.dumps(report, ensure_ascii=False, indent=2) if args.json else summary(report))
        # A valid package can report a failed installation; do not call that success.
        return 0 if report['status'] == 'passed' else 1
    except (OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile) as error:
        print('Reporte rechazado: ' + str(error), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
