# Moving to GileaLauncher

TensaLauncher can offer a one-time transition to the TensaCraft edition of
[GileaLauncher v0.0.9](https://github.com/TensaCraft/GileaLauncher/releases/tag/v0.0.9).
This feature is available in packaged Windows x64 builds. It requires explicit
confirmation even when automatic launcher updates are enabled. Declining leaves
TensaLauncher usable, including its normal update checks. The launcher settings
contain a retry action.

## Compatibility

- The migration downloads `GileaLauncher-tensa.exe`, not the generic edition or
  an installer. Its expected size and SHA-256 are pinned in the migration code.
  An upstream replacement of that asset requires a reviewed compatibility change.
- Windows WebView2 Runtime must already be installed. The prerequisite check uses
  Microsoft's [documented registry locations](https://learn.microsoft.com/microsoft-edge/webview2/concepts/distribution).
  The migration does not install system components or request elevation.
- Language, Minecraft directory, RAM, supported appearance and launcher settings,
  Java selections, backup preferences and per-build launch options are adapted.
  Unsupported settings are disclosed before committing; their originals remain
  in the backup and in TensaLauncher's state.
- Account names, UUIDs, default selection and per-build account bindings are
  preserved. Compatible Fernet-encrypted tokens retain their encryption key.
  Undecryptable tokens require a new sign-in in Gilea; the account identity stays.
- Gilea stores each build in `games/<folder>/version.json`. The migration writes
  these records instead of importing a central `versions.json` into Gilea's state.
  Existing folder names and old shortcut aliases are preserved.
- Builds already directly inside `Minecraft/games/` keep their worlds, mods and
  resources in place. Other build directories require a second consent listing
  the full paths. Their files are copied and verified; originals are not deleted.
  Hidden, ambiguous, overlapping, linked or conflicting paths block the transition.
- Existing Gilea state, storage relocation pointers or installation files block
  automatic merging. Close Gilea and all games before migration. Do not remove
  an existing Gilea installation's data to bypass the warning.

The initial destination is `%LOCALAPPDATA%/GileaLauncher`; the executable goes to
`%LOCALAPPDATA%/Programs/GileaLauncher`. Gilea keeps using the original Minecraft
root and manages its own later updates. Tensa's path overrides are not forwarded.

## Preservation And Recovery

The old executable, uninstall registration, config, accounts, encryption key and
build registry remain intact. Backups and a protected transaction journal are
stored under `<Tensa state>/gilea-migration/`. They contain account credentials:
never attach this folder to a public issue or share it without careful redaction.

Journal phases are `preparing`, `staged`, `activating`, `committed`, `rolled_back`
and `repair_required`. Windows write guards and instance-operation locks protect
the original snapshot. The journal is authenticated with a local, access-controlled
key. Files activate through temporary files on each destination volume.

An interrupted transition is checked on the next ordinary Tensa startup. Automatic
cleanup removes only recorded files whose content and file identity still match.
Modified or replaced files are kept and require manual investigation. A crash
during a file copy, a missing journal key or changed root can also require manual
repair; do not delete the journal or key to force a retry. Protected staged data
and backups are deliberately retained. They can occupy space comparable to copied
external builds even after success. Cancellation can wait for the current local
file operation; network downloads check cancellation between chunks.

To open the original launcher without forwarding, use the separate
**TensaLauncher Recovery** shortcut or launch its original executable with:

```text
TensaLauncher.exe --stay-on-tensa
```

Ordinary old shortcuts forward to Gilea after a committed transfer, including
their build selection. The recovery flag also works when a Tensa process is
already running. Unknown build aliases or an unavailable Gilea executable fall
back to Tensa. Existing user-owned desktop shortcuts are not overwritten.

Successful data commit and starting a process are separate events. An immediate
Gilea exit is treated as a launch failure, except for a successful secondary-process
handoff to an already running copy of the same executable. Neither event alone
proves a healthy application UI.
A launch failure does not delete committed accounts or builds; retry reuses them.

**Rollback limit:** both launchers share normal Minecraft builds. Returning to
Tensa does not undo changes a game or Gilea subsequently makes to worlds, mods,
configuration or shared resources. Keep independent world backups. Do not run
both launchers against the same builds concurrently.

## Verification

Automated tests use synthetic accounts, worlds, temporary directories and fake
downloads. They cover adapter compatibility, checksum failures, source changes,
external-copy consent, interruption, rollback ownership, retry, cancellation,
startup forwarding, shortcut conflicts and localization. Fixtures are checked
against Gilea v0.0.9's config, account store, per-folder build store and CLI source.
They do not sign in, launch real worlds or certify the packaged GUI.
Windows-only tests also exercise actual current-user directory ACLs, source write
guards and process discovery using temporary files and the test process itself.

Before release, complete a fixture-only Windows smoke test on an identified
secondary monitor. Use a disposable Windows account (to isolate LocalAppData and
desktop shortcuts) with two synthetic accounts and two builds, including an
external directory with spaces and Unicode. Do not use real account tokens.

1. Start a packaged Tensa build there; decline the transition and check normal
   startup, then retry from settings. Cancel a download and retry again.
2. Accept the transition and the external-copy confirmation. Confirm Gilea's
   TensaCraft branding, Ukrainian/English language, both accounts and builds,
   account assignments, custom Minecraft root, Java and JVM options.
3. Check old build shortcuts and the recovery shortcut. Verify the originals
   remain intact and that a later Gilea update still permits forwarding.
4. Exercise missing WebView2, occupied destinations and a running game without
   modifying the main Windows account or real worlds.

This interactive packaged smoke test has not yet been completed for the bridge.
No release or automatic user migration is implied by passing the unit tests.
