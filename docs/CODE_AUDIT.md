# Code Audit

Audit date: 2026-07-29

## Scope

The audit covered launch and installation flows, updates, version storage, backups, reports, mod
management, background tasks, Flet lifecycle handling, and the largest application and UI modules.
The first implementation pass prioritizes data integrity, command execution safety, correct
diagnostics, and UI responsiveness. Broad rewrites are deliberately split into tested stages.

## Resolved In This Pass

### Critical

- Pending updates no longer execute command strings from a shared temporary marker. Update markers
  are typed, private to the current user, hash-validated, path-validated, and launched without a
  shell.
- Linux and macOS updates now stage and validate the replacement before activation, keep a rollback
  copy, and restore it when activation fails.
- Version deletion validates the resolved target under the Minecraft root and rejects traversal,
  root deletion, and symlink escapes.
- World restore validates the staged backup before touching the current world. A durable phase
  journal recovers crashes before or after directory activation, and backup output inside the
  source world is rejected. Restore targets come only from a launcher-selected world; mutable
  `source_path` values in backup sidecars are never trusted.

### High

- Launch diagnostics now analyze bounded, fresh artifacts through an extensible rule engine instead
  of classifying the last few log lines with a first-match heuristic.
- Reports redact user paths and token-like values before upload and disclose included diagnostic
  data in the UI.
- Mod compatibility scanning detects corrupt archives, wrong loaders, duplicate IDs, missing
  required dependencies, and declared conflicts without changing files.
- Socket connectivity checks no longer change the process-wide default timeout.
- Version persistence now keeps remote identity and description fields.
- Application restart disposes the current controller and tracked tasks without rebuilding the
  application object through its constructor.

### Installation Integrity

- Modrinth content requires a positive size plus SHA-512 or SHA-1 from the exact version response.
  Exact dependency `version_id` values are strict pins and never fall back to another release.
- A complete Modrinth dependency plan is downloaded and verified in staging, then its files,
  authorized stale removals, and metadata index are activated through one rollback transaction.
  Fuzzy filename matches never authorize deletion.
- CurseForge performs remote metadata and override preflight before changing the instance. Remote
  files require size plus a supported content hash; local and ZIP overrides reject traversal,
  links, reparse points, duplicate paths, and oversized archives. Remote files, overrides, and
  profile persistence share one rollback boundary.
- Fabric, Quilt, and NeoForge installer JARs require a supplied checksum or the strongest available
  Maven checksum sidecar before Java executes them. Remote profile IDs are validated against the
  requested loader and contained under `versions`.
- TensaCraft managed-file sync now follows `stage -> verify -> backup -> activate -> commit`.
  Stale files are moved to rollback storage rather than deleted first, profile persistence is part
  of the commit, and interrupted operations recover before a new sync can start. A durable,
  identity-bound `committing` state resumes the matching idempotent profile commit instead of
  rolling back already-committed files.
- Resumable downloads bind partial data to the URL, expected size/hash, request body, ETag or
  Last-Modified validator, and an exact `Content-Range`. Unsafe partial responses restart from
  zero.
- Per-instance operations and shared Minecraft resources are coordinated separately. Installers
  for different instances cannot concurrently mutate common versions, libraries, assets, or Java
  runtimes, while nested technical-loader work stays reentrant in one worker thread.

### Responsiveness

- Activity refresh avoids rebuilding unchanged controls every 500 ms.
- Minecraft component discovery runs outside the UI thread and ignores stale results.
- File picker services unregister on page disposal and ignore late callbacks.
- Mod compatibility diagnostics run outside the UI thread.
- Installed-mod discovery and page workers use cancellable session tasks. Late results cannot
  update a disposed page, and newer refreshes supersede stale work.

### Runtime Structure

- `Game.start` delegates preparation, synchronization, verification, backup, process launch, and
  result cleanup to a tested launch workflow with immutable request/result models.
- Version settings are composed from focused section builders instead of one large constructor.
- Provider loaders delegate installation plans, staging, rollback, and activation to application
  services instead of maintaining parallel compatibility paths.
- The downloader uses one verified requests-based transfer path for GET and POST, with request
  identity included in resumable state and POST resumption disabled.
- TensaCraft plans and diagnostic models use their typed production contracts directly; obsolete
  dictionary adapters, no-op aliases, and legacy journal writers were removed.
- Versions, loader registries, TensaCraft API clients, and game services are bound to one explicit
  application runtime. Newly created versions are prepared by their owning store before install,
  and technical component services receive the session loader registry explicitly. No mutable
  global application context or cross-session loader cache remains.

### Refactoring Outcome

- Compared with branch baseline `33002db`, launcher production sources are 852 lines smaller while
  preserving the current behavior and adding regression coverage.
- Duplicate provider orchestration, downloader transports, settings state wrappers, migration
  branches, and unused storage interfaces were removed.
- Two independent diff reviews found five behavior risks. POST dispatch, launch timing telemetry,
  immutable workflow results, concurrent version metadata preservation, and partial directory
  cleanup handling were corrected before the final validation gate.

### Storage And Credentials

- Credential encryption failure is a closed, explicit reauthentication state. Plaintext tokens are
  never retained as fallback data.
- Generic file transactions and provider-specific staging plans live in application services and
  have interruption, rollback, and commit-failure coverage.
- Modrinth managed metadata stores hashes of installed bytes. Path reuse without a matching hash
  cannot inherit ownership or authorize removal.
- Large downloads, backups, restores, and staging operations share writable-target and disk-space
  preflight checks. Modrinth replacement backup space is included before the first backup write.
- Profile create, edit, delete, default selection, authentication, and token refresh propagate
  atomic persistence failures instead of changing memory or reporting success.
- Multi-file recovery contracts and commit points are documented in `docs/RECOVERY.md`.

## Remaining Priorities

No unresolved implementation priority remains from this audit pass. New work should be based on
measured regressions, new provider contracts, or launcher reports rather than additional broad
cleanup.

## Refactoring Rules

- Preserve behavior with characterization tests before moving logic.
- Prefer small domain services and immutable results over utility collections and UI-owned logic.
- Keep names short only when their meaning remains clear in the local domain.
- Do not merge unrelated cleanup into safety fixes.
- Remove compatibility code only after supported installations no longer require it.
- Measure UI or I/O changes before calling them performance improvements.

## Review Gate

Future changes to launch, provider installation, credentials, or recovery must run the full
validation suite and packaged Windows smoke test. New multi-file writes must extend the recovery
matrix and add interruption coverage before merging.
