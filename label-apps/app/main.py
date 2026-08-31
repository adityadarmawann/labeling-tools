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

from fastapi import Depends, FastAPI, Request
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import get_settings
from .deps import NeedsLogin, current_session, login_redirect
from .session import Session
from .routers import (admin, annotate, auth, datasets, projek, review, tag,
                      tugas, uploads)

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

    # Selalu ada sekurang-kurangnya satu admin, dan haknya DITULIS ke berkas.
    # Menyimpulkannya saat dibaca tidak cukup: aturan apa pun yang bergantung
    # pada isi berkas bisa gugur begitu isinya berubah, dan yang berubah di
    # sini adalah orang lain mendaftar.
    from .security import pastikan_ada_admin
    diangkat = pastikan_ada_admin(st.users_file)
    if diangkat:
        print(f"  ADMIN     : '{diangkat}' diangkat jadi admin karena belum ada\n"
              f"              satu pun. Ubah lewat halaman /akun.", flush=True)
    yield
    # Cache thumbnail bersifat sementara: dibuang saat proses berhenti supaya
    # /tmp tidak menumpuk sisa dari sesi-sesi lama.
    shutil.rmtree(st.thumb_root, ignore_errors=True)


def create_app() -> FastAPI:
    # Halaman dokumentasi bawaan FastAPI dimatikan lalu dipasang ulang dengan
    # penjaga sesi. Bawaannya terbuka untuk siapa saja yang bisa menjangkau
    # portnya: /openapi.json menyebutkan SETIAP rute beserta nama parameternya,
    # termasuk rute kelola akun, tanpa perlu login sama sekali. Aplikasinya
    # sendiri tidak memakai halaman itu; yang memakainya cuma kita saat
    # mengembangkan, dan kita memang punya akun.
    app = FastAPI(title="Labeling Tools — papan periksa anotasi",
                  description="Papan periksa anotasi untuk dataset "
                              "AnyLabeling / labelme / YOLO-seg, dipakai bersama satu tim.",
                  version="0.1.0",
                  docs_url=None, redoc_url=None, openapi_url=None,
                  lifespan=lifespan)

    @app.get("/openapi.json", include_in_schema=False)
    async def _openapi(sess: Session = Depends(current_session)):
        return app.openapi()

    @app.get("/docs", include_in_schema=False)
    async def _docs(sess: Session = Depends(current_session)):
        return get_swagger_ui_html(openapi_url="/openapi.json",
                                   title="Labeling Tools — API")

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.middleware("http")
    async def _halaman_jangan_dicache(request: Request, panggil):
        """
        Halaman HTML selalu diambil ulang; berkas static tetap boleh di-cache.

        Berkas static sudah punya cap versi dari mtime (lihat templating.statik),
        jadi peramban aman menyimpannya. Halamannya sendiri tidak punya cap
        seperti itu, dan tanpa header ini peramban menebak sendiri — akibatnya
        teks yang sudah diubah di server masih tampil versi lama berjam-jam,
        dan itu terbaca seolah perubahannya tidak pernah dikerjakan.
        """
        r = await panggil(request)
        if "text/html" in r.headers.get("content-type", ""):
            r.headers["Cache-Control"] = "no-cache, must-revalidate"
        return r

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
    app.include_router(projek.router)
    app.include_router(tag.router)
    app.include_router(tugas.router)
    app.include_router(admin.router)
    app.include_router(annotate.router)
    app.include_router(review.router)      # paling akhir: memegang "/"
    return app


app = create_app()
