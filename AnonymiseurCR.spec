# -*- mode: python ; coding: utf-8 -*-
"""Build PyInstaller : produit un .exe autonome (onefile)."""
from PyInstaller.utils.hooks import collect_all, collect_submodules

# Ressources embarquees (servies en lecture seule depuis sys._MEIPASS).
datas = [('frontend', 'frontend'), ('anonymiseur.ico', '.')]
binaries = []
hiddenimports = ['multipart']

# Paquets a embarquer entierement (code + donnees + binaires natifs).
for pkg in ['fitz', 'pymupdf', 'pdfplumber', 'pdfminer',
            'uvicorn', 'starlette', 'fastapi', 'anyio']:
    try:
        d, b, h = collect_all(pkg)
        datas += d; binaries += b; hiddenimports += h
    except Exception:
        pass

hiddenimports += collect_submodules('uvicorn')

a = Analysis(
    ['app_launcher.py'],
    pathex=['backend'],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=['matplotlib', 'numpy.tests'],  # tkinter requis : selecteur de dossier natif (/api/pick-folder)
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='MedAiCR',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    icon='anonymiseur.ico',
)
