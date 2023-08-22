import os
import sys
import unittest
import tempfile
import shutil
import stat
import time

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))
from justsync import SyncRoot, Synchronizer

DEBUG = False
if DEBUG:
    import logging
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)
    root_logger.addHandler(logging.StreamHandler(sys.stdout))

class TestSync(unittest.TestCase):
    _reverse_sync_order = False

    def setUp(self):
        self.temp_dir_base = None
        self._temp_dirs = []

    def tearDown(self):
        if self.temp_dir_base:
            shutil.rmtree(self.temp_dir_base)

    ########## Test Tools ##########

    def make_temp_dir(self):
        if not self.temp_dir_base:
            self.temp_dir_base = tempfile.mkdtemp()

        i = 0
        path = os.path.join(self.temp_dir_base, str(i))
        while os.path.exists(path):
            i += 1
            path = os.path.join(self.temp_dir_base, str(i))

        os.makedirs(path)
        self._temp_dirs.append(path)
        return path

    def make_temp_dirs(self, count):
        """Return a list of `count` temporary directories."""
        return [self.make_temp_dir() for i in range(count)]

    def write_file(self, root_path, path, content=""):
        full_path = os.path.join(root_path, path)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, "w") as f:
            f.write(content)
        self.assertFilePresent(root_path, path)

    def delete_file(self, root_path, path):
        full_path = os.path.join(root_path, path)
        os.remove(full_path)
        self.assertFileAbsent(root_path, path)

    def write_dir(self, root_path, path):
        full_path = os.path.join(root_path, path)
        os.makedirs(full_path)

    def delete_dir(self, root_path, path):
        full_path = os.path.join(root_path, path)
        os.rmdir(full_path)

    def sync_dirs(self, *dirs, force_hash=False):
        """
        Create a SyncRoot for each of the given dirs and synchronize
        them.
        """
        roots = [SyncRoot(d) for d in dirs]
        if self._reverse_sync_order:
            roots = reversed(roots)
        synchronizer = Synchronizer(*roots, force_hash=force_hash)
        synchronizer.sync()

    def sync_all(self, force_hash=False):
        """
        Sync all temporary directories returned by `make_temp_dir` and
        `make_temp_dirs`.
        """
        self.sync_dirs(*self._temp_dirs, force_hash=force_hash)

    ########## Assertions ##########

    def assertFile(self, root_path, path, expected_content):
        """Assert contents of `path` in `root_path` are `expected_content`."""
        full_path = os.path.join(root_path, path)
        self.assertFilePresent(root_path, path)
        with open(full_path) as f:
            content = f.read()
        self.assertEqual(content, expected_content)

    def assertFilePresent(self, root_path, path):
        """Assert `path` is present in `root_path`."""
        full_path = os.path.join(root_path, path)
        self.assertTrue(os.path.exists(full_path))

    def assertFileAbsent(self, root_path, path):
        """Assert `path` is not present in `root_path`."""
        full_path = os.path.join(root_path, path)
        self.assertFalse(os.path.exists(full_path))

    def assertDirPresent(self, root_path, path):
        """Assert `path` is a directory in `root_path`."""
        full_path = os.path.join(root_path, path)
        self.assertTrue(os.path.exists(full_path))
        self.assertTrue(os.path.isdir(full_path))

    ########## Tests ##########

    def test_file_create(self):
        dir0, dir1 = self.make_temp_dirs(2)
        self.write_file(dir0, "foo", "bar")
        self.sync_all()

        self.assertFile(dir0, "foo", "bar")
        self.assertFile(dir1, "foo", "bar")

    def test_no_changes(self):
        dir0, dir1 = self.make_temp_dirs(2)
        self.write_file(dir0, "foo", "bar")
        self.write_file(dir0, "foo", "bar")
        self.sync_all()
        self.assertFile(dir0, "foo", "bar")
        self.assertFile(dir1, "foo", "bar")

        for d in [dir0, dir1]:
            root = SyncRoot(d)
            self.assertEqual(root.changes, {})

    def test_file_change(self):
        dir0, dir1 = self.make_temp_dirs(2)
        self.write_file(dir0, "foo", "bar")
        self.write_file(dir1, "foo", "bar")
        self.sync_all()

        self.write_file(dir0, "foo", "baz")
        # Force hash here because there is technically a race condition if the
        # mtime written by the first sync is the same as the mtime written by
        # the file-write directly after.
        self.sync_all(force_hash=True)

        self.assertFile(dir0, "foo", "baz")
        self.assertFile(dir1, "foo", "baz")

    def test_file_delete(self):
        dir0, dir1 = self.make_temp_dirs(2)
        self.write_file(dir0, "foo", "bar")
        self.write_file(dir1, "foo", "bar")
        self.sync_all()
        self.assertFile(dir0, "foo", "bar")
        self.assertFile(dir1, "foo", "bar")

        self.delete_file(dir0, "foo")
        self.sync_all()

        self.assertFileAbsent(dir0, "foo")
        self.assertFileAbsent(dir1, "foo")

    def test_no_overlapping_roots(self):
        """Two roots that overlap should throw an error."""
        dir0 = self.make_temp_dir()
        with self.assertRaises(ValueError):
            Synchronizer(SyncRoot(dir0), SyncRoot(dir0))

        dir1 = os.path.join(dir0, "subdir")
        with self.assertRaises(ValueError):
            Synchronizer(SyncRoot(dir0), SyncRoot(dir1))

    def test_file_in_dir(self):
        dir0, dir1 = self.make_temp_dirs(2)
        self.write_file(dir0, "subdir/foo", "bar")
        self.sync_all()
        self.assertFile(dir0, "subdir/foo", "bar")
        self.assertFile(dir1, "subdir/foo", "bar")

    def test_empty_dir(self):
        dir0, dir1 = self.make_temp_dirs(2)
        self.write_dir(dir0, "emptydir")
        self.assertDirPresent(dir0, "emptydir")
        self.assertFileAbsent(dir1, "emptydir")
        self.sync_all()
        self.assertDirPresent(dir0, "emptydir")
        self.assertDirPresent(dir1, "emptydir")

        self.delete_dir(dir1, "emptydir")
        self.sync_all()
        self.assertFileAbsent(dir0, "emptydir")
        self.assertFileAbsent(dir1, "emptydir")

    def test_sync_3(self):
        """Sync 3 directories with each other."""
        dir0, dir1, dir2 = self.make_temp_dirs(3)
        self.write_file(dir0, "foo", "bar")
        self.sync_all()

        self.assertFile(dir0, "foo", "bar")
        self.assertFile(dir1, "foo", "bar")
        self.assertFile(dir2, "foo", "bar")

    def test_sync_2_then_3(self):
        """Sync two directories then add a third and sync again."""
        dir0, dir1 = self.make_temp_dirs(2)
        self.write_file(dir0, "foo", "bar")
        self.sync_all()
        self.assertFile(dir0, "foo", "bar")
        self.assertFile(dir1, "foo", "bar")

        dir2 = self.make_temp_dir()
        self.sync_all()
        self.assertFile(dir0, "foo", "bar")
        self.assertFile(dir1, "foo", "bar")
        self.assertFile(dir2, "foo", "bar")

    def test_skipped_update(self):
        """Sync 3 dirs but skip one dir when a file is updated."""
        dir0, dir1, dir2 = self.make_temp_dirs(3)
        self.write_file(dir0, "foo", "bar")
        self.sync_all()

        # Update dir0 and sync dir0/dir1 but not dir2
        self.write_file(dir0, "foo", "baz")
        self.sync_dirs(dir0, dir1)
        self.assertFile(dir0, "foo", "baz")
        self.assertFile(dir1, "foo", "baz")
        self.assertFile(dir2, "foo", "bar")

        # dir2 should pick up the change when all are sync'd
        self.sync_all()
        self.assertFile(dir0, "foo", "baz")
        self.assertFile(dir1, "foo", "baz")
        self.assertFile(dir2, "foo", "baz")

    def test_skipped_dir_create(self):
        """Sync 3 dirs but skip one dir when a folder is created."""
        dir0, dir1, dir2 = self.make_temp_dirs(3)
        self.sync_all()

        # Make subdir in dir0 and sync dir0/dir1 but not dir2
        self.write_dir(dir0, "subdir")
        self.sync_dirs(dir0, dir1)
        self.assertDirPresent(dir0, "subdir")
        self.assertDirPresent(dir1, "subdir")
        self.assertFileAbsent(dir2, "subdir")

        # Sync all and subdir should be created in dir2 also
        self.sync_all()
        self.assertDirPresent(dir0, "subdir")
        self.assertDirPresent(dir1, "subdir")
        self.assertDirPresent(dir2, "subdir")

    def test_change_file_to_dir_with_file(self):
        """Changing a file into a directory of the same name."""
        #TODO: File must be removed before directory is created
        dir0, dir1 = self.make_temp_dirs(2)
        self.write_file(dir0, "foo", "bar")
        self.sync_all()
        self.assertFile(dir0, "foo", "bar")
        self.assertFile(dir1, "foo", "bar")

        self.delete_file(dir0, "foo")
        self.write_file(dir0, "foo/bar", "baz")
        self.sync_all()
        self.assertFile(dir0, "foo/bar", "baz")
        self.assertFile(dir1, "foo/bar", "baz")

    def test_change_file_to_dir_without_file(self):
        """Changing a file into a directory of the same name."""
        dir0, dir1 = self.make_temp_dirs(2)
        self.write_file(dir0, "foo", "bar")
        self.sync_all()
        self.assertFile(dir0, "foo", "bar")
        self.assertFile(dir1, "foo", "bar")

        self.delete_file(dir0, "foo")
        self.write_dir(dir0, "foo")
        self.sync_all()
        self.assertDirPresent(dir0, "foo")
        self.assertDirPresent(dir1, "foo")

    def test_change_dir_to_file(self):
        """Changing a directory into a file of the same name."""
        dir0, dir1 = self.make_temp_dirs(2)
        self.write_dir(dir0, "foo")
        self.sync_all()
        self.assertDirPresent(dir0, "foo")
        self.assertDirPresent(dir1, "foo")

        self.delete_dir(dir0, "foo")
        self.write_file(dir0, "foo", "bar")
        self.sync_all()
        self.assertFile(dir0, "foo", "bar")
        self.assertFile(dir1, "foo", "bar")

    def test_change_non_empty_dir_to_file(self):
        """Changing a directory into a file of the same name."""
        dir0, dir1 = self.make_temp_dirs(2)
        self.write_file(dir0, "foo/bar", "baz")
        self.sync_all()
        self.assertFile(dir0, "foo/bar", "baz")
        self.assertFile(dir1, "foo/bar", "baz")

        self.delete_file(dir0, "foo/bar")
        self.delete_dir(dir0, "foo")
        self.write_file(dir0, "foo", "bar")
        self.sync_all()
        self.assertFile(dir0, "foo", "bar")
        self.assertFile(dir1, "foo", "bar")

    def test_file_executable_bit(self):
        dir0, dir1 = self.make_temp_dirs(2)
        self.write_file(dir0, "foo", "bar")
        self.write_file(dir1, "foo", "bar")
        self.sync_all()

        # Executable bit not set
        dir0_foo = os.path.join(dir0, "foo")
        stat_result = os.stat(dir0_foo)
        self.assertFalse(stat_result.st_mode & stat.S_IXUSR)

        # Set executable bit and sync
        os.chmod(dir0_foo, stat_result.st_mode | stat.S_IXUSR)
        self.sync_all()

        # Executable bit is set
        stat_result = os.stat(dir0_foo)
        self.assertTrue(stat_result.st_mode & stat.S_IXUSR)

    def test_file_conflict(self):
        """Basic conflict where two roots edit the same file."""
        dir0, dir1 = self.make_temp_dirs(2)
        self.write_file(dir0, "foo")
        self.sync_all()

        self.write_file(dir0, "foo", "bar")
        time.sleep(0.1)
        self.write_file(dir1, "foo", "baz")
        self.sync_all()
        # File with later mtime wins
        self.assertFile(dir0, "foo", "baz")
        self.assertFile(dir1, "foo", "baz")

    def test_file_empty_dir_conflict(self):
        """Conflict of file and empty directory of the same name."""
        dir0, dir1 = self.make_temp_dirs(2)
        self.write_file(dir0, "foo")
        self.write_dir(dir1, "foo")
        self.sync_all()
        # Directory wins. File is deleted in dir0
        self.assertDirPresent(dir0, "foo")
        self.assertDirPresent(dir1, "foo")

    def test_file_dir_conflict(self):
        """Conflict of file and non-empty directory of the same name."""
        dir0, dir1 = self.make_temp_dirs(2)
        self.write_file(dir0, "foo")
        self.write_file(dir1, "foo/bar", "baz")
        self.sync_all()
        # Directory wins. File is deleted in dir0
        self.assertFile(dir0, "foo/bar", "baz")
        self.assertFile(dir1, "foo/bar", "baz")

    def test_file_update_delete_conflict(self):
        """Conflict where one root updates a file and another deletes it."""
        dir0, dir1 = self.make_temp_dirs(2)
        self.write_file(dir0, "foo", "bar")
        self.sync_all()

        self.write_file(dir0, "foo", "baz")
        self.delete_file(dir1, "foo")
        self.sync_all()
        self.assertFileAbsent(dir0, "foo")
        self.assertFileAbsent(dir1, "foo")

    def test_symlink(self):
        dir0, dir1 = self.make_temp_dirs(2)
        target1 = os.path.join(self.temp_dir_base, "target1")
        target2 = os.path.join(self.temp_dir_base, "target2")
        dir0_link = os.path.join(dir0, "foo")
        dir1_link = os.path.join(dir1, "foo")
        with open(target1, 'w') as f:
            f.write("Target Contents")
        with open(target2, 'w') as f:
            f.write("Target Contents 2")

        os.symlink(target1, dir0_link)
        self.assertTrue(stat.S_ISLNK(os.stat(dir0_link, follow_symlinks=False).st_mode))
        self.assertEqual(os.readlink(dir0_link), target1)

        self.sync_all()
        self.assertTrue(stat.S_ISLNK(os.stat(dir0_link, follow_symlinks=False).st_mode))
        self.assertTrue(stat.S_ISLNK(os.stat(dir1_link, follow_symlinks=False).st_mode))
        self.assertEqual(os.readlink(dir0_link), target1)
        self.assertEqual(os.readlink(dir1_link), target1)

        # Update where symlink points to
        os.remove(dir0_link)
        os.symlink(target2, dir0_link)
        self.sync_all()
        self.assertEqual(os.readlink(dir0_link), target2)
        self.assertEqual(os.readlink(dir1_link), target2)

    def test_symlink_dir(self):
        dir0, dir1 = self.make_temp_dirs(2)
        target = os.path.join(self.temp_dir_base, "target")
        dir0_link = os.path.join(dir0, "foo")
        dir1_link = os.path.join(dir1, "foo")

        os.makedirs(target)
        os.symlink(target, dir0_link)
        self.assertTrue(stat.S_ISLNK(os.stat(dir0_link, follow_symlinks=False).st_mode))
        self.assertEqual(os.readlink(dir0_link), target)

        self.sync_all()
        self.assertTrue(stat.S_ISLNK(os.stat(dir0_link, follow_symlinks=False).st_mode))
        self.assertTrue(stat.S_ISLNK(os.stat(dir1_link, follow_symlinks=False).st_mode))
        self.assertEqual(os.readlink(dir0_link), target)
        self.assertEqual(os.readlink(dir1_link), target)

    def test_symlink_change_to_file(self):
        dir0, dir1 = self.make_temp_dirs(2)
        target = os.path.join(self.temp_dir_base, "target")
        dir0_link = os.path.join(dir0, "foo")
        dir1_link = os.path.join(dir1, "foo")
        with open(target, 'w') as f:
            f.write("Target Contents")
        os.symlink(target, dir0_link)
        self.sync_all()

        os.remove(dir0_link)
        with open(dir0_link, 'w') as f:
            f.write("Now a file")
        self.sync_all()
        self.assertFile(dir0, "foo", "Now a file")
        self.assertFile(dir1, "foo", "Now a file")
        self.assertFalse(stat.S_ISLNK(os.stat(dir0_link, follow_symlinks=False).st_mode))
        self.assertFalse(stat.S_ISLNK(os.stat(dir1_link, follow_symlinks=False).st_mode))

    def test_file_change_to_symlink(self):
        dir0, dir1 = self.make_temp_dirs(2)
        target = os.path.join(self.temp_dir_base, "target")
        dir0_link = os.path.join(dir0, "foo")
        dir1_link = os.path.join(dir1, "foo")
        with open(target, 'w') as f:
            f.write("Target Contents")
        self.write_file(dir0, "foo", "bar")
        self.sync_all()
        self.assertFile(dir1, "foo", "bar")

        os.remove(dir0_link)
        os.symlink(target, dir0_link)
        self.sync_all()
        self.assertTrue(stat.S_ISLNK(os.stat(dir0_link, follow_symlinks=False).st_mode))
        self.assertTrue(stat.S_ISLNK(os.stat(dir1_link, follow_symlinks=False).st_mode))
        self.assertEqual(os.readlink(dir0_link), target)
        self.assertEqual(os.readlink(dir1_link), target)

    def test_delete_symlink_to_dir(self):
        dir0, dir1 = self.make_temp_dirs(2)
        target = os.path.join(self.temp_dir_base, "target")
        dir0_link = os.path.join(dir0, "foo")
        dir1_link = os.path.join(dir1, "foo")

        os.makedirs(target)
        os.symlink(target, dir0_link)
        self.sync_all()
        self.assertFilePresent(dir1, "foo")

        os.remove(dir0_link)
        self.sync_all()
        self.assertFileAbsent(dir1, "foo")


class TestSyncReverse(TestSync):
    """Same tests but reverse the order of SyncRoots passed to Synchronizer.

    The order shouldn't matter. This will find bugs where order matters when
    finding which root has the golden copy of a path.
    """
    _reverse_sync_order = True

if __name__ == "__main__":
    unittest.main()
