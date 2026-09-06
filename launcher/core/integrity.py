"""
Модуль перевірки цілісності Minecraft компонентів.

Забезпечує перевірку та автоматичне відновлення:
- Бібліотек (.jar файли)
- Java runtime
- Assets
- Версій та лоадерів
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, cast

import minecraft_launcher_lib._helper as minecraft_launcher_helper

from launcher.application.java_runtime import JavaRuntimeService
from launcher.core.minecraft_install import install_minecraft_version_with_retries
from launcher.models.logger import Logger


class IntegrityError(Exception):
    """Помилка цілісності компонента."""
    pass


class IntegrityChecker:
    """Перевіряє цілісність Minecraft компонентів."""

    def __init__(self, minecraft_dir: Path):
        self.minecraft_dir = Path(minecraft_dir)
        self.runtime = JavaRuntimeService(self.minecraft_dir, Logger)

    # ================================================================
    # Основні методи перевірки
    # ================================================================

    def check_version(
        self,
        version_id: str,
        mc_version: Optional[str] = None,
        *,
        check_java: bool = True,
    ) -> Dict[str, Any]:
        """
        Комплексна перевірка версії.

        Args:
            version_id: ID версії (loader ID, напр. "fabric-loader-0.17.3-1.21.10")
            mc_version: Minecraft версія (напр. "1.21.10"). Якщо None, буде витягнута з version_id
            check_java: Перевіряти launcher-managed Java runtime. Для pre-loader перевірки
                базової Minecraft версії вимикається, бо Java встановлюється окремим кроком.

        Returns:
            Dict з результатами перевірки:
            {
                'valid': bool,
                'issues': List[str],
                'components': {
                    'manifest': bool,
                    'jar': bool,
                    'libraries': bool,
                    'natives': bool,
                    'java': bool
                }
            }
        """
        Logger.info(f"Checking integrity of version: {version_id}")

        result = {
            'valid': True,
            'issues': [],
            'components': {}
        }

        # Спочатку перевіряємо чи версія взагалі встановлена
        if not self._is_version_installed(version_id):
            Logger.warning(f"Version {version_id} is not installed")
            result['valid'] = False
            result['issues'].append(f"Version not installed: {version_id}")
            result['components'] = {
                'manifest': False,
                'jar': False,
                'libraries': False,
                'natives': False,
                'java': False
            }
            return result

        # Перевірка JSON маніфесту
        manifest_valid = self._check_version_manifest(version_id)
        result['components']['manifest'] = manifest_valid
        if not manifest_valid:
            result['valid'] = False
            result['issues'].append(f"Version manifest missing or corrupted: {version_id}")

        # Перевірка JAR файлу
        jar_valid = self._check_version_jar(version_id)
        result['components']['jar'] = jar_valid
        if not jar_valid:
            result['valid'] = False
            result['issues'].append(f"Version JAR missing or corrupted: {version_id}")

        # Перевірка бібліотек (тільки якщо маніфест валідний)
        if manifest_valid:
            libraries_valid = self._check_libraries(version_id)
            result['components']['libraries'] = libraries_valid
            if not libraries_valid:
                result['valid'] = False
                result['issues'].append("Some libraries are missing or corrupted")
        else:
            result['components']['libraries'] = False

        # Перевірка natives (опціонально)
        natives_valid = self._check_natives(version_id)
        result['components']['natives'] = natives_valid
        # Natives не критичні для деяких версій

        # Перевірка Java runtime (передаємо MC версію)
        java_valid = self._check_java_runtime(version_id, mc_version) if check_java else True
        result['components']['java'] = java_valid
        if not java_valid:
            result['valid'] = False
            result['issues'].append("Java runtime missing or corrupted")

        if result['valid']:
            Logger.info(f"Version {version_id} integrity check passed")
        else:
            Logger.warning(f"Version {version_id} has integrity issues: {result['issues']}")

        return result

    def quick_check_version(
        self,
        version_id: str,
        mc_version: Optional[str] = None,
        assume_installed: bool = False,
    ) -> bool:
        """Швидка перевірка без повної перевірки бібліотек."""
        if not assume_installed and not self._is_version_installed(version_id):
            return False
        if not self._check_version_manifest(version_id):
            return False
        if not self._check_version_jar(version_id):
            return False
        if not self._check_java_runtime(version_id, mc_version):
            return False
        return True

    # ================================================================
    # Перевірка компонентів
    # ================================================================

    def _is_version_installed(self, version_id: str) -> bool:
        """Read only the requested manifest; component verification remains separate."""
        if not version_id or version_id in {".", ".."} or any(char in version_id for char in "/\\:\0"):
            return False

        version_json = self.minecraft_dir / "versions" / version_id / f"{version_id}.json"
        try:
            with open(version_json, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError, UnicodeError) as e:
            Logger.debug(f"Version manifest unavailable for {version_id}: {e}")
            return False

        return isinstance(data, dict) and data.get('id') == version_id

    def _load_version_manifest(self, version_id: str) -> Dict[str, Any]:
        """Validate local parents, then use the same metadata merge as MLL launch."""
        data = None
        current_id = version_id
        seen = set()
        try:
            while True:
                if (
                    not isinstance(current_id, str) or not current_id or current_id in {".", ".."}
                    or any(char in current_id for char in "/\\:\0") or current_id in seen
                ):
                    raise ValueError(f"Invalid or cyclic version inheritance: {current_id!r}")
                seen.add(current_id)
                path = self.minecraft_dir / "versions" / current_id / f"{current_id}.json"
                with open(path, 'r', encoding='utf-8') as f:
                    current = json.load(f)
                if not isinstance(current, dict) or current.get('id') != current_id:
                    raise ValueError(f"Invalid version manifest: {path}")
                if data is None:
                    data = current
                if 'inheritsFrom' not in current:
                    break
                current_id = current['inheritsFrom']

            # MLL 8.0 merges one parent at launch; do not invent different merge semantics.
            # Avoid get_client_json(), which may fetch remote metadata for missing files.
            if 'inheritsFrom' in data:
                return dict(minecraft_launcher_helper.inherit_json(cast(Any, data), self.minecraft_dir))
            return data
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as e:
            raise IntegrityError(f"Failed to load local metadata for {version_id}: {e}") from e

    def _check_version_manifest(self, version_id: str) -> bool:
        """Перевіряє наявність та валідність JSON маніфесту версії."""
        try:
            data = self._load_version_manifest(version_id)

            # Перевірка обов'язкових полів
            required_fields = ['id', 'type', 'mainClass', 'libraries']
            for field in required_fields:
                if field not in data:
                    Logger.debug(f"Missing required field in manifest: {field}")
                    return False

            return True
        except IntegrityError as e:
            Logger.debug(f"Failed to read version manifest: {e}")
            return False

    def _check_version_jar(self, version_id: str) -> bool:
        """Перевіряє наявність JAR файлу версії."""
        try:
            data = self._load_version_manifest(version_id)
            jar_id = data.get('jar', version_id)
            if (
                not isinstance(jar_id, str) or not jar_id or jar_id in {".", ".."}
                or any(char in jar_id for char in "/\\:\0")
            ):
                raise IntegrityError(f"Invalid JAR version id: {jar_id!r}")
            version_jar = self.minecraft_dir / "versions" / jar_id / f"{jar_id}.jar"
            if not version_jar.is_file() or version_jar.stat().st_size == 0:
                Logger.debug(f"Version JAR missing or empty: {version_jar}")
                return False
            return True
        except (IntegrityError, OSError) as e:
            Logger.debug(f"Failed to check version JAR: {e}")
            return False

    def _check_libraries(self, version_id: str) -> bool:
        """Перевіряє наявність всіх бібліотек."""
        try:
            data = self._load_version_manifest(version_id)

            libraries = data.get('libraries', [])
            missing_libraries = []

            for lib in libraries:
                # Пропускаємо бібліотеки з правилами які не застосовуються
                if not self._should_use_library(lib):
                    continue

                lib_path = self._get_library_path(lib)
                if lib_path and not lib_path.exists():
                    missing_libraries.append(str(lib_path.relative_to(self.minecraft_dir)))

            if missing_libraries:
                sample = ", ".join(missing_libraries[:3])
                suffix = "" if len(missing_libraries) <= 3 else f", +{len(missing_libraries) - 3} more"
                Logger.warning(f"Missing libraries for {version_id}: {sample}{suffix}")
                return False

            return True
        except Exception as e:
            Logger.debug(f"Failed to check libraries: {e}")
            return False

    def _check_natives(self, version_id: str) -> bool:
        """Перевіряє наявність native бібліотек."""
        natives_dir = self.minecraft_dir / "versions" / version_id / "natives"

        # Natives можуть бути відсутні для деяких версій
        if not natives_dir.exists():
            Logger.debug(f"Natives directory not found: {natives_dir}")
            return True  # Не критична помилка

        # Перевірка що директорія не порожня
        if not any(natives_dir.iterdir()):
            Logger.debug(f"Natives directory is empty: {natives_dir}")
            return False

        return True

    def _check_java_runtime(self, version_id: str, mc_version: Optional[str] = None) -> bool:
        """
        Перевіряє наявність та працездатність Java runtime.

        Args:
            version_id: ID версії
            mc_version: Minecraft версія. Якщо None, буде витягнута з version_id
        """
        if mc_version is None:
            mc_version = self.runtime.extract_minecraft_version(version_id)

        if not mc_version:
            Logger.debug(f"Skipping Java runtime check for {version_id} (no MC version)")
            return True

        return self.runtime.has_runtime(version_id, mc_version)

    # ================================================================
    # Методи відновлення
    # ================================================================

    def repair_version(
        self,
        version_id: str,
        callback: Optional[Any] = None,
        force_reinstall: bool = False
    ) -> bool:
        """
        Відновлює пошкоджену версію.

        Args:
            version_id: ID версії для відновлення
            callback: Callback для прогресу
            force_reinstall: Примусове повне перевстановлення

        Returns:
            bool: True якщо відновлення успішне
        """
        Logger.info(f"Repairing version: {version_id}")

        try:
            if force_reinstall:
                Logger.info(f"Force reinstalling version: {version_id}")

            # minecraft_launcher_lib автоматично перевстановить відсутні компоненти
            install_minecraft_version_with_retries(
                version_id,
                self.minecraft_dir,
                callback=callback,
            )

            # Перевірка після відновлення
            result = self.check_version(version_id)

            if result['valid']:
                Logger.info(f"Version {version_id} successfully repaired")
                return True
            else:
                Logger.error(f"Failed to repair version {version_id}: {result['issues']}")
                return False

        except Exception as e:
            Logger.error(f"Error repairing version {version_id}: {e}")
            return False

    def repair_java_runtime(self, version_id: str, callback: Optional[Any] = None) -> bool:
        """
        Відновлює Java runtime для версії.

        Returns:
            bool: True якщо відновлення успішне
        """
        mc_version = self.runtime.extract_minecraft_version(version_id)
        Logger.info(f"Repairing Java runtime for: {mc_version}")

        try:
            runtime_name = self.runtime.get_runtime_name(mc_version)
            if not runtime_name:
                Logger.debug(f"No runtime required for version: {mc_version}")
                return True

            if self.runtime.install_runtime(version_id, mc_version, callback=callback):
                Logger.info(f"Java runtime {runtime_name} successfully repaired")
                return True

            Logger.error(f"Failed to repair Java runtime {runtime_name}")
            return False
        except Exception as e:
            Logger.error(f"Error repairing Java runtime: {e}")
            return False

    # ================================================================
    # Допоміжні методи
    # ================================================================

    def _should_use_library(self, lib: Dict) -> bool:
        """Перевіряє чи повинна використовуватись бібліотека на основі rules."""
        rules = lib.get('rules', [])
        if not rules:
            return True

        try:
            return bool(minecraft_launcher_helper.parse_rule_list(rules, {}))
        except Exception as e:
            Logger.warning(f"Could not parse library rules for {lib.get('name', '<unknown>')}: {e}")
            return True

    def _get_library_path(self, lib: Dict) -> Optional[Path]:
        """Отримує шлях до бібліотеки."""
        downloads = lib.get('downloads', {})
        artifact = downloads.get('artifact', {})
        path = artifact.get('path')

        if not path:
            if lib.get('name'):
                return Path(minecraft_launcher_helper.get_library_path(lib['name'], self.minecraft_dir))
            return None

        return self.minecraft_dir / "libraries" / path

    def get_all_installed_versions(self) -> List[str]:
        """Повертає список всіх встановлених версій."""
        versions_dir = self.minecraft_dir / "versions"
        if not versions_dir.exists():
            return []

        versions = []
        for version_dir in versions_dir.iterdir():
            if version_dir.is_dir():
                version_json = version_dir / f"{version_dir.name}.json"
                if version_json.exists():
                    versions.append(version_dir.name)

        return versions


__all__ = ["IntegrityChecker", "IntegrityError"]
