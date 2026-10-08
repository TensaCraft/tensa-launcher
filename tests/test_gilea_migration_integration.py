import asyncio
import hashlib
import json
import sys
import threading
from contextlib import nullcontext
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet

from launcher.application.gilea_migration import release, service, transaction
from launcher.application.gilea_migration.files import fingerprint
from launcher.application.gilea_migration.models import MigrationError, MigrationRoots
from launcher.platform import gilea_migration as windows
from launcher.ui.gilea_migration import GileaMigrationController


@pytest.fixture
def environment(tmp_path, monkeypatch):
    roots = MigrationRoots(tmp_path / 'Tensa state', tmp_path / '\u0406\u0433\u0440\u0438 Minecraft',
                           tmp_path / 'Gilea state', tmp_path / 'Gilea app', tmp_path / 'Tensa state/gilea-migration')
    roots.source_state.mkdir()
    normal = roots.minecraft / 'games/Creative world'
    external = tmp_path / '\u0417\u0431\u0456\u0440\u043a\u0430 Survival'
    for folder in (normal, external):
        (folder / 'saves/World').mkdir(parents=True)
        (folder / 'saves/World/level.dat').write_bytes(b'synthetic-world')
        (folder / 'options.txt').write_bytes(b'fullscreen:true')
    key = Fernet.generate_key()
    cipher = Fernet(key)
    profiles = {
        'Online': {'id': 'online-uuid', 'name': 'Online', 'type': 'microsoft', 'default': True,
                   'access_token': 'enc::' + cipher.encrypt(b'synthetic-access').decode(),
                   'refresh_token': 'enc::' + cipher.encrypt(b'synthetic-refresh').decode(),
                   'expires_at': 1900000000, 'xuid': 'fixture-xuid', 'auth_client_id': 'fixture-client'},
        'Offline': {'id': 'offline-uuid', 'name': 'Offline', 'type': 'offline', 'access_token': 'offline'},
    }
    config = {'lang': 'uk_UA', 'minecraft_game_dir': str(roots.minecraft), 'auto_update': 'no',
              'ask_profile_on_launch': 'yes', 'default_max_ram_gb': 6, 'close_launcher_on_game': 'yes',
              'world_backups_enabled': 'yes', 'world_backups_keep_count': 4, 'world_backups_dir': 'backups',
              'custom_java_versions': [{'Java 21': 'java/bin/java.exe'}]}
    builds = {
        'creative_old': {'id': 'creative-id', 'name': 'Creative', 'version': '1.21.1', 'loader': 'fabric',
                         'path': str(normal), 'options': {'profileKey': 'Online',
                             'executablePath': 'java/bin/java.exe', 'jvmArguments': ['-Xmx4G', '-Dkey=value with spaces'],
                             'server': 'example.invalid'}},
        'survival_old': {'id': 'survival-id', 'name': 'Survival', 'version': '1.21.1', 'loader': 'neoforge',
                         'path': str(external), 'options': {'profileKey': 'Offline',
                             'jvmArguments': '-Xmx5G -Dkey="value with spaces"'}},
    }
    for name, data in [('config', config), ('profiles', profiles), ('versions', builds)]:
        (roots.source_state / f'{name}.json').write_text(json.dumps(data), encoding='utf-8')
    (roots.source_state / 'profile-token.key').write_bytes(key)
    binary = tmp_path / 'download.exe'
    binary.write_bytes(b'MZ-integration-fixture')
    monkeypatch.setattr(release, 'PINNED_SIZE', binary.stat().st_size)
    monkeypatch.setattr(release, 'PINNED_SHA256', hashlib.sha256(binary.read_bytes()).hexdigest())
    monkeypatch.setattr(windows, 'check_prerequisites', lambda roots: None)
    monkeypatch.setattr(windows, 'protect_directory', lambda path: path.mkdir(parents=True, exist_ok=True))
    if sys.platform != 'win32':
        monkeypatch.setattr(windows, 'read_guard', lambda paths: nullcontext())
    return roots, normal, external, profiles, builds, binary


