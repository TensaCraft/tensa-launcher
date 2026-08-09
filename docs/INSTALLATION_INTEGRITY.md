# Installation Integrity

## Contract

Remote content is never activated directly from a network response. Each provider must:

1. Validate remote identity, URL, filename, destination containment, declared size, and the
   strongest supported checksum.
2. Download every required artifact into a transaction staging directory.
3. Verify staged bytes before any live file changes.
4. Back up every live file that will be replaced or removed.
5. Activate all files and atomic metadata as one transaction.
6. Keep rollback data until the profile or metadata commit succeeds.

Unknown local files are user-owned. A provider may remove a file only when authoritative metadata
proves ownership or an explicit server manifest declares its directory as managed.

## Journal States

`FileSyncJournal` uses schema 2 for multi-file changes:

- `staging`: artifacts are being downloaded or copied; live files are unchanged.
- `prepared`: all staged artifacts passed validation.
- `applying`: original files are moving to backup and staged files are activating.
- `files_applied`: live files changed, but commit cleanup has not completed.
- `committing`: live files are active and an identity-bound external commit is in progress. Recovery
  preserves the files and accepts only the matching idempotent commit callback.
- `complete`: the new state is committed; leftover rollback cleanup is best-effort.
- `rolled_back`: the original state was restored.
- `repair_required`: rollback could not finish. New transactions are blocked until recovery
  succeeds.

A completed transaction is never reopened for rollback because cleanup failed. An interrupted
pre-commit transaction is rolled back before a new operation starts. An interrupted `committing`
transaction is resumed only by the same provider operation and stable commit identity; a different
pack or manifest cannot complete it. TensaCraft synchronization defers recovery while the game
directory is active so it does not replace files used by a running game.
Journal recovery and activation run under the instance operation coordinator so concurrent
launcher tasks cannot recover or overwrite each other's transaction.

World restore has its own durable phase journal. Recovery distinguishes a staged world, the
previous live world, and the activated replacement, so a process crash cannot silently discard
the only valid copy.

Minecraft versions, libraries, assets, and launcher-managed Java runtimes are shared across game
instances. A dedicated coordinator serializes writes to the Minecraft data root. Both instance
and shared-resource coordinators hold nonblocking OS-backed file locks in addition to local
ownership, so separate launcher processes cannot mutate the same target. Nested pack,
technical-loader, and Java installation calls in one worker thread share their borrowed lease.

Large staging and restore operations run a common storage preflight before workers start. The
preflight verifies actual write access and groups known byte requirements by filesystem volume.

## Provider Rules

### Modrinth

- Use the exact version endpoint for pinned dependency IDs.
- Require SHA-512 or SHA-1 and the declared file size.
- Treat search titles, slugs, and filename prefixes as display hints only.
- Permit stale-file removal only for an exact stored `modrinth_project_id`.
- Commit the entire dependency plan and `modrinth-content.json` together.

### CurseForge

- Complete metadata and override preflight before staging.
- Require a supported checksum and `fileLength` for every required remote file.
- Reject traversal, absolute paths, links, reparse points, duplicates, and archive limit violations.
- Stage remote mods and overrides before activating either group.
- Commit profile persistence before discarding rollback data and restore both files and the
  in-memory profile when persistence fails.

### Loader Installers

- Require HTTPS plus SHA-512, SHA-256, or SHA-1 from provider metadata or Maven sidecars.
- Validate JAR structure before execution.
- Validate the installed profile ID against the requested loader ID and contain it under
  `minecraft/versions`.
- Treat missing manifests, libraries, or incomplete markers as an installation that must be
  repaired.

### TensaCraft

- Delete only manifest-owned paths or paths inside an explicit managed directory.
- Preserve configured files and every unknown local file.
- Stage all downloads, then activate replacements and stale removals together.
- Include profile persistence in the transaction commit and restore the in-memory profile on
  failure.
