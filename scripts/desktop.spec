from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules, collect_data_files

root = Path(SPECPATH).parent
upstream = root / 'upstream'
datas = [(str(upstream / 'web/static/dist'), 'web/static/dist'),
         (str(upstream / 'web/static/favicon.svg'), 'web/static'),
         (str(upstream / 'web/article_emotes.json'), 'web'),
         (str(upstream / 'web/static/article-emotes'), 'web/static/article-emotes'),
         (str(upstream / 'config/sources.yaml'), 'config'),
         (str(upstream / 'config/cookies.txt.example'), 'config'),
         (str(upstream / 'config/llm.env.example'), 'config')]
datas += collect_data_files('webview')
hidden = collect_submodules('src') + collect_submodules('web') + collect_submodules('uvicorn')
a = Analysis([str(root / 'desktop.py')], pathex=[str(upstream), str(root)],
             datas=datas, hiddenimports=hidden, excludes=['PyQt5', 'PyQt6', 'PySide2', 'PySide6', 'tkinter'])
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='toBili',
          console=False, icon=str(upstream / 'packaging/windows/binggo.ico'))
coll = COLLECT(exe, a.binaries, a.datas, name='BinggoDesktop')
