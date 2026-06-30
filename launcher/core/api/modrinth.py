from pathlib import Path

import requests


class ModrinthAPI:
    BASE_URL = 'https://api.modrinth.com/v2'
    REQUEST_TIMEOUT = (5, 20)

    @staticmethod
    def search_modpacks(query='', offset=0, limit=20):
        url = f"{ModrinthAPI.BASE_URL}/search"
        params = {
            'query': query,
            'facets': '[["project_type:modpack"]]',
            'index': 'relevance',
            'offset': offset,
            'limit': limit
        }
        response = requests.get(url, params=params, timeout=ModrinthAPI.REQUEST_TIMEOUT)
        response.raise_for_status()
        return response.json()

    @staticmethod
    def get_modpack(modpack_id):
        url = f"{ModrinthAPI.BASE_URL}/project/{modpack_id}"
        response = requests.get(url, timeout=ModrinthAPI.REQUEST_TIMEOUT)
        response.raise_for_status()
        return response.json()

    @staticmethod
    def get_latest_version(modpack_id):
        url = f"{ModrinthAPI.BASE_URL}/project/{modpack_id}/version"
        response = requests.get(url, timeout=ModrinthAPI.REQUEST_TIMEOUT)
        response.raise_for_status()
        versions = response.json()
        return versions[0] if versions else None

    @staticmethod
    def get_version(modpack_id, version_id):
        url = f"{ModrinthAPI.BASE_URL}/project/{modpack_id}/version/{version_id}"
        response = requests.get(url, timeout=ModrinthAPI.REQUEST_TIMEOUT)
        response.raise_for_status()
        return response.json()

    @staticmethod
    def get_versions(modpack_id):
        url = f"{ModrinthAPI.BASE_URL}/project/{modpack_id}/version"
        response = requests.get(url, timeout=ModrinthAPI.REQUEST_TIMEOUT)
        response.raise_for_status()
        return response.json()

    @staticmethod
    def get_version_by_id(version_id):
        url = f"{ModrinthAPI.BASE_URL}/version/{version_id}"
        response = requests.get(url, timeout=ModrinthAPI.REQUEST_TIMEOUT)
        response.raise_for_status()
        return response.json()

    @staticmethod
    def download_mrpack(version, download_path):
        if 'files' not in version:
            raise Exception("The 'version' object does not contain 'files'. Check the version structure.")
        files = version.get('files', [])
        mrpack_file = next((file for file in files if file['filename'].endswith('.mrpack')), None)
        if not mrpack_file:
            raise Exception("No .mrpack file found in the latest version.")

        download_url = mrpack_file['url']
        response = requests.get(download_url, stream=True, timeout=ModrinthAPI.REQUEST_TIMEOUT)
        response.raise_for_status()

        with open(download_path, 'wb') as file:
            for chunk in response.iter_content(chunk_size=8192):
                file.write(chunk)
        return download_path

    @staticmethod
    def search_mods(query='', facets=None, offset=0, limit=20):
        """
        Пошук модів через Modrinth API.

        Args:
            query: Пошуковий запит
            facets: Фільтри (game versions, loaders, categories)
            offset: Зсув для пагінації
            limit: Кількість результатів
        """
        url = f"{ModrinthAPI.BASE_URL}/search"
        params = {
            'query': query,
            'index': 'relevance',
            'offset': offset,
            'limit': limit
        }

        if facets:
            params['facets'] = facets

        response = requests.get(url, params=params, timeout=ModrinthAPI.REQUEST_TIMEOUT)
        response.raise_for_status()
        return response.json()

    @staticmethod
    def get_mod(mod_id):
        """Отримати інформацію про мод."""
        url = f"{ModrinthAPI.BASE_URL}/project/{mod_id}"
        response = requests.get(url, timeout=ModrinthAPI.REQUEST_TIMEOUT)
        response.raise_for_status()
        return response.json()

    @staticmethod
    def get_mod_versions(mod_id, game_versions=None, loaders=None):
        """
        Отримати версії мода.

        Args:
            mod_id: ID мода
            game_versions: Список версій Minecraft (напр. ["1.20.1"])
            loaders: Список лоадерів (напр. ["fabric", "forge"])
        """
        url = f"{ModrinthAPI.BASE_URL}/project/{mod_id}/version"
        params = {}

        # Modrinth API очікує JSON масиви для фільтрації
        if game_versions:
            import json
            params['game_versions'] = json.dumps(game_versions)
        if loaders:
            import json
            params['loaders'] = json.dumps(loaders)

        response = requests.get(url, params=params, timeout=ModrinthAPI.REQUEST_TIMEOUT)
        response.raise_for_status()
        return response.json()

    @staticmethod
    def download_mod_file(file_url, download_path, *, progress_callback=None):
        """Завантажити файл мода."""
        response = requests.get(file_url, stream=True, timeout=ModrinthAPI.REQUEST_TIMEOUT)
        response.raise_for_status()

        total = _safe_int(response.headers.get("content-length"))
        completed = 0
        filename = Path(download_path).name
        if progress_callback is not None:
            progress_callback(completed, total, filename)

        with open(download_path, 'wb') as file:
            for chunk in response.iter_content(chunk_size=8192):
                if not chunk:
                    continue
                file.write(chunk)
                completed += len(chunk)
                if progress_callback is not None:
                    progress_callback(completed, total, filename)
        return download_path


def _safe_int(value):
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0
