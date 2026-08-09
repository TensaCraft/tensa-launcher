# Atomic Recovery

## Rule

An operation that changes more than one persistent file must define:

1. the staging boundary;
2. the durable commit point;
3. the rollback or restart behavior after every intermediate phase;
4. the owner lock held during recovery;
5. a test that interrupts activation or commit.

Single JSON, text, marker, and backup-file replacements use the helpers in
`launcher.storage.atomic`. They write and flush a temporary file in the destination directory,
then replace the destination. A failed replace preserves the previous file.

## Recovery Matrix

| Operation | Commit point | Recovery |
| --- | --- | --- |
| Modrinth pack | Identity-bound profile commit enters `committing`, then `FileTransaction.complete()` | Pre-commit interruption rolls files back; matching retry resumes an interrupted idempotent profile commit without reverting activated files |
| Modrinth content/dependencies | `FileTransaction.complete()` after the hash-backed metadata index activates | Instance lock plus schema-2 journal restores all old files and metadata |
| CurseForge import | Identity-bound profile commit succeeds after staged remote files and overrides activate | Commit failure restores files and in-memory state; interruption resumes only with the same manifest identity |
| TensaCraft install/sync | Identity-bound version profile commit succeeds after managed files activate | Pre-commit failure restores files; matching next sync resumes an interrupted profile commit |
| Loader profile install | Successful marker replaces the incomplete marker after manifest and libraries validate | An incomplete marker makes the next install repair instead of reuse the profile |
| World restore | Activated world validates and the previous directory is removed | Restore journal distinguishes staged, previous, and active directories and recovers on entry |
| Profiles and credentials | Atomic `profiles.json` replace | Failed encryption stores no plaintext and marks the profile for reauthentication |
| Mod backup/restore | Atomic destination-file replace | Failed copy or replace preserves the previous backup or installed mod |
| Launcher update | Validated replacement activates and the pending marker reaches its final phase | Platform update script restores the rollback executable when activation fails |

## Locking

Recovery and activation must hold the same instance or shared-resource lease as the original
operation. Coordinators combine process-local ownership with a nonblocking OS-backed file lock, so
two launcher processes cannot recover or activate the same target simultaneously. Lock files are
persistent metadata containers; process exit releases the kernel lock, and stale PID text never
authorizes entry.

## Storage Preflight

Large downloads, transaction staging, world backup, and world restore use one storage preflight
service. It performs a real create, flush, and delete probe in each target directory and coalesces
known byte requirements by filesystem volume. The operation stops before worker creation or live
file mutation when the target is not writable or lacks required space plus reserve.

Unknown download sizes still receive the writable-target check and remain bounded by the
downloader's maximum response size.

## Required Tests

Every new multi-file operation must cover:

- interruption before activation leaves live files unchanged;
- interruption during activation restores every original;
- commit failure rolls back activated files and in-memory state;
- interruption after commit begins resumes only with the same durable commit identity;
- rollback failure leaves `repair_required` instead of reporting success;
- a second process cannot acquire the same instance or shared-resource key;
- insufficient disk space starts no download or activation worker.
