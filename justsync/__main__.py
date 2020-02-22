import argparse
import logging
import os
import sys

from . import Synchronizer, SyncRoot

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("dirs", nargs="+")
    parser.add_argument("--watch", default=False, action="store_true",
                        help="Watch directories and re-sync on changes.")
    parser.add_argument("--verbose", default=False, action="store_true",
                        help="Print debugging information.")

    def assert_dir_exists(d):
        if not os.path.exists(d):
            argparse.ArgumentError(f'Directory does not exist: "{d}"')

    args = parser.parse_args()
    for d in args.dirs:
        assert_dir_exists(d)

    logger = logging.getLogger("justsync")
    logger.setLevel(logging.DEBUG)
    logger.addHandler(logging.StreamHandler(sys.stdout))

    roots = [SyncRoot(d) for d in args.dirs]
    synchronizer = Synchronizer(*roots)

    if args.watch:
        synchronizer.watch()
    else:
        synchronizer.sync()

if __name__ == "__main__":
    main()
