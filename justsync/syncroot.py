import os
import shutil
import hashlib
import random
import json
import string
import logging
import pathlib
from contextlib import contextmanager

logger = logging.getLogger(__name__)

SAFE_FILENAME_CHARS = string.ascii_lowercase + string.digits + "-"

def list_files(root):
    for cwd, dirs, files in os.walk(root):
        for f in dirs + files:
            absolute_path = os.path.join(cwd, f)
            yield os.path.relpath(absolute_path, root)

def file_in_dir(file_path, dir_path):
    if not os.path.isabs(file_path) or not os.path.isabs(dir_path):
        raise ValueError("file_path and dir_path must both be absolute")
    file_path = os.path.normpath(file_path)
    dir_path = os.path.normpath(dir_path)

    common_path = os.path.commonpath((file_path, dir_path))
    return common_path.endswith(dir_path)

def get_file_hash(abspath):
    h = hashlib.md5()
    with open(abspath, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            h.update(chunk)
    return h.hexdigest()


class SyncRoot:

    def __init__(self, root_path):
        self.root_path = os.path.abspath(root_path)
        self._hidden_dir = os.path.join(self.root_path, ".syncstate")
        self._state_file_path = os.path.join(self._hidden_dir, "state")
        self._tmp_path = os.path.join(self._hidden_dir, "tmp")

        if not os.path.exists(self.root_path):
            os.makedirs(self.root_path)
        assert os.path.isdir(self.root_path)

        os.makedirs(self._tmp_path, exist_ok=True)

        # `self._state` contains everything tracked between
        # invocations of the program.
        # It contains the following keys:
        #   * "files": Maps file paths (relative to `root_path`) to `os.stat`
        #       results.Used to tell if a file was created, updated, or
        #       deleted. Otherwise when we're syncing we wouldn't know if we
        #       should create the file in this SyncRoot, or delete it in the
        #       other.
        self._read_state()

        # Dict of changed files not yet committed to `self._state`.
        # type: {path: ("created"|"updated"|"deleted", None|os.stat(path))}
        self.changes = {}

        # Inspect all files in previous state, to see if they've been modified
        # or deleted.
        inspected_files = set()
        for path in self._state.files():
            self.inspect_file_for_changes(path)
            inspected_files.add(path)

        # List all files inside the root and inspect them if they haven't been.
        for path in list_files(self.root_path):
            if path not in inspected_files:
                self.inspect_file_for_changes(path)

    def __str__(self):
        return f"<Root {self.root_path}>"

    def abspath(self, path):
        abspath = os.path.abspath(
            os.path.join(self.root_path, path)
        )
        assert file_in_dir(abspath, self.root_path)
        return abspath

    def stat(self, path):
        try:
            s = os.stat(self.abspath(path))
            return StatResult(s)
        except FileNotFoundError:
            return None

    def should_ignore_path(self, path):
        return file_in_dir(self.abspath(path), self._hidden_dir)

    def _read_state(self):
        if os.path.exists(self._state_file_path):
            with open(self._state_file_path) as f:
                state_dict = json.load(f)
        else:
            state_dict = {}

        self._state = SyncState(state_dict)

    def _write_state(self):
        self._atomic_write(
            self._state_file_path,
            json.dumps(self._state).encode()
        )

    def inspect_file_for_changes(self, path, force_hash=False):
        if self.should_ignore_path(path):
            return

        def get_important_stat_info(stat):
            if stat is None:
                return None
            # Only include attributes of StatResult which are important to
            # detecing changes. Here are some options that could make sense:
            #
            #   * mode: We want to sync at least some mode information (ex:
            #           executable), but updating the mode doesn't change the
            #           size or mtime.
            #   * inode: May produce false positives on network file systems.
            #            Won't catch anything that ctime or mtime wouldn't
            #            catch.
            #   * uid/gid: Whenever this updates, mtime/ctime will update
            #              anyways.
            #   * size: No reason not to include this. It could catch some
            #           false negatives in obscure race conditions (another
            #           process writing to a file at the same time we're
            #           syncing it), although if the file size is the same it
            #           can't catch that case.
            #   * atime: Modified when file is accessed (depending on the
            #            filesystem). This could produce a ton of false
            #            positives.
            #   * mtime: Modified when file contents are changed. Can be set by
            #            user.
            #   * ctime: Nice because it's not user modifyable, but it updates
            #            whenever atime updates, so it has the same problems.
            #
            # We'll use mtime, size, and mode. mtime catches most cases, mode
            # will catch cases when the file contens haven't changed but the
            # mode does, and size is safe to include and may catch edge cases.
            #
            # See more discussion in [Borg issue #911]
            # (https://github.com/borgbackup/borg/issues/911)
            return (stat.st_mtime_ns, stat.st_size, stat.st_mode)

        def stats_equal(stat1, stat2):
            return get_important_stat_info(stat1) == get_important_stat_info(stat2)

        old_stat_info = self._state.file_get_stat(path)
        stat_info = self.stat(path)
        if stat_info:
            if old_stat_info is None:
                self.changes[path] = ("created", stat_info)
                logger.debug(f'{self} Detected created file "{path}"')
            elif not stats_equal(stat_info, old_stat_info) or force_hash:
                if get_file_hash(self.abspath(path)) != self._state.file_get_hash(path):
                    self.changes[path] = ("updated", stat_info)
                    logger.debug(f'{self} Detected updated file "{path}"')
        else:
            if old_stat_info:
                self.changes[path] = ("deleted", None)
                logger.debug(f'{self} Detected deleted file "{path}"')

    def reset_state(self, path):
        """
        Update state file to reflect filesystem for this path. Write the
        previously observed change in `path` to the state file and clear
        self.changes for the path. Used after an action is performed on the
        filesystem so we don't detect that action in the future as a user
        change.

        If the path is in self.changes, that state is written to the state
        file. Otherwise, the state is gathered from the filesystem.
        """
        if path in self.changes:
            change_type, stat_info = self.changes[path]
            del self.changes[path]
        else:
            stat_info = self.stat(path)
            change_type = "deleted" if stat_info is None else "updated"

        # Update self._state
        if change_type in ("created", "updated"):
            self._state.file_set_stat(path, stat_info)
        elif change_type == "deleted":
            self._state.file_delete(path)
        else:
            assert False

        self._write_state()

        # If file changed while processing, this will trigger another update.
        # Force hashing to check for changes, since another process may have
        # written to the same file at the same mtime.
        self.inspect_file_for_changes(path, force_hash=True)

    def perform_create(self, dest_path, source_abspath):
        self._perform_action("create", dest_path, source_abspath)

    def perform_update(self, dest_path, source_abspath):
        self._perform_action("update", dest_path, source_abspath)

    def perform_delete(self, path):
        self._perform_action("delete", path)

    def _perform_action(self, action, path, source_abspath=None):
        logger.debug(f'{self} Performing action {action} "{path}"')

        # Perform action before saving self._state to disk. This way if we
        # crash, the worst that happens is we detect that this file was
        # created/updated/deleted later.

        # Perform action
        if action in ("create", "update"):
            file_hash = self._atomic_copy(source_abspath, self.abspath(path),
                                          do_hash=True)
            self._state.file_set_hash(path, file_hash)
        elif action == "delete":
            try:
                os.remove(self.abspath(path))
            except FileNotFoundError:
                pass

        self.reset_state(path)

    def _atomic_copy(self, source_abspath, dest_abspath, do_hash=False):
        # Note: dest_abspath MUST be on the same filesystem as self.root_path!
        file_hash = None
        with self._temp_file() as temp_abspath:
            shutil.copy(source_abspath, temp_abspath)
            if do_hash:
                #TODO: Hash while copying
                file_hash = get_file_hash(temp_abspath)
            shutil.move(temp_abspath, dest_abspath)
        return file_hash

    def _atomic_write(self, dest_abspath, content):
        # Note: dest_abspath MUST be on the same filesystem as self.root_path!
        assert isinstance(content, bytes)
        with self._temp_file() as temp_abspath:
            with open(temp_abspath, 'wb') as f:
                f.write(content)
            shutil.move(temp_abspath, dest_abspath)

    @contextmanager
    def _temp_file(self):

        # Generate unique filename
        for i in range(100):
            filename = ''.join(random.choices(SAFE_FILENAME_CHARS, k=20))
            abspath = os.path.join(self._tmp_path, filename)
            if not os.path.exists:
                break
        if os.path.exists(abspath):
            raise FileExistsError("Could not find temp filename that isn't taken.")

        yield abspath

        # Remove temp file when done
        try:
            os.remove(abspath)
        except FileNotFoundError:
            pass

    def current_filesystem_time(self):
        with self._temp_file() as filename:
            pathlib.Path(filename).touch()
            return self.stat(filename).modified_time


class SyncState(dict):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setdefault("files", {})

    def files(self):
        return list(self["files"].keys())

    def _file_get_attr(self, path, attr):
        path_attrs = self["files"].get(path, None)
        return path_attrs.get(attr, None) if path_attrs else None

    def _file_set_attr(self, path, attr, value):
        if path not in self["files"]:
            self["files"][path] = {}
        self["files"][path][attr] = value

    def file_get_stat(self, path):
        stat = self._file_get_attr(path, "stat")
        return StatResult(stat) if stat else None

    def file_set_stat(self, path, stat_result):
        self._file_set_attr(path, "stat", StatResult(stat_result))

    def file_get_hash(self, path):
        return self._file_get_attr(path, "hash")

    def file_set_hash(self, path, value):
        self._file_set_attr(path, "hash", value)

    def file_delete(self, path):
        del self["files"][path]


class StatResult(dict):

    STAT_FIELDS = (
        "st_mode",
        "st_size",
        "st_atime_ns",
        "st_mtime_ns",
        "st_ctime_ns",
    )

    def __init__(self, stat):
        """
        Takes a stat_result returned from os.stat, or a dictionary previously
        generated from a StatResult.
        """
        super().__init__()
        for key in StatResult.STAT_FIELDS:
            if hasattr(stat, key):
                self[key] = getattr(stat, key)
            elif key in stat:
                self[key] = stat[key]

    def __getattr__(self, name):
        if name in StatResult.STAT_FIELDS:
            return self.get(name, None)
        else:
            return self.get(name)

    @property
    def modified_time(self):
        return self.st_ctime_ns
