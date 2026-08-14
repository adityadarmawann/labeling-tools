"""
Perakitan aplikasi.

Berkas ini sengaja tipis: hanya membuat aplikasi, memasang static, mendaftarkan
router, dan mengurus pembersihan saat berhenti. Semua logika ada di services/,
semua URL ada di routers/.
"""
from __future__ import annotations

import shutil
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import get_settings
from .deps import NeedsLogin, login_redirect
from .routers import annotate, auth, datasets, review, uploads

STATIC_DIR = Path(__file__).resolve().parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    st = get_settings()
    # flush eksplisit: aplikasi ini sering dijalankan dengan stdout diarahkan
    # ke berkas log, dan baris-baris ini yang dipakai untuk memastikan
    # konfigurasi yang benar-benar terbaca.
    print(f"  Akun      : {st.users_file}", flush=True)
    print(f"  Daftar dari: {st.datasets_root or '(LABELAPP_DATASETS_ROOT tidak diisi)'}", flush=True)
    print(f"  Unggahan  : {st.uploads_root}  (maks {st.max_upload_mb} MB/berkas)", flush=True)
    print(f"  Thumbnail : {st.thumb_root}  (per akun, dihapus saat berhenti)", flush=True)
    if st.autologin:
        print(f"  AUTOLOGIN : '{st.autologin}' — masuk tanpa password, HANYA dari\n"
              f"              mesin ini. Permintaan dari jaringan tetap harus login.",
              flush=True)
    yield
    # Cache thumbnail bersifat sementara: dibuang saat proses berhenti supaya
    # /tmp tidak menumpuk sisa dari sesi-sesi lama.
    shutil.rmtree(st.thumb_root, ignore_errors=True)


def create_app() -> FastAPI:
    app = FastAPI(title="Labeling Tools — papan periksa anotasi",
                  description="Papan periksa anotasi untuk dataset "
                              "AnyLabeling / labelme / YOLO-seg, dipakai bersama satu tim.",
                  version="0.1.0",
                  lifespan=lifespan)

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.exception_handler(NeedsLogin)
    async def _needs_login(request: Request, exc: NeedsLogin):
        """Halaman HTML tanpa sesi dialihkan ke /login, tidak menampilkan 401."""
        return login_redirect()

    @app.exception_handler(404)
    async def _not_found(request: Request, exc):
        if request.url.path.startswith(("/api", "/upload")):
            return JSONResponse({"ok": False, "error": "tidak ada"}, status_code=404)
        return JSONResponse({"ok": False, "error": "halaman tidak ada"}, status_code=404)

    app.include_router(auth.router)
    app.include_router(datasets.router)
    app.include_router(uploads.router)
    app.include_router(annotate.router)
    app.include_router(review.router)      # paling akhir: memegang "/"
    return app


app = create_app()
