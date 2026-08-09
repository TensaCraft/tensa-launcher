# Instance Operations

## Contract

Every operation that can change a game instance uses one canonical key derived from
`Path.resolve(strict=False)` and the platform-normalized path. Acquisition is immediate and never
waits on the UI thread.

The coordinator covers:

- launch preparation, verification, repair, synchronization, and pre-launch backups;
- TensaCraft, Modrinth, and CurseForge pack installation;
- Modrinth content transactions and persistent mod backups;
- mod enable, disable, restore, and deletion;
- world restore.

Shared Minecraft roots (`versions`, `libraries`, `assets`, and launcher-managed Java runtimes) use a
second coordinator keyed by the Minecraft data root. This prevents installers for different
instances or launcher processes from writing the same shared files concurrently. Nested loader
work in one worker thread is reentrant because a pack installer can invoke a technical loader and
Java runtime installation as one operation.

An operation that finds the same instance busy fails with `InstanceOperationBusy`. Different
instances have different keys, although the existing global feedback session still limits some UI
flows until loader progress state becomes fully request-scoped.

## Nested Operations

Nested work receives an explicit borrowed `InstanceOperationLease`. The coordinator does not infer
ownership from a thread ID and does not use a reentrant lock.

`Game.start()` owns the launch lease and passes it to TensaCraft synchronization. The lease is
released after the game process is registered or launch preparation fails. It is not held for the
entire game session; mutating operations recheck `Game.is_game_dir_active()` while holding their
lease.

All long-running synchronous work acquires and releases its lease inside the worker thread. A Flet
coroutine must not own a lease across `await run_blocking(...)`, because cancelling the coroutine
must not release protection while its worker is still writing files.

## Lock Order

When both registries are needed, acquire them in this order:

1. instance operation coordinator;
2. shared Minecraft resource coordinator;
3. active game process registry.

`FileSyncJournal` is a recovery mechanism, not a lock. A caller must own the instance lease before
starting or recovering a journal transaction.

## Process Boundary

Each local lease also owns a persistent lock file protected by `msvcrt.locking` on Windows or
`fcntl.flock` on Linux and macOS. Acquisition is nonblocking. The file records owner diagnostics
but is never deleted: the kernel handle, not PID text, determines ownership and is released
automatically when a process exits.
