
# JustSync

Sync a set of local directories with each other.

Just give a list of two or more directories to sync:

    $ justsync /dir1/ /dir2/

Pass `--watch` to continually watch the directories and keep them in sync
(requires `watchdog` python package):

    $ justsync --watch /dir1/ /dir2/

**Warning:** JustSync works in my normal usecase, but everybody's usecase is
different. There are some decisions made and features not yet implemented which
may be important for your data integrity. Here is a non-exhaustive list:

* Conflicts are not implemented. The latest modified time wins.
* If a file is detected as deleted, it is deleted in all directories, even if
  it's modified in another directory.
* If one directory has a file, and another has a directory of the same name,
  the directory always wins. The file is deleted and replaced with the file's
  contents.
  * This decision was made because a directory may have many files in it, which
    would all have to be deleted to create the directory, as opposed to a
    single file deleted in the current implementation.
* Only tested on Linux. I have no plans to test on anything but Linux, but
  bugfixes, testing, and test infrastructure is welcome.
* Nested sync directories might work, but haven't been tested.
* Since a ".syncstate/" directory is created, JustSync isn't meant to be used
  for one-off directory syncs, but rather a long-running or periodic sync.
  JustSync does not look at parent directories to see if there is already a
  ".syncstate/" in a parent directory.

Thoughts and suggestions are welcome.

## How JustSync is Unique

There is no left/right or source/target directories. Just a set of directories
to sync with eachother. JustSync aims to be a simple command-line program that
can easily be scripted.

## What JustSync is Not

JustSync does not directly support remove filesystems (ssh, nfs, etc.). Instead
you should mount them onto the local filesystem, for example with sshfs. If you
want to sync files between multiple computers where one of them is roaming, you
either need to mount the remote directory before syncing, or use a different
sync program, such as Syncthing.

In otherwords: JustSync just syncs directories, nothing else.

## How JustSync Works

Since there is no distinction between source and target, how does it
distinguish between a deleted file in one directory and a created file in
another directory? JustSync creates state directory ".syncstate/" in the root
folder which stores data between syncs. When JustSync runs, it inspects the
previous state to tell if a file was created, updated or deleted.

## Similar Programs

* [dirsync](https://pypi.org/project/dirsync/)
* [directsync](https://pypi.org/project/directsync/)
* [sftpclone](https://pypi.org/project/sftpclone/)
* [rsync](https://rsync.samba.org/)
  * Not meant for 2-way sync. ([Stack Overflow](https://stackoverflow.com/questions/2936627/two-way-sync-with-rsync))
* [csync](https://www.csync.org/)
* [unison](http://www.cis.upenn.edu/~bcpierce/unison/)
* [osync](http://www.netpower.fr/osync)
* [bitpocket](https://github.com/sickill/bitpocket)
* [FreeFileSync](https://freefilesync.org/)
* [pytograph](https://github.com/joshdick/pytograph)

* dropbox, nextcloud, owncloud, etc.