def test_transition_fixture_contract(environment, monkeypatch):
    roots, normal, external, profiles, builds, binary = environment
    sources = [p for root in (roots.source_state, normal, external) for p in root.rglob('*') if p.is_file()]
    before = {p: (fingerprint(p), p.stat().st_mtime_ns) for p in sources}
    plan = service.prepare_plan(roots, effective_defaults={})
    assert len(plan.adapted.builds) == 2 and not plan.adapted.warnings
    result = transaction.commit_migration(plan, binary, allow_external_copy=True,
                                          cancel=threading.Event(), progress=lambda event: None)
    migrated = json.loads((roots.target_state / 'profiles.json').read_text())
    assert migrated == profiles
    key = (roots.target_state / 'profile-token.key').read_bytes()
    assert Fernet(key).decrypt(migrated['Online']['access_token'][5:].encode()) == b'synthetic-access'
    config = json.loads((roots.target_state / 'config.json').read_text())
    assert config['minecraft_game_dir'] == str(roots.minecraft)
    assert config['lang'] == 'uk_UA' and config['default_max_ram_gb'] == 6
    assert config['auto_update'] == 'no' and config['ask_profile_on_launch'] == 'yes'
    assert config['on_game_start'] == 'close' and config['setup_wizard_completed'] == 'yes'
    assert config['world_backups_dir'] == str(roots.source_state / 'backups')
    assert config['custom_java_versions'] == [{'Java 21': str(roots.source_state / 'java/bin/java.exe')}]
    for build in plan.adapted.builds:
        record = json.loads((build.target / 'version.json').read_text())
        old = builds[build.aliases[0]]
        assert record['id'] == old['id'] and record['name'] == old['name']
        assert record['options']['profileKey'] == old['options']['profileKey']
        assert record['options']['jvmArguments'] == old['options']['jvmArguments']
        assert (build.target / 'saves/World/level.dat').read_bytes() == b'synthetic-world'
    assert not (roots.target_state / 'versions.json').exists()
    assert before == {p: (fingerprint(p), p.stat().st_mtime_ns) for p in sources}
    assert transaction.commit_migration(plan, binary, allow_external_copy=True,
                                        cancel=threading.Event(), progress=lambda event: None) == result
    calls = []
    monkeypatch.setattr(windows, '_gilea_running', lambda exe: False)
    monkeypatch.setattr(windows.subprocess, 'Popen', lambda command, **kwargs: calls.append(command) or
                        SimpleNamespace(wait=lambda timeout: (_ for _ in ()).throw(
                            windows.subprocess.TimeoutExpired(command, timeout))))
    windows.launch_gilea(result, version_id='survival_old')
    assert calls == [[str(result.executable), f'--launch-version={external.name}']]
    result.executable.write_bytes(b'synthetic-updated-gilea')
    assert service.load_committed_migration(roots) == result


def test_cancel_retry_then_recover(environment):
    roots, _, _, _, _, binary = environment
    plan = service.prepare_plan(roots, effective_defaults={})
    cancel = threading.Event()

    def interrupt(event):
        if event.phase == 'activate':
            cancel.set()

    with pytest.raises(MigrationError, match='cancelled'):
        transaction.commit_migration(plan, binary, allow_external_copy=True, cancel=cancel, progress=interrupt)
    assert service.load_committed_migration(roots) is None
    plan = service.prepare_plan(roots, effective_defaults={})

    class PowerLoss(BaseException):
        pass

    def crash(event):
        if event.phase == 'activate':
            (roots.target_state / 'profiles.json').write_text('{"later_user_data":true}')
            raise PowerLoss

    with pytest.raises(PowerLoss):
        transaction.commit_migration(plan, binary, allow_external_copy=True,
                                      cancel=threading.Event(), progress=crash)
    with pytest.raises(MigrationError, match='repair_required'):
        transaction.recover_migration(roots)
    assert json.loads((roots.target_state / 'profiles.json').read_text()) == {'later_user_data': True}


def test_controller_declining_external_copy_never_starts_download(environment, monkeypatch):
    roots, _, external, _, _, _ = environment
    plan = service.prepare_plan(roots, effective_defaults={})
    questions = []
    control = SimpleNamespace(disabled=False)
    app = SimpleNamespace(config={}, _terminating=False, page=SimpleNamespace(controls=[control]),
                          feedback=SimpleNamespace(is_busy=lambda: False))

    def confirm(title, question, callback):
        questions.append(question)
        callback(len(questions) == 1)

    app.feedback.confirm = confirm
    controller = GileaMigrationController(app)
    controller.available = True
    monkeypatch.setattr(controller, '_prepare', lambda: plan)
    monkeypatch.setattr(controller, '_execute', lambda *args: pytest.fail('download started'))
    monkeypatch.setattr(controller, '_show_progress', lambda: None)
    monkeypatch.setattr(controller, '_hide_progress', lambda: None)
    monkeypatch.setattr('launcher.ui.gilea_migration.schedule_update', lambda page: None)
    asyncio.run(controller.offer())
    assert len(questions) == 2 and str(external) in questions[1]
    assert not control.disabled and not controller.busy and not roots.target_state.exists()
