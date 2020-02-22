from collections import defaultdict
import os
import time
import logging

try:
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler
    _has_watchdog = True
except ImportError:
    _has_watchdog = False

logger = logging.getLogger(__name__)


class Synchronizer:
    """
    Coordinates multiple SyncRoot objects.
    """

    def __init__(self, *roots, force_hash=False):
        self.roots = roots

        # Error if any of the roots are:
        #    * Have the same root_path
        #    * Overlap in any way
        # This could cause an update infinite loop (which we have checks to
        # make not infinite, but still...)
        for root1 in self.roots:
            for root2 in self.roots:
                if root1 is root2: continue
                common = os.path.commonpath((root1.root_path, root2.root_path))
                if common.endswith(root1.root_path) or \
                        common.endswith(root2.root_path):
                    raise ValueError(
                        'One root cannot be inside another. Offending paths:\n'
                        f'{root1.root_path}\n{root2.root_path}'
                    )

        for root in self.roots:
            root.inspect_root_for_changes(force_hash=force_hash)

    def sync(self, trust_previous_sync=False):
        """Sync all roots with eachother.

        If `trust_previous_sync` is True, trust that the previous sync was
        performed on the same root directories that we are dealing with now.
        """
        path_seen_counter = defaultdict(lambda: 0)
        while True:
            path = self._get_changed_path()
            if path is None:
                break
            path_seen_counter[path] += 1

            # Ignore path if we've seen it too many times.
            # This check does two things: 1) prevents infinite loops due to a
            # bug, and 2) handles a very rare case where we never return
            # because the user keeps writing to this file while we're syncing
            # it.
            if path_seen_counter[path] > 10:
                logger.warning(f"Path encountered more than 10 times: {path}")
                break

            self._sync_path(path)

        # If the previous sync call didn't include the same roots that we're
        # dealing with now, any roots left out of the last sync wouldn't have
        # gotten the changes from that sync. This handles that case by checking
        # the metadata between all roots for paths that weren't just updated to
        # make sure they match.
        if not trust_previous_sync:
            for root in self.roots:
                for path in root.state.paths():
                    if path in path_seen_counter:
                        continue
                    path_seen_counter[path] += 1

                    # We know there aren't actually explicit changes detected,
                    # but this will check metadata and update if it differs
                    # between roots.
                    self._sync_path(path)

        # Write state to all roots
        for root in self.roots:
            root.write_state()

    def _sync_path(self, path):
        """
        Look at changes for the given path in all roots and perform actions
        to synchronize path in all of them.

        This expects at least one root to have detected a change.
        """

        # Detect types of changes
        was_deleted = False
        was_updated_or_created = False
        for root in self.roots:
            action, stat = root.changes.get(path, (None, None))
            if action == "deleted":
                was_deleted = True
            if action in ("updated", "created"):
                was_updated_or_created = True

            if was_deleted and was_updated_or_created:
                break

        # If no explicit changes were detected, check metadata and consider it
        # an update if they don't all agree.
        if not was_deleted and not was_updated_or_created:
            types = set()
            hashes = set()
            for root in self.roots:
                stat = root.state.path_get_stat(path)
                if stat:
                    types.add(stat.type)
                else:
                    types.add("deleted")
                hashes.add(root.state.path_get_hash(path))
            if len(types) > 1 or len(hashes) > 1:
                was_updated_or_created = True

        # If at least one root has deleted, delete for everybody.
        if was_deleted:
            for root in self.roots:
                action, stat = root.changes.get(path, (None, None))
                if action != "deleted":
                    root.perform_delete(path)
                else:
                    root.remove_change(path)

        # Somebody updated. Update the roots with older copies
        elif was_updated_or_created:
            source_root = self._get_root_with_golden_copy(path)
            self._update_roots(source_root, path)

    def _update_roots(self, source_root, path):
        """Update all roots to `source_root`'s copy of path."""
        for root in self.roots:
            if root is not source_root:

                # Only perform change if mode or stat actually changed
                old_hash = root.state.path_get_hash(path)
                old_stat = root.state.path_get_stat(path)
                new_hash = source_root.state.path_get_hash(path)
                new_stat = source_root.state.path_get_stat(path)
                if old_hash == new_hash and \
                        old_stat and new_stat and \
                        old_stat.st_mode == new_stat.st_mode:
                    root.remove_change(path)
                else:
                    root.perform_update(path, source_root.abspath(path))
            else:
                root.remove_change(path)

    def _get_root_with_golden_copy(self, path):
        """
        Looks at `path` in all roots and returns the root that has the best
        (usually most up to date) copy.
        """
        # Find the root with the latest updated time of path.
        root_stats = []  # [(root, was_changed, stat), ...]
        for root in self.roots:

            # Get root from root.changes or root.state, or None.
            was_changed = True
            stat = root.changes.get(path, (None, None))[1]
            if stat is None:
                was_changed = False
                stat = root.state.path_get_stat(path)

            if stat:
                root_stats.append((root, was_changed, stat))

        def sort_key(root_stat):
            root, was_changed, stat = root_stat
            return (
                # Prioritize actual changes seen over old info
                0 if was_changed else 1,
                # Directories win over files
                0 if stat.is_dir else 1,
                # Later paths win over older paths.
                -stat.updated_time
            )

        root_stats.sort(key=sort_key)
        golden_root, _, _ = root_stats[0]
        return golden_root

    def _get_changed_path(self):
        """Get a path that has changed in one of the roots."""
        #TODO: Optimize this so we don't have to iterate over changes
        #      n_changes**2 times.
        changes = []
        for root in self.roots:
            for path, (action, stat) in root.changes.items():
                changes.append((path, action, stat))

        # Sort order
        # From most to least significant, here are the factors that decide
        # which change is returned:
        #
        # Deleted first: Changing path from file to dir will delete that file.
        #   Perform deletions first so we delete the file first anyways.
        #   TODO: Honestly not sure if this is needed. Removing it doesn't fail
        #     any tests.
        #
        # Longest path first:
        #   For deletes, this insures that files inside directories are deleted before the
        #   directory itself.
        #
        #   For updates, this ensures that files are updated before the
        #   containing directory is changed to a file type. For example,
        #   suppose root1 changes "foo" from a directory to a file of the same name, and root2
        #   updates "foo/bar". The "foo/bar" update will be performed first,
        #   and the process of root1 updating "foo/bar" it will remove the
        #   pending change to "foo".
        def sort_key(change):
            path, action, stat = change
            action_priority = ["deleted", "updated", "created"]
            return (
                action_priority.index(action),
                -len(path),
            )

        if changes:
            changes.sort(key=sort_key)
            path, action, stat = changes[0]
            return path
        else:
            return None

    def watch(self):
        # Watch all roots and call self._sync_path(path) for any path changed.

        if not _has_watchdog:
            print("Could not import watchdog. Please install it.")
            print("    $ pip install watchdog")
            return

        self.sync()

        class SyncRootEventHandler(FileSystemEventHandler):
            def __init__(self, root):
                self.root = root
                self.updated_paths = set()

            def on_any_event(self, event):
                self.add_path(event.src_path)
                if hasattr(event, "dest_path"):
                    self.add_path(event.dest_path)

            def add_path(self, path):
                path = os.path.normpath(os.path.abspath(path))
                path = os.path.relpath(path, self.root.root_path)
                self.updated_paths.add(path)

        observer = Observer()
        event_handlers = []
        for root in self.roots:
            event_handler = SyncRootEventHandler(root)
            event_handlers.append(event_handler)
            observer.schedule(
                event_handler,
                root.root_path,
                recursive=True
            )
        observer.start()

        while True:
            time.sleep(5)

            #XXX event_handler.updated_paths updated asynchronously?
            for event_handler in event_handlers:
                for path in event_handler.updated_paths:
                    event_handler.root.inspect_path_for_changes(path)
                event_handler.updated_paths = set()

            self.sync(trust_previous_sync=True)
