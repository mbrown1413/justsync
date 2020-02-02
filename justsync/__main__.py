import argparse
import os

from . import Synchronizer, SyncRoot

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("dirs", nargs="+")
    parser.add_argument("--watch", default=False, action="store_true",
                        help="Watch directories and re-sync on changes.")

    def assert_dir_exists(d):
        if not os.path.exists(d):
            argparse.ArgumentError(f'Directory does not exist: "{d}"')

    args = parser.parse_args()
    for d in args.dirs:
        assert_dir_exists(d)

    roots = [SyncRoot(d) for d in args.dirs]
    synchronizer = Synchronizer(*roots)
    synchronizer.sync()

    if args.watch:
        synchronizer.watch()

if __name__ == "__main__":
    main()
