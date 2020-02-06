from collections import defaultdict
import os
import logging

logger = logging.getLogger(__name__)


class Synchronizer:
    """
    Coordinates multiple SyncRoot objects.
    """

    def __init__(self, *roots):
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
            root.inspect_root_for_changes()

    def sync(self):
        """Sync all roots with eachother."""
        path_seen_counter = defaultdict(lambda: 0)
        while True:
            path = self._get_changed_file()
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

            self._sync_file(path)

        for root in self.roots:
            root.write_state()

    def _sync_file(self, path):
        """
        Look at changes for the given path in all roots and perform actions
        to synchronize path in all of them.

        This expects at least one root to have detected a change.
        """

        # Detect if at least one root deleted
        was_deleted = False
        for root in self.roots:
            action, stat = root.changes.get(path, (None, None))
            if action == "deleted":
                was_deleted = True

        # If at least one dir has deleted, delete for everybody.
        if was_deleted:
            for root in self.roots:
                action, stat = root.changes.get(path, (None, None))
                if action != "deleted":
                    root.perform_delete(path)
                else:
                    root.reset_state(path)
            return

        # At this point, we know the file should exist, we just need to decide
        # which root has the most up to date (global) copy.

        # Find the root with the latest modified time of path.
        last_modified = None
        last_modified_root = None
        for root in self.roots:
            stat = root.stat(path)
            if stat:
                if last_modified is None or stat.modified_time > last_modified:
                    last_modified = stat.modified_time
                    last_modified_root = root

        # The root that observed the change should at least have the file. If
        # not, maybe it was deleted since then?
        # TODO: Not sure how to handle this case. We could just delete, since
        #       it's clear one of the roots deleted the file. It might be best
        #       to just call inspect_path_for_changes() again and let the
        #       process happen normally from that point.
        assert last_modified_root is not None

        # Update all other roots.
        # Note: if we have two roots with the same update (or same created
        # file, etc.) we will redundantly update one of them.
        # TODO: Compare file hashes before actually writing.
        for root in self.roots:
            if root is not last_modified_root:
                root.perform_update(path, last_modified_root.abspath(path))
            else:
                root.reset_state(path)

    def _get_changed_file(self):
        """Get a path that has changed in one of the roots."""
        for root in self.roots:
            for changed_file in root.changes:
                return changed_file

    def watch(self):
        # Watch all roots and call self._sync_file(path) for any path changed.
        raise NotImplementedError
