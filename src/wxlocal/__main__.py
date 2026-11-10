import os
import sys


def main():
    if sys.argv[1:2] == ['--home']:
        if len(sys.argv) < 4:
            raise SystemExit('wxlocal --home DIRECTORY COMMAND')
        os.environ['WXLOCAL_HOME'] = sys.argv[2]
        del sys.argv[1:3]
    from .api.cli import main as cli
    return cli()


if __name__ == '__main__':
    raise SystemExit(main())
