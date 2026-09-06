# Code Audit

## Follow-up: 2026-09-06

This pass covers local content discovery, asynchronous page callbacks, persistence,
runtime detection, diagnostics, and dependency/build compatibility. The application
version and release/publication configuration are unchanged.

- Local JAR display metadata uses a bounded, per-service cache (512 entries, at most
  4096 characters per entry). File identity, size and timestamps invalidate entries;
  failures are not cached. Mod ownership and installation still verify content hashes.
  This does not introduce TensaCraft API or manifest caching.
- Component size and modification time share one directory traversal. Looking up one
  component no longer measures every installed component's contents.
- Installed mods refresh after the first asynchronous scan. Resourcepack and shader
  scans run off the UI thread; tab switches reuse pending work and ignore stale results.
- Delayed search callbacks, worker cancellation and callbacks after page disposal no
  longer repaint a stale page. Install/restore workers retain their original target.
- Config and version writes merge local edits with persisted data under shared,
  process-local file locks. Profile mutations serialize within their store. Failed writes
  remain retryable; corrupt JSON/UTF-8 does not crash loading or discard a known-good
  in-memory config. Version removal cannot be undone by a stale bound version saving.
- Selecting a technical component preserves TensaCraft pack identity and restores
  profile settings if Java selection or profile persistence fails.
- Launch-log tail reads are limited to 256 KiB. Minecraft pre-release/snapshot identifiers
  are preserved; normal Fabric/Quilt version parsing avoids a remote catalog request.
- Installed-version detection reads only the requested local manifest, without a shared
  five-minute cache. Integrity checks follow MLL's inherited metadata/JAR selection and
  include inherited libraries, rejecting missing or cyclic parents without network fallback.
- Build command logging redacts certificate passwords, including failed-command errors.
- The obsolete `setuptools<81` and `pkg_resources` build requirement is removed after a
  successful Windows build and packaged smoke test with setuptools 84.0.0.
- Direct dependency versions were checked against PyPI. Ruff advances to 0.16.6;
  Flet/Flet Desktop 0.86.5 and minecraft-launcher-lib 8.0 remain unchanged. Compatible
  transitive updates were installed locally; constraints from upstream packages remain.

### Measurements

Run `python .tools/benchmark_content.py --mods 200` to reproduce the fixture: 200 JARs
with 100 small entries each, plus 20 component directories with 50 files each.
Median milliseconds on the local Windows machine:

| Operation | Before | After (two runs) |
| --- | ---: | ---: |
| Cold JAR metadata scan | 124 | 127-154 |
| Repeated JAR metadata scan | 123 | 13-33 |
| List installed components | 140 | 12-16 |
| Look up one component | 119 | 10-11 |

Cold means a new metadata service, not a cleared operating-system disk cache. These
measurements exclude network, hash verification and UI rendering; they do not measure
whole-launcher startup. Disk load and antivirus affect timings. No cold-scan speedup
is claimed. Persistence locks do not provide multi-process coordination. Linux/macOS
packaged execution requires their respective CI runners or machines.

### Validation And Static Analysis

- Baseline: 898 tests. Final: 1036 tests passed (138 additional regression cases).
- Repository lint, typecheck, compilation, whitespace checks and `pip check` passed.
- A fresh Windows EXE was built with the repository helper and passed
  `.tools/smoke_packaged.py --target windows --artifact .build/audit/windows/TensaLauncher.exe --timeout 60`.
  This checks packaged imports/assets/runtime, not interactive gameplay or every machine.
- CodeQL `python-security-and-quality` completed with no security findings. The unused
  task re-export and redundant downloader factory wrapper were removed. The remaining
  21 findings were reviewed: six mixed-import notes in monkeypatch tests, seven Protocol
  ellipsis notes, one async-test note, six best-effort cleanup/cancellation notes, and
  one unreachable-code warning after `pytest.raises` (the tested path executes).
  Cleanup notes concern an optional empty parent directory, persistence of cleanup-only
  journal metadata, temporary probes, unsupported directory fsync, or closed task loops;
  they do not authorize skipping failed downloads or failed profile commits.
- The analysis is stored locally at `.codeql/results/audit-2026-09.sarif`.

Dependency references checked on this date: [Flet](https://pypi.org/project/flet/),
[Minecraft Launcher Lib](https://pypi.org/project/minecraft-launcher-lib/),
[Ruff 0.16.6](https://github.com/astral-sh/ruff/releases/tag/0.16.6), and
[setuptools removal of pkg_resources](https://setuptools.pypa.io/en/latest/history.html#v82-0-0).

## Previous Audit

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
