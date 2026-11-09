"""Release version source and packaging consistency checks."""
import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def current(root=ROOT):
    text = (root / 'core/src/tobili/__init__.py').read_text(encoding='utf-8')
    return re.search(r'__version__ = "([0-9]+\.[0-9]+\.[0-9]+)"', text)[1]


def validate_bundle(root=ROOT):
    version = current(root)
    manifest = json.loads((root / 'ui/dist/version.json').read_text(encoding='utf-8'))
    if manifest['version'] != version:
        raise ValueError('界面与程序版本不同，请重新构建 UI')
    return version


def windows_resource(path, name, version):
    numbers = tuple(int(x) for x in version.split('.')) + (0,)
    path.write_text(
        f"VSVersionInfo(ffi=FixedFileInfo(filevers={numbers}, prodvers={numbers}, mask=0x3f, flags=0, OS=0x40004, fileType=0x1, subtype=0, date=(0,0)), "
        f"kids=[StringFileInfo([StringTable('040904B0', [StringStruct('FileDescription', {name!r}), StringStruct('ProductName', {name!r}), StringStruct('FileVersion', {version!r}), StringStruct('ProductVersion', {version!r})])]), VarFileInfo([VarStruct('Translation', [1033,1200])])])",
        encoding='utf-8',
    )
    return path


def bump(version, root=ROOT):
    if not re.fullmatch(r'\d+\.\d+\.\d+', version):
        raise ValueError('版本格式为 major.minor.patch')
    old = current(root)
    if tuple(map(int,version.split('.'))) <= tuple(map(int,old.split('.'))):
        raise ValueError('新版本必须大于当前版本')
    p=root/'core/src/tobili/__init__.py'
    p.write_text(p.read_text(encoding='utf-8').replace(f'__version__ = "{old}"',f'__version__ = "{version}"'),encoding='utf-8')
    for name in ['pyproject.toml','core/pyproject.toml']:
        p=root/name;s=p.read_text(encoding='utf-8')
        p.write_text(re.sub(r'(?m)^version = "[^"]+"',f'version = "{version}"',s,count=1),encoding='utf-8')
    for name in ['ui/package.json','ui/package-lock.json']:
        p=root/name;v=json.loads(p.read_text(encoding='utf-8'));v['version']=version
        if 'packages' in v:v['packages']['']['version']=version
        p.write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    p=root/'uv.lock';s=p.read_text(encoding='utf-8')
    s=re.sub(r'(name = "tobili-(?:core|workspace)"\nversion = ")[^"]+',lambda m:m[1]+version,s)
    p.write_text(s,encoding='utf-8')
    print(f'{old} -> {version}')


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('version',nargs='?');args=parser.parse_args()
    bump(args.version) if args.version else print(current())
