import importlib.util
import json
from pathlib import Path

import pytest

spec=importlib.util.spec_from_file_location('release_version',Path(__file__).parents[2]/'tools/release_version.py')
release=importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


def test_packaging_rejects_a_stale_frontend(tmp_path):
    source=tmp_path/'core/src/tobili/__init__.py'
    source.parent.mkdir(parents=True)
    source.write_text('__version__ = "0.2.0"')
    bundle=tmp_path/'ui/dist/version.json'
    bundle.parent.mkdir(parents=True)
    bundle.write_text(json.dumps({'version':'0.1.0'}))
    with pytest.raises(ValueError,match='界面与程序版本不同'):
        release.validate_bundle(tmp_path)
    bundle.write_text(json.dumps({'version':'0.2.0'}))
    assert release.validate_bundle(tmp_path)=='0.2.0'


def test_release_bump_updates_own_packages_only(tmp_path):
    files={
        'core/src/tobili/__init__.py':'__version__ = "0.2.0"',
        'core/pyproject.toml':'version = "0.2.0"',
        'pyproject.toml':'version = "0.2.0"',
        'ui/package.json':json.dumps({'version':'0.2.0'}),
        'ui/package-lock.json':json.dumps({'version':'0.2.0','packages':{'':{'version':'0.2.0'},'node_modules/example':{'version':'0.2.0'}}}),
        'uv.lock':'name = "tobili-core"\nversion = "0.2.0"\nname = "tobili-workspace"\nversion = "0.2.0"\nname = "example"\nversion = "0.2.0"',
    }
    for name,body in files.items():
        p=tmp_path/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(body)
    release.bump('0.2.1',tmp_path)
    assert release.current(tmp_path)=='0.2.1'
    lock=json.loads((tmp_path/'ui/package-lock.json').read_text())
    assert lock['packages']['']['version']=='0.2.1'
    assert lock['packages']['node_modules/example']['version']=='0.2.0'
    assert 'name = "example"\nversion = "0.2.0"' in (tmp_path/'uv.lock').read_text()
    with pytest.raises(ValueError):release.bump('0.2.0',tmp_path)
