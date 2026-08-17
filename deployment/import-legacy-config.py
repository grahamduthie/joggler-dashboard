#!/usr/bin/env python3
"""One-time migration of legacy embedded settings into a protected .env file.

It intentionally prints only setting names, never values.  Delete the legacy source
copy after a successful import.
"""
import argparse
import os
import re
from pathlib import Path

NAMES = ('NR_TOKEN', 'BUS_APP_ID', 'BUS_APP_KEY')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--legacy-source', type=Path, required=True)
    parser.add_argument('--env-file', type=Path, required=True)
    args = parser.parse_args()

    source = args.legacy_source.read_text()
    values = {}
    for name in NAMES:
        match = re.search(rf"^{name}\s*=\s*'([^']+)'", source, re.MULTILINE)
        if not match:
            raise SystemExit(f'Missing {name} in legacy source')
        values[name] = match.group(1)

    existing = {}
    if args.env_file.exists():
        for line in args.env_file.read_text().splitlines():
            if '=' in line and not line.lstrip().startswith('#'):
                key, value = line.split('=', 1)
                existing[key.strip()] = value.strip()
    existing.update(values)
    temp = args.env_file.with_suffix('.env.tmp')
    temp.write_text(''.join(f'{key}={value}\n' for key, value in sorted(existing.items())))
    os.chmod(temp, 0o600)
    temp.replace(args.env_file)
    print('Imported: ' + ', '.join(NAMES))


if __name__ == '__main__':
    main()
