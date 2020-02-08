import os
import sys
import unittest
import tempfile
import logging
import shutil

from justsync import SyncRoot, Synchronizer

root_logger = logging.getLogger()
root_logger.setLevel(logging.DEBUG)
root_logger.addHandler(logging.StreamHandler(sys.stdout))

class TestSync(unittest.TestCase):

    def setUp(self):
        self._temp_dir_base = None
        self._temp_dirs = []

    def tearDown(self):
        if self._temp_dir_base:
            shutil.rmtree(self._temp_dir_base)

    ########## Test Tools ##########

    def make_temp_dir(self):
        if not self._temp_dir_base:
            self._temp_dir_base = tempfile.mkdtemp()

        i = 0
        path = os.path.join(self._temp_dir_base, str(i))
        while os.path.exists(path):
            i += 1
            path = os.path.join(self._temp_dir_base, str(i))

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

    def sync_dirs(self, *dirs):
        """
        Create a SyncRoot for each of the given dirs and synchronize
        them.
        """
        roots = [SyncRoot(d) for d in dirs]
        synchronizer = Synchronizer(*roots)
        synchronizer.sync()

    def sync_all(self):
        """
        Sync all temporary directories returned by `make_temp_dir` and
        `make_temp_dirs`.
        """
        self.sync_dirs(*self._temp_dirs)

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
        self.sync_all()

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
        dir0_emptydir = os.path.join(dir0, "emptydir")
        dir1_emptydir = os.path.join(dir1, "emptydir")
        os.makedirs(dir0_emptydir)
        self.assertTrue(os.path.isdir(dir0_emptydir))
        self.sync_all()
        self.assertTrue(os.path.isdir(dir1_emptydir))

        os.rmdir(dir1_emptydir)
        self.sync_all()
        self.assertFalse(os.path.exists(dir0_emptydir))
        self.assertFalse(os.path.exists(dir1_emptydir))

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
        os.makedirs(os.path.join(dir0, "subdir"))
        self.sync_dirs(dir0, dir1)
        self.assertTrue(os.path.isdir(os.path.join(dir0, "subdir")))
        self.assertTrue(os.path.isdir(os.path.join(dir1, "subdir")))
        self.assertFalse(os.path.isdir(os.path.join(dir2, "subdir")))

        # Sync all and subdir should be created in dir2 also
        self.sync_all()
        self.assertTrue(os.path.isdir(os.path.join(dir0, "subdir")))
        self.assertTrue(os.path.isdir(os.path.join(dir1, "subdir")))
        self.assertTrue(os.path.isdir(os.path.join(dir2, "subdir")))

    @unittest.skip
    def test_change_file_to_dir(self):
        """Changing a file into a directory of the same name."""
        #TODO: File must be removed before directory is created
        raise NotImplementedError()

    @unittest.skip
    def test_change_dir_to_file(self):
        """Changing a directory into a file of the same name."""
        #TODO: Directory must be removed before file is created
        raise NotImplementedError()

    @unittest.skip
    def test_file_executable_bit(self):
        raise NotImplementedError()

    @unittest.skip
    def test_file_conflict(self):
        """Basic conflict where two roots edit the same file."""
        raise NotImplementedError()

    @unittest.skip
    def test_file_dir_conflict(self):
        """Conflict of file and directory of the same name."""
        raise NotImplementedError()

    @unittest.skip
    def test_file_update_delete_conflict(self):
        """Conflict where one root updates a file and another deletes it."""
        raise NotImplementedError()

    @unittest.skip
    def test_no_infinite_loop(self):
        """Test infinite loop detection.

        We detect loops where the same file is sync'd over and over again. This
        can be caused by overlapping roots (which we have a separate check for)
        or other bugs. Here we artificially induce an infinite loop to see if
        it's detected and broken.
        """
        raise NotImplementedError()


if __name__ == "__main__":
    unittest.main()
