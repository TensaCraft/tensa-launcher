from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

from launcher.application.file_transaction import FileTransaction, FileTransactionPlan
from launcher.application.instance_operations import (
    InstanceOperationBusy,
    InstanceOperationCoordinator,
)
from launcher.application.modrinth_mods import ModrinthInstallCandidate
from launcher.application.storage_preflight import (
    StorageRequest,
    ensure_storage_available,
)
from launcher.core.async_downloader import AsyncDownloader, DownloadTask

MODRINTH_CONTENT_JOURNAL = ".tensalauncher-modrinth-sync.json"
_METADATA_SPACE_RESERVE = 4 * 1024 * 1024


class ModrinthContentInstallBusy(RuntimeError):
    pass


class ModrinthContentGameRunning(RuntimeError):
    pass


class ModrinthContentBackupError(RuntimeError):
    pass


class ModrinthContentInstaller:
    def __init__(
        self,
        content: Any,
        modrinth_mods: Any,
        *,
        instance_operations: InstanceOperationCoordinator | None = None,
    ) -> None:
        self._content = content
        self._modrinth_mods = modrinth_mods
        self._instance_operations = instance_operations

    def install(
        self,
        version: Any,
        candidates: Sequence[ModrinthInstallCandidate],
        *,
        content_key: str,
        target_dir: Path,
    ) -> dict[str, Path]:
        version_root = self._content.get_version_directory(version)
        if version_root is None:
            raise FileNotFoundError("Content directory is unavailable")
        version_root = Path(version_root).resolve()
        target_dir = Path(target_dir).resolve()
        if target_dir == version_root or not _is_path_inside(target_dir, version_root):
            raise ValueError("Modrinth content directory is outside the version directory")

        try:
            if self._instance_operations is not None:
                return self._instance_operations.execute(
                    version_root,
                    "modrinth_content_install",
                    self._install_locked,
                    version,
                    candidates,
                    content_key,
                    target_dir,
                    version_root,
                )
            return self._install_locked(
                version,
                candidates,
                content_key,
                target_dir,
                version_root,
            )
        except InstanceOperationBusy as exc:
            raise ModrinthContentInstallBusy(str(exc)) from exc

    def _install_locked(
        self,
        version: Any,
        candidates: Sequence[ModrinthInstallCandidate],
        content_key: str,
        target_dir: Path,
        version_root: Path,
    ) -> dict[str, Path]:
        pending = [candidate for candidate in candidates if candidate.action != "satisfied"]
        if not pending:
            return {}

        from launcher.core.game import Game

        game_is_running = Game.is_game_dir_active(version_root)
        live_install_allowed = content_key in {"resourcepacks", "shaders"} or (
            content_key == "mods"
            and all(candidate.action == "install" for candidate in pending)
        )
        if game_is_running and not live_install_allowed:
            raise ModrinthContentGameRunning(str(version_root))

        destinations: dict[str, Path] = {}
        destination_keys: set[str] = set()
        stale_paths: list[Path] = []
        records = []
        relative_destinations: list[str] = []
        for candidate in pending:
            destination = _safe_destination(
                target_dir,
                candidate.install_file.filename,
            )
            relative_destination = destination.relative_to(version_root).as_posix()
            destination_key = relative_destination.casefold()
            if destination_key in destination_keys:
                raise RuntimeError(
                    f"Duplicate Modrinth destination: {relative_destination}"
                )
            if game_is_running and content_key == "mods" and destination.exists():
                raise ModrinthContentGameRunning(str(destination))
            destination_keys.add(destination_key)
            destinations[candidate.project_id] = destination
            relative_destinations.append(relative_destination)

            stale_path = self._authorized_stale_path(
                candidate,
                target_dir,
                destination,
            )
            if stale_path is not None:
                stale_paths.append(stale_path)
            records.append(
                (
                    content_key,
                    destination,
                    candidate.project,
                    candidate.version_data,
                    candidate.install_file,
                )
            )

        metadata_path = (
            version_root
            / ".tensalauncher"
            / self._content.MODRINTH_METADATA_FILE
        )
        metadata_relative = metadata_path.relative_to(version_root).as_posix()
        stale_relative = [
            path.relative_to(version_root).as_posix()
            for path in stale_paths
        ]
        transaction = FileTransaction(
            version_root,
            FileTransactionPlan(
                operation="modrinth-content-install",
                replacements=[*relative_destinations, metadata_relative],
                stale=stale_relative,
                staged_bytes=(
                    sum(
                        max(0, int(candidate.install_file.size or 0))
                        for candidate in pending
                    )
                    + _METADATA_SPACE_RESERVE
                ),
            ),
            journal_filename=MODRINTH_CONTENT_JOURNAL,
        )
        backup_candidates = (
            [
                candidate
                for candidate in pending
                if candidate.action == "replace"
                and candidate.installed_item is not None
            ]
            if content_key == "mods"
            else []
        )
        backup_requests = self._backup_storage_requests(
            target_dir,
            backup_candidates,
        )
        ensure_storage_available(
            [transaction.storage_request(), *backup_requests]
        )
        for candidate in backup_candidates:
            if not self._content.create_backup(
                target_dir,
                candidate.installed_item,
            ):
                raise ModrinthContentBackupError(candidate.install_file.filename)

        def stage(current: FileTransaction) -> None:
            tasks = [
                DownloadTask(
                    candidate.install_file.url,
                    current.stage_path(relative_destination),
                    expected_size=candidate.install_file.size,
                    expected_hash=candidate.install_file.file_hash,
                    expected_hash_algorithm=candidate.install_file.hash_algorithm,
                    task_id=candidate.version_id or candidate.project_id,
                )
                for candidate, relative_destination in zip(
                    pending,
                    relative_destinations,
                    strict=True,
                )
            ]
            result = AsyncDownloader(max_workers=min(4, len(tasks))).download_files(
                tasks,
                skip_existing=False,
            )
            if result.get("success") != len(tasks):
                details = "; ".join(
                    task.error for task in tasks if task.error
                ) or "; ".join(str(error) for error in result.get("errors", []))
                raise RuntimeError(
                    details or "Failed to stage the Modrinth dependency plan"
                )

            self._content.write_modrinth_content_batch(
                version,
                records,
                removed_paths=stale_paths,
                output_path=current.stage_path(metadata_relative),
            )

        transaction.execute(stage)
        return destinations

    @staticmethod
    def _backup_storage_requests(
        target_dir: Path,
        candidates: Sequence[ModrinthInstallCandidate],
    ) -> list[StorageRequest]:
        requests: list[StorageRequest] = []
        for candidate in candidates:
            installed_item = candidate.installed_item or {}
            source_value = str(installed_item.get("path") or "").strip()
            filename = str(installed_item.get("filename") or "").strip()
            if not source_value or not filename or Path(filename).name != filename:
                continue
            source = Path(source_value)
            try:
                size = source.stat().st_size if source.is_file() else 0
            except OSError:
                size = 0
            requests.append(
                StorageRequest(
                    target=target_dir / ".backups" / f"{filename}.backup",
                    required_bytes=max(0, size),
                    label="modrinth replacement backup",
                )
            )
        return requests

    def _authorized_stale_path(
        self,
        candidate: ModrinthInstallCandidate,
        target_dir: Path,
        destination: Path,
    ) -> Path | None:
        installed_item = candidate.installed_item
        if candidate.action != "replace" or installed_item is None:
            return None
        installed_match = candidate.installed_match
        if installed_match is None:
            installed_match = self._modrinth_mods.match_installed(
                [installed_item],
                candidate.project,
            )
        if not installed_match.can_replace:
            return None
        old_value = str((installed_item or {}).get("path") or "").strip()
        if not old_value:
            return None
        old_path = Path(old_value)
        if not old_path.is_absolute():
            return None
        try:
            old_path = old_path.resolve()
        except OSError:
            return None
        if old_path == destination or not old_path.is_file():
            return None
        if not _is_path_inside(old_path, target_dir):
            return None
        return old_path


def _safe_destination(target_dir: Path, filename: str) -> Path:
    root = target_dir.resolve()
    destination = (root / filename).resolve()
    if destination == root or not _is_path_inside(destination, root):
        raise ValueError(f"Unsafe Modrinth filename: {filename}")
    return destination


def _is_path_inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except (OSError, ValueError):
        return False
    return True


__all__ = [
    "MODRINTH_CONTENT_JOURNAL",
    "ModrinthContentBackupError",
    "ModrinthContentGameRunning",
    "ModrinthContentInstallBusy",
    "ModrinthContentInstaller",
]
