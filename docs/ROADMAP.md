# Roadmap

## Instance Settings Sync

Status: planned, not implemented. Recorded: 2026-09-12. Target release: not assigned.

### Goal

Let users share selected Minecraft settings between local instances without manually copying
files. Follow the supplied reference screens for interaction, using TensaLauncher's existing
Flet controls, theme, spacing, icons, and translations. This is separate from TensaCraft modpack
updates and does not require cloud storage or an account.

### User Experience

- Add a section in global settings for synchronization between instances.
- Show independent rows for game options, multiplayer servers, resource packs, command history,
  and saved creative hotbars. Each row has a category label, an enable switch, and an edit icon
  with a tooltip where configuration is available. All categories start disabled.
- Enabling a category opens a source-selection dialog before changing any files. Cancelling
  leaves the category disabled and local data unchanged.
- The dialog has instance search, a scrollable single-selection list with icons, names,
  Minecraft/loader versions, and Cancel/Sync actions. Unavailable or incompatible sources cannot
  be selected; show the reason.
- Choose participating instances explicitly, separately from the initial source. Newly created
  instances do not silently join. Use stable instance identities, not display names.
- The selected source seeds the first sync only. Later edits in any participating instance can
  update the shared settings; this is not a permanent one-way master-instance relationship.
- The edit action manages participants and category-specific choices. Replacing shared settings
  from another source requires a preview and confirmation, not an accidental reseed.
- Show useful states: disabled, up to date, pending, syncing, conflict, and failed. A manual sync
  action reports which instances were updated, skipped, or deferred.
- Disabling sync or removing an instance keeps its last local files. Removing the initial source
  does not delete the shared state or other instances' data.
- Keep rows aligned and dialogs responsive in the launcher's style. Reuse theme tokens and
  standard switches, search fields, icon buttons, and selection controls; avoid nested cards.

### Category Boundaries

| Category | Planned behavior |
| --- | --- |
| Game options | Merge supported options per key; preserve unknown/local keys. Keep resource-pack selection under its own switch. Do not copy the entire file blindly between Minecraft versions. |
| Multiplayer servers | Share server entries with deterministic duplicate and conflict handling; preserve supported metadata using an established format parser. |
| Resource packs | Treat pack files and enabled order as related but distinct data. Verify compatibility and file integrity before applying selection; never enable a missing pack. |
| Command history | Explicit opt-in because commands may contain private data. Bound retained history and avoid duplicate entries. Verify storage support for each supported game version before enabling it. |
| Creative hotbars | Share only when the source and target data formats are supported and compatible. Never blindly downgrade newer item data. |

Out of scope: worlds, mods, arbitrary mod configuration directories, screenshots, authentication
tokens, Java paths, RAM/GPU settings, and synchronization between computers.

### Consistency And Safety

- Use one small application service with category-specific read/validate/merge behavior. Keep
  filesystem work outside UI controls; do not introduce a generic plugin framework just for this.
- Persist a shared category snapshot and each participant's last applied revision. Compare current
  data against that baseline so local changes and incoming changes are distinguishable.
- Merge independent edits where the format permits. When both sides edit the same value, ask the
  user which to keep; do not silently resolve by modification time. Apply deletions only when they
  are distinguishable from missing, unreadable, or temporarily unavailable files.
- Read changed data after game exit and before preparing the next launch. Reconcile after launcher
  restart as well. Do not continuously rescan all files or copy unchanged resource-pack archives.
- Never overwrite files currently owned by a running game. Defer only affected writes, leaving
  unrelated instances and safe actions available; do not hold an instance lease for the game session.
- Apply each instance/category update through staging, validation, and recoverable replacement.
  Commit the applied revision only with the corresponding files. A failed target remains pending;
  never report whole-group success after only some instances updated.
- Keep a bounded last-known-good backup for recovery. Handle disk-full, permission, locked-file,
  cancellation, process-exit, and interrupted-commit cases without losing the original data.
- Coordinate two launcher processes through the existing operation/recovery contracts. Avoid
  holding multiple instance leases at once while propagating settings across the group.
- Respect TensaCraft managed-file ownership, forced updates, and preserve rules. Detect overlaps
  from the actual manifest, not just filenames. Exclude managed data from shared publishing and
  application unless an explicit supported policy allows it; never alternate two writers over
  the same data. Surface exclusions to the user.
- Resolve paths through the existing platform service. Validate membership and file paths; do not
  share directories through symlinks or require administrator permissions.
- Local sync works offline. Unsupported formats and unavailable instances preserve their local
  data and receive an explicit skipped/deferred status, not an empty replacement.

### Implementation Stages

- [ ] Inspect supported game file formats and version boundaries; document per-category ownership,
      merge rules, and TensaCraft overlap policy before choosing parsers or adding dependencies.
- [ ] Implement and test the shared-state service with game options and multiplayer servers first.
- [ ] Add localized settings rows, source/participant dialogs, conflict handling, and status updates.
- [ ] Integrate before-launch, after-exit, manual-sync, and restart recovery paths.
- [ ] Extend with resource packs, then command history and creative hotbars once compatibility is
      covered. Do not expose nonfunctional toggles for categories that are not implemented yet.
- [ ] Validate on Windows, Linux, and macOS, then test through a prerelease before stable inclusion.

Existing integration points to review during implementation:

- `launcher/pages/settings.py` and `launcher/pages/version_settings.py` for settings presentation.
- `launcher/application/launch_workflow.py` and `launcher/core/game.py` for process lifecycle.
- `launcher/application/version_content.py` for resource packs and options-list handling.
- `launcher/application/tensacraft_content_install.py` for managed-file ownership and preserve rules.
- `launcher/platform/paths.py` and `launcher/storage/config_store.py` for paths and persisted settings.
- [Instance operations](INSTANCE_OPERATIONS.md) and [recovery](RECOVERY.md) for coordination,
  transaction ownership, and crash recovery. Reuse these contracts instead of a second locking system.

### Acceptance Criteria

- [ ] Opt-in and source selection modify only confirmed categories and participants.
- [ ] A change made in either of two participating instances reaches the other at the next safe
      sync point; unrelated settings, files, and instances remain unchanged.
- [ ] Independent category switches work even where categories share a physical options file.
- [ ] Conflicting edits, removals, renames, unsupported formats, and version differences have
      deterministic tests and visible outcomes without silent data loss.
- [ ] TensaCraft forced synchronization and local settings synchronization do not overwrite each
      other or publish pack-controlled settings to other instances.
- [ ] Running games, concurrent launcher processes, interrupted writes, full disks, and inaccessible
      targets cannot corrupt settings or leave a false successful revision.
- [ ] Disabling sync preserves local data; a clean restart recovers pending work correctly.
- [ ] Repeated sync without changes performs no file replacements and does not block the UI.
- [ ] UI smoke tests cover source search, cancellation, empty lists, selection, disabled states,
      conflicts, and closed-page callbacks; desktop layout and localization are manually checked.
