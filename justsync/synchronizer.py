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

        # If at least one dir has deleted, delete for everybody.
        if was_deleted:
            for root in self.roots:
                action, stat = root.changes.get(path, (None, None))
                if action != "deleted":
                    root.perform_delete(path)
                else:
                    root.remove_change(path)

        # Somebody updated. Update the roots with older copies
        elif was_updated_or_created:
            source_root = self._get_last_updated_root(path)
            self._update_roots(source_root, path)

    def _update_roots(self, source_root, path):
        """Update all roots to `source_root`'s copy of path."""
        # TODO: Compare file hashes before actually writing.
        for root in self.roots:
            if root is not source_root:
                root.perform_update(path, source_root.abspath(path))
            else:
                root.remove_change(path)

    def _get_last_updated_root(self, path):
        # Find the root with the latest updated time of path.
        last_updated = None
        last_updated_root = None
        for root in self.roots:

            # Get root from root.changes or root.state, or None.
            stat = root.changes.get(path, (None, None))[1]
            if stat is None:
                stat = root.state.path_get_stat(path)

            if stat:
                if last_updated is None or stat.updated_time > last_updated:
                    last_updated = stat.updated_time
                    last_updated_root = root

        return last_updated_root

    def _get_changed_path(self):
        """Get a path that has changed in one of the roots."""
        #TODO: Optimize this so we don't have to iterate over changes
        #      n_changes**2 times.
        delete_changes = []
        other_changes = []
        for root in self.roots:
            for path, (action, stat) in root.changes.items():
                if action == "deleted":
                    delete_changes.append(path)
                else:
                    other_changes.append(path)

        if delete_changes:
            # Sort by length, longest first. This ensures that files inside
            # directories are delteed before the directory itself.
            delete_changes.sort(key=len, reverse=True)
            return delete_changes[0]
        elif other_changes:
            return other_changes[0]
        else:
            return None

    def watch(self):
        # Watch all roots and call self._sync_path(path) for any path changed.
        raise NotImplementedError
