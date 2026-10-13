# -*- mode: python ; coding: utf-8 -*-
# installer/hebrew-maps.spec
#
# PyInstaller spec for Hebrew Maps (--onedir mode).
#
# Run from project root:
#   pyinstaller installer\hebrew-maps.spec --distpath installer\dist --workpath installer\build

import os
from pathlib import Path

# SPECPATH is set by PyInstaller to the directory containing this .spec file,
# which is <project-root>\installer\.  One level up is the project root.
ROOT = Path(SPECPATH).parent

a = Analysis(
    [str(ROOT / 'hmaps' / '__main__.py')],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[
        # openapi.json is loaded via FileResponse(Path(__file__).with_name("openapi.json"))
        # — it must land at hmaps/api/ inside the bundle.
        (str(ROOT / 'hmaps' / 'api' / 'openapi.json'), 'hmaps/api'),
        # tzdata ships as a data-only package; PyInstaller misses the zoneinfo files.
        # Use importlib to find the installed path dynamically (works on any venv layout).
        *([(__import__('importlib.util', fromlist=['util']).util.find_spec('tzdata').submodule_search_locations[0], 'tzdata')]
          if __import__('importlib.util', fromlist=['util']).util.find_spec('tzdata') else []),
        # web/ assets are NOT bundled here — Inno Setup copies them alongside the EXE.
        # They are listed in the [Files] section of hebrew-maps.iss instead.
    ],
    hiddenimports=[
        # uvicorn uses importlib to load these at startup
        'uvicorn',
        'uvicorn.logging',
        'uvicorn.loops',
        'uvicorn.loops.auto',
        'uvicorn.loops.asyncio',
        'uvicorn.protocols',
        'uvicorn.protocols.http',
        'uvicorn.protocols.http.auto',
        'uvicorn.protocols.http.h11_impl',
        'uvicorn.protocols.websockets',
        'uvicorn.protocols.websockets.auto',
        'uvicorn.lifespan',
        'uvicorn.lifespan.on',
        # starlette dynamic imports
        'starlette',
        'starlette.middleware.gzip',
        'starlette.staticfiles',
        # anyio backend
        'anyio',
        'anyio._backends._asyncio',
        # HTTP parser used by uvicorn
        'h11',
        # optional uvicorn speedups (not fatal if absent)
        'httptools',
        'uvicorn.protocols.http.httptools_impl',
        'uvicorn.protocols.websockets.wsproto',
        'watchfiles',
        # numpy C-extensions (sometimes missed on Windows)
        'numpy.core._methods',
        'numpy._core._methods',
        # sqlite3 is stdlib but occasionally missed on Windows builds
        '_sqlite3',
        # Pillow
        'PIL._imaging',
        'PIL.Image',
    ],
    excludes=[
        'tkinter',
        'unittest',
        'pydoc',
        'doctest',
        'matplotlib',
        # ETL pipeline is not needed at runtime; osmium binaries are heavy
        'hmaps.etl',
        'osmium',
    ],
    noarchive=False,
    strip=False,
    upx=True,
)

pyz = PYZ(a.pure, a.zipped_data)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='hebrew-maps',
    icon=str(ROOT / 'installer' / 'icon.ico'),
    console=False,
    windowed=True,
    upx=True,
    upx_exclude=[],
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    name='hebrew-maps',
    upx=True,
    upx_exclude=[],
)
