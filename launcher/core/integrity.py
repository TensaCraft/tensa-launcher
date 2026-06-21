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
import time
from pathlib import Path
from typing import Optional, Dict, List

import minecraft_launcher_lib
import minecraft_launcher_lib._helper as minecraft_launcher_helper

from launcher.application.java_runtime import JavaRuntimeService
from launcher.core.minecraft_install import install_minecraft_version_with_retries
from launcher.models.logger import Logger


class IntegrityError(Exception):
    """Помилка цілісності компонента."""
    pass


class IntegrityChecker:
    """Перевіряє цілісність Minecraft компонентів."""

    _installed_cache = {"timestamp": 0.0, "versions": set()}
    # Scanning the versions directory can be expensive on Windows (AV + large installs).
    # Cache longer; we don't expect external installs/uninstalls during a launcher session.
    _installed_cache_ttl = 300.0

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
    ) -> Dict[str, any]:
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
                result['issues'].append(f"Some libraries are missing or corrupted")
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
            result['issues'].append(f"Java runtime missing or corrupted")

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
        """Перевіряє чи версія встановлена використовуючи minecraft_launcher_lib."""
        try:
            now = time.time()
            cache = self._installed_cache
            if (now - cache["timestamp"]) > self._installed_cache_ttl:
                installed_versions = minecraft_launcher_lib.utils.get_installed_versions(
                    str(self.minecraft_dir)
                )
                cache["versions"] = {
                    version.get('id')
                    for version in installed_versions
                    if version.get('id')
                }
                cache["timestamp"] = now

            if version_id in cache["versions"]:
                return True

            Logger.debug(f"Version {version_id} not found in installed versions")
            return False
        except Exception as e:
            Logger.error(f"Failed to get installed versions: {e}")
            # Fallback на перевірку файлів
            version_dir = self.minecraft_dir / "versions" / version_id
            return version_dir.exists()

    def _check_version_manifest(self, version_id: str) -> bool:
        """Перевіряє наявність та валідність JSON маніфесту версії."""
        version_json = self.minecraft_dir / "versions" / version_id / f"{version_id}.json"

        if not version_json.exists():
            Logger.debug(f"Version manifest not found: {version_json}")
            return False

        try:
            with open(version_json, 'r', encoding='utf-8') as f:
                data = json.load(f)

            # Перевірка обов'язкових полів
            required_fields = ['id', 'type', 'mainClass', 'libraries']
            for field in required_fields:
                if field not in data:
                    Logger.debug(f"Missing required field in manifest: {field}")
                    return False

            return True
        except (json.JSONDecodeError, IOError) as e:
            Logger.debug(f"Failed to read version manifest: {e}")
            return False

    def _check_version_jar(self, version_id: str) -> bool:
        """Перевіряє наявність JAR файлу версії."""
        version_jar = self.minecraft_dir / "versions" / version_id / f"{version_id}.jar"

        if not version_jar.exists():
            Logger.debug(f"Version JAR not found: {version_jar}")
            return False

        # Перевірка що файл не порожній
        if version_jar.stat().st_size == 0:
            Logger.debug(f"Version JAR is empty: {version_jar}")
            return False

        return True

    def _check_libraries(self, version_id: str) -> bool:
        """Перевіряє наявність всіх бібліотек."""
        version_json = self.minecraft_dir / "versions" / version_id / f"{version_id}.json"

        if not version_json.exists():
            return False

        try:
            with open(version_json, 'r', encoding='utf-8') as f:
                data = json.load(f)

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
        callback: Optional[any] = None,
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

    def repair_java_runtime(self, version_id: str, callback: Optional[any] = None) -> bool:
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
