import os
import shutil
import hashlib
import random
import json
import string
import logging
from contextlib import contextmanager

logger = logging.getLogger(__name__)

SAFE_FILENAME_CHARS = string.ascii_lowercase + string.digits + "-"

def list_paths(root):
    for cwd, dirs, paths in os.walk(root):
        for f in dirs + paths:
            absolute_path = os.path.join(cwd, f)
            yield os.path.relpath(absolute_path, root)

def path_in_dir(path, dir_path):
    if not os.path.isabs(path) or not os.path.isabs(dir_path):
        raise ValueError("path and dir_path must both be absolute")
    path = os.path.normpath(path)
    dir_path = os.path.normpath(dir_path)

    common_path = os.path.commonpath((path, dir_path))
    return common_path.endswith(dir_path)

def get_file_hash(abspath):
    h = hashlib.md5()
    with open(abspath, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            h.update(chunk)
    return h.hexdigest()


class SyncRoot:
    """
    The root of a folder to be synchronized.

    Tracks changes of the root directory persistently in a state file. On
    initialization the saved state is read in and compared to the current
    filesystem. These changes are put in the `changes` attribute and later
    resolved and moved to the saved state.

    When an argument or variable is a path, it will be a relative path inside
    self.root_path, unless the name is something like `abspath`.

    The general steps to use SyncRoot are:

    1) Call `inspect_root_for_changes()` or `inspect_path_for_changes(path)` to
    read the filesystem and get a list of changes made from the previously
    stored state. This will add to the `changes` attribute indicating what
    changed. It's safe to inspect for changes on paths that haven't actually
    changed, in which cases nothing will be added to `changes`.

    2) For each path in `changes`, resolve the change by calling one of the
    following:

        * `perform_create()`
        * `perform_update()`
        * `perform_delete()`
        * `reset_state()`

    These all change the internal state to reflect the filesystem and clear any
    changes in the `changes` attribute for the path. The `perform_*()` variants
    will change the filesystem in some way beforehand. Which one is called
    depends on how conflicts are resolved between multiple SyncRoot objects.
    For example: if a file is changed and you want this root's copy to be
    distributed to the others, call `reset_state()` on this root and call
    `perform_update()` on all others.

    3) Call `write_state()` to write the internal state to disk.

    At this point the sync has finished. If you want to continue using the
    object, start back at step 1.

    Note that the first time a SyncRoot is created for a given root path, it's
    state will be empty. This is perfectly fine, since any files on the
    filesystem will be considered to be just created.
    """

    def __init__(self, root_path):
        self.root_path = os.path.abspath(root_path)
        self._hidden_dir = os.path.join(self.root_path, ".syncstate")
        self._state_file_path = os.path.join(self._hidden_dir, "state")
        self._tmp_path = os.path.join(self._hidden_dir, "tmp")

        if not os.path.exists(self.root_path):
            os.makedirs(self.root_path)
        assert os.path.isdir(self.root_path)

        os.makedirs(self._tmp_path, exist_ok=True)

        # `self._state` is an instance of SyncState which contains everything
        # tracked between invocations of the program.
        self._read_state()

        # Dict of changed paths not yet committed to `self._state`.
        # Type: {path: ("created"|"updated"|"deleted", None|StatResult)}
        self.changes = {}

    def __str__(self):
        return f"<Root {self.root_path}>"

    def _atomic_copy(self, source_abspath, dest_abspath, do_hash=False):
        """
        Copy from source to dest atomically by copying to a temporary file then
        moving it.

        Note: `dest_abspath` MUST be on the same filesystem as
        `self.root_path`! Otherwise the move may not be atomic.

        If `do_hash=True` then the hash of the copied file will be returned.
        """
        file_hash = None
        with self._temp_file() as temp_abspath:
            shutil.copy(source_abspath, temp_abspath)
            if do_hash:
                #TODO: Hash while copying
                file_hash = get_file_hash(temp_abspath)
            shutil.move(temp_abspath, dest_abspath)
        return file_hash

    def _atomic_write(self, dest_abspath, content):
        """
        Write contents to path atomically by writing to a temporary file then
        moving it.

        Note: `dest_abspath` MUST be on the same filesystem as
        `self.root_path`! Otherwise the move may not be atomic.
        """
        assert isinstance(content, bytes)
        with self._temp_file() as temp_abspath:
            with open(temp_abspath, 'wb') as f:
                f.write(content)
            shutil.move(temp_abspath, dest_abspath)

    @contextmanager
    def _temp_file(self):
        """
        Context manager to make a temporary filename and ensure it's deleted
        when finished.
        """

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

    def _read_state(self):
        """Read the state file into `self._state` from the filesystem."""
        if os.path.exists(self._state_file_path):
            with open(self._state_file_path) as f:
                state_dict = json.load(f)
        else:
            state_dict = {}

        self._state = SyncState(state_dict)

    def write_state(self):
        """Write the internal state to permenant storage."""
        self._atomic_write(
            self._state_file_path,
            json.dumps(self._state).encode()
        )

    def abspath(self, path):
        """Return the absolute path of the given path."""
        abspath = os.path.abspath(
            os.path.join(self.root_path, path)
        )
        assert path_in_dir(abspath, self.root_path)
        return abspath

    def stat(self, path):
        """Return a StatResult object of the path."""
        try:
            s = os.stat(self.abspath(path))
            return StatResult(s)
        except FileNotFoundError:
            return None

    def should_ignore_path(self, path):
        """
        Return True if no changes should ever be detected from the given path.
        """
        return path_in_dir(self.abspath(path), self._hidden_dir)

    def inspect_root_for_changes(self):
        """
        Compare the filesystem to internal state and add to `self.changes` if
        they are different.
        """

        # Inspect all paths in previous state, to see if they've been modified
        # or deleted.
        inspected_paths = set()
        for path in self._state.paths():
            self.inspect_path_for_changes(path)
            inspected_paths.add(path)

        # List all paths inside the root and inspect them if they haven't been.
        for path in list_paths(self.root_path):
            if path not in inspected_paths:
                self.inspect_path_for_changes(path)


    def inspect_path_for_changes(self, path, force_hash=False):
        """
        Compare the given path to internal state and add to `self.changes` if
        they are different.
        """
        if self.should_ignore_path(path):
            return

        # Only include attributes of StatResult which are important to detecing
        # changes. Here are some options that could make sense:
        #
        #   * mode: We want to sync at least some mode information (ex:
        #           executable), but updating the mode doesn't change the
        #           size or mtime.
        #   * inode: May produce false positives on network file systems. Won't
        #            catch anything that ctime or mtime wouldn't catch.
        #   * uid/gid: Whenever this updates, mtime/ctime will update anyways.
        #   * size: No reason not to include this. It could catch some false
        #           negatives in obscure race conditions (another process
        #           writing to a file at the same time we're syncing it),
        #           although if the file size is the same it can't catch that
        #           case.
        #   * atime: Modified when file is accessed (depending on the
        #            filesystem). This could produce a ton of false positives.
        #   * mtime: Modified when file contents are changed. Can be set by
        #            user.
        #   * ctime: Nice because it's not user modifyable, but it updates
        #            whenever atime updates, so it has the same problems.
        #
        # We'll use mtime, size, and mode. mtime catches most cases, mode will
        # catch cases when the file contens haven't changed but the mode does,
        # and size is safe to include and may catch edge cases.
        #
        # See more discussion in [Borg issue #911]
        # (https://github.com/borgbackup/borg/issues/911)
        def get_important_stat_info(stat):
            if stat is None:
                return None
            return (stat.st_mtime_ns, stat.st_size, stat.st_mode)

        def stats_equal(stat1, stat2):
            return get_important_stat_info(stat1) == get_important_stat_info(stat2)

        old_stat_info = self._state.path_get_stat(path)
        stat_info = self.stat(path)
        if stat_info:
            # File exists.
            # If file didn't used to exist. Make "created" change.
            if old_stat_info is None:
                self.changes[path] = ("created", stat_info)
                logger.debug(f'{self} Detected created file "{path}"')
            # If the file stat has changed, check the file hash and make a
            # "updated" change if it's different.
            elif not stats_equal(stat_info, old_stat_info) or force_hash:
                current_hash = get_file_hash(self.abspath(path))
                saved_hash = self._state.path_get_hash(path)
                if current_hash != saved_hash:
                    self.changes[path] = ("updated", stat_info)
                    logger.debug(f'{self} Detected updated file "{path}"')
        else:
            # File doesn't exist.
            # Make "deleted" change if it used to exist.
            if old_stat_info:
                self.changes[path] = ("deleted", None)
                logger.debug(f'{self} Detected deleted file "{path}"')

    def reset_state(self, path):
        """
        Update internal state to reflect filesystem for this path. Write the
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
            self._state.path_set_stat(path, stat_info)
        elif change_type == "deleted":
            self._state.path_delete(path)
        else:
            assert False

        # If path changed while processing, this will trigger another update.
        # Force hashing to check for changes, since another process may have
        # written to the same path at the same mtime.
        self.inspect_path_for_changes(path, force_hash=True)

    def perform_create(self, dest_path, source_abspath):
        self._perform_action("create", dest_path, source_abspath)

    def perform_update(self, dest_path, source_abspath):
        self._perform_action("update", dest_path, source_abspath)

    def perform_delete(self, path):
        self._perform_action("delete", path)

    def _perform_action(self, action, path, source_abspath=None):
        logger.debug(f'{self} Performing action {action} "{path}"')

        # Perform action before saving self._state to disk. This way if we
        # crash, the worst that happens is we detect that this path was
        # created/updated/deleted later.

        # Perform action
        if action in ("create", "update"):
            file_hash = self._atomic_copy(source_abspath, self.abspath(path),
                                          do_hash=True)
            self._state.path_set_hash(path, file_hash)
        elif action == "delete":
            try:
                os.remove(self.abspath(path))
            except FileNotFoundError:
                pass

        self.reset_state(path)


class SyncState(dict):
    """The state data structure stored for a SyncRoot.

    This is just a dictionary with some helper methods to provide structure.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setdefault("paths", {})

    def paths(self):
        """Return a list of paths."""
        return list(self["paths"].keys())

    def _path_get_attr(self, path, attr):
        path_attrs = self["paths"].get(path, None)
        return path_attrs.get(attr, None) if path_attrs else None

    def _path_set_attr(self, path, attr, value):
        if path not in self["paths"]:
            self["paths"][path] = {}
        self["paths"][path][attr] = value

    def path_get_stat(self, path):
        stat = self._path_get_attr(path, "stat")
        return StatResult(stat) if stat else None

    def path_set_stat(self, path, stat_result):
        self._path_set_attr(path, "stat", StatResult(stat_result))

    def path_get_hash(self, path):
        return self._path_get_attr(path, "hash")

    def path_set_hash(self, path, value):
        self._path_set_attr(path, "hash", value)

    def path_delete(self, path):
        del self["paths"][path]


class StatResult(dict):
    """A dict of the info we're interested from a path's stat.

    The constructor can take a dict a StatResult dict, or a stat_result
    returned from os.stat. The resulting StatResult can serialized as a
    dictionary, which can then be deserialized and turned into a new StatResult
    object by being passed into the construtor.
    """

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
