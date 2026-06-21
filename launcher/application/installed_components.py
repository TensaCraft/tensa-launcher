from __future__ import annotations

import json
import importlib
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

from launcher.core.integrity import IntegrityChecker
from launcher.models.logger import Logger


GameVersionsProvider = Callable[[], Iterable[Any]]


def _get_launcher_loader(loader_id: str) -> Any:
    launcher_class = importlib.import_module("launcher.core.launcher").Launcher
    return launcher_class.get_loader(loader_id)


@dataclass(frozen=True)
class InstalledComponent:
    version_id: str
    kind: str
    loader_name: str
    minecraft_version: str | None
    loader_version: str | None
    inherits_from: str | None
    path: Path
    size_bytes: int
    modified_at: float | None
    used_by: tuple[str, ...]
    dependent_components: tuple[str, ...]

    @property
    def is_loader(self) -> bool:
        return self.kind in {"fabric", "forge", "neoforge", "quilt"}

    @property
    def is_used(self) -> bool:
        return bool(self.used_by or self.dependent_components)


class InstalledComponentsService:
    """Manage technical Minecraft components under ``minecraft/versions``.

    This service intentionally does not create launcher game profiles. It only
    installs, repairs, lists, and deletes the low-level version components used
    by profiles.
    """

    LOADER_NAMES = {
        "minecraft": "Minecraft",
        "fabric": "Fabric",
        "forge": "Forge",
        "neoforge": "NeoForge",
        "quilt": "Quilt",
        "unknown": "Unknown",
    }

    def __init__(
        self,
        minecraft_dir: str | Path,
        *,
        games_dir: str | Path | None = None,
        versions_provider: GameVersionsProvider | None = None,
    ) -> None:
        self.minecraft_dir = Path(minecraft_dir)
        self.versions_dir = self.minecraft_dir / "versions"
        self.games_dir = Path(games_dir) if games_dir else self.minecraft_dir / "games"
        self._versions_provider = versions_provider or (lambda: [])
        self.integrity = IntegrityChecker(self.minecraft_dir)

    def list_installed(self) -> list[InstalledComponent]:
        manifests = list(self._installed_manifests())
        direct_usage = self._profile_usage()
        dependent_components = self._dependent_components(manifests)

        components = [
            self._build_component(
                version_dir=version_dir,
                manifest=manifest,
                used_by=direct_usage.get(version_id, ()),
                dependent_components=dependent_components.get(version_id, ()),
            )
            for version_id, version_dir, manifest in manifests
        ]
        return sorted(components, key=self._component_sort_key)

    def delete_component(self, version_id: str) -> None:
        target = self._component_dir(version_id)
        if not target.exists():
            return
        shutil.rmtree(target)
        self._clear_installed_cache()

    def verify_component(self, component: InstalledComponent) -> dict[str, Any]:
        return self.integrity.check_version(component.version_id, component.minecraft_version)

    def reinstall_component(self, component: InstalledComponent, operation: Any | None = None) -> InstalledComponent:
        return self.install_component(
            component.kind,
            component.minecraft_version or component.version_id,
            loader_version=component.loader_version,
            operation=operation,
            force_check=True,
        )

    def install_component(
        self,
        loader_id: object,
        minecraft_version: str,
        *,
        loader_version: str | None = None,
        operation: Any | None = None,
        force_check: bool = True,
    ) -> InstalledComponent:
        loader_id = self._normalize_loader_id(loader_id)
        minecraft_version = str(minecraft_version or "").strip()
        if not minecraft_version:
            raise ValueError("Minecraft version is required")

        if loader_id == "minecraft":
            loader = _get_launcher_loader("minecraft")
            loader._install_minecraft_if_needed(minecraft_version, force_check=force_check, operation=operation)
            installed_version_id = minecraft_version
        elif loader_id in {"fabric", "forge", "neoforge", "quilt"}:
            loader = _get_launcher_loader(loader_id)
            installed_version_id, _actual_loader_version = loader._install_mod_loader(
                minecraft_version,
                loader_id,
                requested_loader_version=loader_version,
                force_check=force_check,
                operation=operation,
            )
        else:
            raise ValueError(f"Unsupported component loader: {loader_id}")

        self._clear_installed_cache()
        component = self.get_component(installed_version_id)
        if component is None:
            raise FileNotFoundError(f"Installed component not found: {installed_version_id}")
        return component

    def install_game_build(self, version: Any, *, operation: Any | None = None) -> Any:
        """Install the technical component and persist a launcher game build.

        ``Version`` instances are user-facing game builds. Their low-level
        Minecraft/loader artifacts live under ``minecraft/versions`` and are
        managed by this service, so creation and repair use the same install
        path.
        """
        loader_id = self._normalize_loader_id(getattr(version, "client", None) or getattr(version, "loader", None))
        minecraft_version = str(getattr(version, "version", "") or "").strip()
        loader_version = getattr(version, "loader_version", None)
        component = self.install_component(
            loader_id,
            minecraft_version,
            loader_version=loader_version,
            operation=operation,
        )
        version.path = str(self.game_path(getattr(version, "version_id", "") or getattr(version, "id", "")))
        version.loader = component.version_id
        version.client = component.loader_name
        version.loader_version = component.loader_version
        self._apply_runtime_path(version, component, operation=operation)
        version.save()
        return version

    def game_path(self, version_id: str) -> Path:
        game_id = str(version_id or "").strip()
        if not game_id:
            raise ValueError("Game version id is required")
        path = self.games_dir / game_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def get_component(self, version_id: str) -> InstalledComponent | None:
        for component in self.list_installed():
            if component.version_id == version_id:
                return component
        return None

    @classmethod
    def expected_component_id(
        cls,
        loader_id: object,
        minecraft_version: str,
        loader_version: str | None = None,
    ) -> str | None:
        loader_id = cls._normalize_loader_id(loader_id)
        minecraft_version = str(minecraft_version or "").strip()
        loader_version = str(loader_version or "").strip()
        if not minecraft_version:
            return None
        if loader_id == "minecraft":
            return minecraft_version
        if not loader_version:
            return None
        if loader_id == "neoforge":
            return f"neoforge-{loader_version}"
        if loader_id == "forge":
            return f"{minecraft_version}-forge-{loader_version}"
        if loader_id in {"fabric", "quilt"}:
            return f"{loader_id}-loader-{loader_version}-{minecraft_version}"
        return None

    def _installed_manifests(self) -> Iterable[tuple[str, Path, dict[str, Any]]]:
        if not self.versions_dir.exists():
            return []

        manifests: list[tuple[str, Path, dict[str, Any]]] = []
        for version_dir in self.versions_dir.iterdir():
            if not version_dir.is_dir():
                continue
            manifest_path = version_dir / f"{version_dir.name}.json"
            if not manifest_path.exists():
                continue
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                Logger.warning(f"Skipping unreadable Minecraft version manifest {manifest_path}: {exc}")
                continue
            version_id = str(manifest.get("id") or version_dir.name).strip() or version_dir.name
            manifests.append((version_id, version_dir, manifest))
        return manifests

    def _build_component(
        self,
        *,
        version_dir: Path,
        manifest: dict[str, Any],
        used_by: Iterable[str],
        dependent_components: Iterable[str],
    ) -> InstalledComponent:
        version_id = str(manifest.get("id") or version_dir.name)
        kind, minecraft_version, loader_version = self._classify_manifest(version_id, manifest)
        return InstalledComponent(
            version_id=version_id,
            kind=kind,
            loader_name=self.LOADER_NAMES.get(kind, self.LOADER_NAMES["unknown"]),
            minecraft_version=minecraft_version,
            loader_version=loader_version,
            inherits_from=self._manifest_inherits_from(manifest),
            path=version_dir,
            size_bytes=self._directory_size(version_dir),
            modified_at=self._modified_at(version_dir),
            used_by=tuple(sorted({name for name in used_by if name})),
            dependent_components=tuple(sorted({name for name in dependent_components if name})),
        )

    def _classify_manifest(self, version_id: str, manifest: dict[str, Any]) -> tuple[str, str | None, str | None]:
        lower_id = version_id.lower()
        inherits_from = self._manifest_inherits_from(manifest)

        if lower_id.startswith("fabric-loader-"):
            return self._classify_prefixed_loader(version_id, "fabric", "fabric-loader-")

        if lower_id.startswith("quilt-loader-"):
            return self._classify_prefixed_loader(version_id, "quilt", "quilt-loader-")

        if lower_id.startswith("neoforge-"):
            loader_version = version_id[len("neoforge-") :]
            return "neoforge", self._manifest_mc_version(manifest) or inherits_from, loader_version

        if "-forge-" in lower_id:
            mc_version, loader_version = version_id.split("-forge-", 1)
            return "forge", mc_version or inherits_from, loader_version or None

        if not inherits_from:
            return "minecraft", version_id, None

        return "unknown", self._manifest_mc_version(manifest) or inherits_from, None

    def _classify_prefixed_loader(self, version_id: str, kind: str, prefix: str) -> tuple[str, str | None, str | None]:
        raw = version_id[len(prefix) :]
        minecraft_version = self._extract_minecraft_version_from_loader_id(raw)
        loader_version = raw
        if minecraft_version and raw.endswith(f"-{minecraft_version}"):
            loader_version = raw[: -(len(minecraft_version) + 1)]
        return kind, minecraft_version, loader_version or None

    @staticmethod
    def _extract_minecraft_version_from_loader_id(value: str) -> str | None:
        parts = [part for part in value.split("-") if part]
        for index in range(len(parts)):
            candidate = "-".join(parts[index:])
            if candidate.startswith("1.") or candidate[0:2].isdigit():
                return candidate
        return None

    @staticmethod
    def _manifest_inherits_from(manifest: dict[str, Any]) -> str | None:
        value = manifest.get("inheritsFrom")
        return str(value).strip() if value else None

    def _manifest_mc_version(self, manifest: dict[str, Any]) -> str | None:
        game_args = manifest.get("arguments", {}).get("game", [])
        if not isinstance(game_args, list):
            return None
        for index, value in enumerate(game_args):
            if value == "--fml.mcVersion" and index + 1 < len(game_args):
                mc_version = str(game_args[index + 1]).strip()
                return mc_version or None
        return None

    def _profile_usage(self) -> dict[str, tuple[str, ...]]:
        usage: dict[str, set[str]] = {}
        for version in self._safe_game_versions():
            name = str(getattr(version, "name", "") or "").strip()
            if not name:
                continue
            for field_name in ("loader", "version"):
                component_id = str(getattr(version, field_name, "") or "").strip()
                if component_id:
                    usage.setdefault(component_id, set()).add(name)
        return {version_id: tuple(sorted(names)) for version_id, names in usage.items()}

    def _dependent_components(self, manifests: list[tuple[str, Path, dict[str, Any]]]) -> dict[str, tuple[str, ...]]:
        dependents: dict[str, set[str]] = {}
        for version_id, _version_dir, manifest in manifests:
            kind, minecraft_version, _loader_version = self._classify_manifest(version_id, manifest)
            if kind not in {"fabric", "forge", "neoforge", "quilt"} or not minecraft_version:
                continue
            dependents.setdefault(minecraft_version, set()).add(version_id)
        return {version_id: tuple(sorted(values)) for version_id, values in dependents.items()}

    def _safe_game_versions(self) -> Iterable[Any]:
        try:
            return list(self._versions_provider() or [])
        except Exception as exc:
            Logger.warning(f"Unable to read launcher versions for component usage: {exc}")
            return []

    def _component_dir(self, version_id: str) -> Path:
        clean_id = str(version_id or "").strip()
        if not clean_id or clean_id in {".", ".."}:
            raise ValueError("Invalid component id")

        root = self.versions_dir.resolve()
        target = (root / clean_id).resolve()
        if target == root or root not in target.parents:
            raise ValueError(f"Component path escapes versions directory: {version_id}")
        return target

    @staticmethod
    def _directory_size(path: Path) -> int:
        total = 0
        try:
            for item in path.rglob("*"):
                if item.is_file():
                    total += item.stat().st_size
        except OSError:
            return total
        return total

    @staticmethod
    def _modified_at(path: Path) -> float | None:
        try:
            return max((item.stat().st_mtime for item in path.rglob("*")), default=path.stat().st_mtime)
        except OSError:
            return None

    @staticmethod
    def _component_sort_key(component: InstalledComponent) -> tuple[int, str]:
        order = {"minecraft": 0, "neoforge": 1, "forge": 2, "fabric": 3, "quilt": 4, "unknown": 9}
        return order.get(component.kind, 9), component.version_id.lower()

    @staticmethod
    def _normalize_loader_id(loader_id: object) -> str:
        value = str(loader_id or "").strip().lower()
        names = {
            "": "minecraft",
            "vanilla": "minecraft",
            "minecraft": "minecraft",
            "fabric": "fabric",
            "forge": "forge",
            "neoforge": "neoforge",
            "neo forge": "neoforge",
            "quilt": "quilt",
        }
        return names.get(value, value)

    @staticmethod
    def _apply_runtime_path(version: Any, component: InstalledComponent, *, operation: Any | None = None) -> None:
        if component.kind == "minecraft":
            return
        try:
            loader = _get_launcher_loader(component.kind)
            java_path = loader._get_version_java_path(component.minecraft_version or version.version, operation=operation)
        except Exception as exc:
            Logger.warning(f"Unable to resolve Java runtime path for {component.version_id}: {exc}")
            return
        if java_path:
            options = getattr(version, "options", None)
            if not isinstance(options, dict):
                options = {}
                version.options = options
            options["executablePath"] = java_path

    @staticmethod
    def _clear_installed_cache() -> None:
        IntegrityChecker._installed_cache = {"timestamp": 0.0, "versions": set()}


__all__ = ["InstalledComponent", "InstalledComponentsService"]
