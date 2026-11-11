"""Login dan logout."""
from __future__ import annotations

import asyncio
import time
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, Request, status

from fastapi.responses import HTMLResponse, RedirectResponse

from ..config import Settings, get_settings
from ..deps import kuki_aman, optional_session
from ..log import catat
from ..services import projek as svc_projek
from ..security import (COOKIE_NAME, authenticate, daftar_sendiri,
                        load_users, menunggu_setujuan, user_slug)
from ..session import store
from ..templating import templates

log = catat("labelapp.auth")

router = APIRouter(tags=["auth"])

# Jeda setiap login gagal. Menebak password lewat jaringan jadi mahal tanpa
# perlu menyimpan penghitung percobaan per alamat.
FAILED_LOGIN_DELAY = 0.6

# Rate-limit login per-IP. Jeda 0,6 dtk di atas membuat satu tebakan mahal, tapi
# di internet publik ribuan tebakan bisa jalan berbarengan. Setelah sekian gagal
# dalam satu jendela, alamat itu diblokir sejenak (429). Di memori saja, seperti
# SessionStore: untuk tim kecil, hilang saat restart dapat diterima, dan tak ada
# basis data yang perlu dijaga. Login berhasil menghapus catatan IP-nya.
_RL_JENDELA = 300.0        # detik: rentang hitung percobaan gagal
_RL_MAKS = 10              # maksimum gagal dalam jendela sebelum diblokir
_RL_DINGIN = 300.0         # detik: lama blokir setelah tembus batas
_gagal_login: dict[str, list[float]] = {}


def _klien_ip(request: Request) -> str:
    """IP klien sebenarnya. Di belakang nginx, request.client itu 127.0.0.1;
    alamat aslinya ada di X-Forwarded-For (dipasang proxy, aman karena backend
    di-bind ke 127.0.0.1 sehingga tak bisa dijangkau langsung dari luar)."""
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "?"


def _blokir_sisa(ip: str) -> float:
    """Detik sisa blokir untuk IP ini; 0 kalau boleh mencoba."""
    now = time.time()
    cap = [t for t in _gagal_login.get(ip, []) if now - t < _RL_JENDELA]
    if cap:
        _gagal_login[ip] = cap
    else:
        _gagal_login.pop(ip, None)
    if len(cap) >= _RL_MAKS:
        return max(0.0, _RL_DINGIN - (now - cap[-1]))
    return 0.0


def _catat_gagal(ip: str) -> None:
    _gagal_login.setdefault(ip, []).append(time.time())
    # Jaga ukuran kamus supaya banjir IP acak tak menggelembungkan memori.
    if len(_gagal_login) > 5000:
        now = time.time()
        for k in list(_gagal_login):
            sisa = [t for t in _gagal_login[k] if now - t < _RL_JENDELA]
            if sisa:
                _gagal_login[k] = sisa
            else:
                _gagal_login.pop(k, None)


def _boleh_daftar(s: Settings) -> bool:
    """Tautan "Daftar" publik hanya muncul kalau daftar mandiri hidup DAN bukan
    mode hanya-undangan. Di mode undangan, akun baru hanya lahir lewat tautan
    token di email -- halaman masuk tak menawarkan daftar terbuka."""
    return s.daftar_sendiri and not s.daftar_hanya_undangan


# Halaman PUBLIK (tanpa sesi): wajah aplikasi + dokumentasi fitur. Akar "/"
# untuk pengunjung anonim juga memakai landing.html (lewat handler NeedsLogin di
# main.py); rute eksplisit ini menyediakan URL tetap yang SELALU menampilkannya
# — berguna untuk ditautkan, dan untuk pratinjau di dev (yang auto-login
# membuat "/" selalu masuk sebagai devuser). Keduanya tak butuh sesi.
@router.get("/landing", response_class=HTMLResponse)
async def landing(request: Request):
    return templates.TemplateResponse(request, "landing.html", {})


@router.get("/fitur", response_class=HTMLResponse)
async def fitur(request: Request):
    return templates.TemplateResponse(request, "fitur.html", {})


# Kode galat halaman masuk. Dikirim lewat query oleh redirect sesudah POST,
# lalu diterjemahkan DI SINI -- bukan teksnya yang dioper -- supaya tautan
# buatan orang lain tak bisa menampilkan kalimat apa pun di halaman kita.
GALAT_LOGIN = {
    "salah": "Akun atau password salah.",
    "belum": "Akun ini sudah terdaftar tapi belum disetujui admin.",
    "limit": "Terlalu banyak percobaan gagal. Coba lagi sebentar.",
}


def _balik_login(e: str, akun: str = "", next: str = "", detik: int = 0,
                 headers: dict | None = None) -> RedirectResponse:
    """POST lalu REDIRECT lalu GET untuk kegagalan masuk.

    Dulu kegagalan dirender langsung dari POST. Peramban lalu menyimpan
    halaman itu sebagai hasil sebuah POST, jadi menekan muat ulang atau tombol
    kembali memunculkan "Confirm Form Resubmission" dan, di Chrome yang tak
    menyimpan badan POST, ERR_CACHE_MISS -- halaman yang tak bisa dimuat ulang
    sama sekali. Yang salah bentuk responsnya, bukan pemeriksaannya.
    """
    dari = {"e": e}
    if akun:
        dari["akun"] = akun
    if next:
        dari["next"] = next
    if detik:
        dari["detik"] = str(int(detik))
    return RedirectResponse("/login?" + urlencode(dari),
                            status_code=status.HTTP_303_SEE_OTHER,
                            headers=headers or {})


@router.get("/login", response_class=HTMLResponse)
async def login_form(request: Request, next: str = "", e: str = "",
                     akun: str = "", detik: int = 0):
    if optional_session(request):
        # Sudah masuk: langsung ke tujuan semula, bukan ke beranda. Orang yang
        # menekan tautan undangan sambil sudah punya sesi tidak perlu lewat
        # halaman masuk sama sekali.
        tujuan = next if next.startswith("/") and not next.startswith("//") else "/"
        return RedirectResponse(tujuan, status_code=status.HTTP_303_SEE_OTHER)
    s = get_settings()
    # Pesan galat datang sebagai KODE lewat query, bukan teks. Teks bebas di
    # query berarti siapa pun bisa membuat tautan yang menampilkan kalimat
    # apa pun di halaman masuk kita sendiri.
    pesan = GALAT_LOGIN.get(e, "")
    if e == "limit" and detik > 0:
        pesan = (f"Terlalu banyak percobaan gagal. Coba lagi sekitar "
                 f"{int(detik)} detik lagi.")
    return templates.TemplateResponse(request, "login.html", {
        "akun": akun, "next": next,
        "boleh_daftar": _boleh_daftar(s),
        "daftar_langsung": s.daftar_langsung,
        "error": pesan})


@router.post("/login")
async def login(request: Request,
                user: str = Form(""),
                pw: str = Form(""),
                next: str = Form(""),
                ingat: str = Form(""),
                settings: Settings = Depends(get_settings)):
    # Rate-limit hanya untuk lalu lintas yang datang LEWAT PROXY (internet
    # publik): nginx selalu memasang X-Forwarded-For, sedangkan koneksi langsung
    # tanpa header itu hanya mungkin dari mesin server sendiri (backend di-bind
    # ke 127.0.0.1). Jadi ancaman tebak-beruntun dari luar tetap tertahan, tanpa
    # menghukum akses lokal atau mengganggu suite tes yang tak lewat proxy.
    diproksikan = bool(request.headers.get("x-forwarded-for"))
    ip = _klien_ip(request)
    if diproksikan and (sisa := _blokir_sisa(ip)) > 0:
        # Terlalu banyak gagal dari alamat ini. Tahan dulu, jangan sampai
        # tebakan beruntun bisa jalan tanpa batas dari internet publik.
        detik = int(sisa) + 1
        # Retry-After tetap dipasang: ia keterangan yang bisa dibaca mesin dan
        # tak ada ruginya ikut di redirect.
        return _balik_login("limit", user, next, detik,
                            headers={"Retry-After": str(detik)})

    users = load_users(settings.users_file)
    akun = authenticate(users, user, pw)
    if akun is None:
        if diproksikan:
            _catat_gagal(ip)
        await asyncio.sleep(FAILED_LOGIN_DELAY)
        # Sandi benar tapi belum disetujui itu keadaan yang berbeda, dan
        # menyebutnya "akun atau password salah" membuat orang mengetik ulang
        # sandinya berkali-kali padahal sandinya memang benar.
        menunggu = menunggu_setujuan(users, user_slug(user))
        return _balik_login("belum" if menunggu else "salah", user, next)

    # Berhasil: lupakan catatan gagal IP ini supaya pemakaian wajar tak terkena
    # ambang gara-gara salah ketik beberapa kali sebelumnya.
    _gagal_login.pop(ip, None)
    sid, sess = store.create(akun, settings)
    if settings.default_src and settings.default_src.is_dir():
        try:
            await asyncio.to_thread(sess.load, settings.default_src)
        except Exception:
            pass          # folder awal bermasalah bukan alasan gagal login

    # Kembali ke halaman yang tadi diminta, kalau ada. Hanya jalur di dalam
    # aplikasi ini — '//' dan skema apa pun dibuang, supaya kolom tersembunyi
    # ini tidak bisa dipakai mengarahkan orang ke situs lain lewat alamat yang
    # mereka percaya.
    tujuan = next if next.startswith("/") and not next.startswith("//") else "/"
    resp = RedirectResponse(tujuan, status_code=status.HTTP_303_SEE_OTHER)
    # "Ingat saya" -> cookie PERSISTEN (bertahan Chrome ditutup, sampai 30 hari
    # = sama dengan umur sesi server di session.py). Tak dicentang -> cookie
    # sesi (hilang saat browser ditutup), untuk komputer bersama.
    resp.set_cookie(COOKIE_NAME, sid, httponly=True, samesite="lax", path="/",
                    secure=kuki_aman(request),
                    max_age=(30 * 86400 if ingat else None))
    return resp


@router.get("/logout")
async def logout(request: Request):
    store.drop(request.cookies.get(COOKIE_NAME))
    resp = RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    resp.delete_cookie(COOKIE_NAME, path="/")
    return resp


# Kode galat form pendaftaran, pola yang sama dengan GALAT_LOGIN: yang dioper
# lewat query kodenya, bukan kalimatnya.
GALAT_DAFTAR = {
    "akun": "Nama akun masih kosong.",
    "pendek": "Kata sandi minimal 8 karakter.",
    "ulang": "Ulangan kata sandinya tidak sama.",
    "email": "Alamat email itu tidak terlihat benar.",
}


@router.get("/daftar", response_class=HTMLResponse)
async def daftar_form(request: Request, next: str = "", e: str = "",
                      akun: str = "", email: str = "", pesan: str = "",
                      ok: str = "",
                      settings: Settings = Depends(get_settings)):
    if ok:
        # Baru selesai mendaftar dan menunggu persetujuan admin. Dirender dari
        # GET, jadi halamannya bisa dimuat ulang dan dibagikan tanpa mendaftar
        # ulang apa pun.
        return templates.TemplateResponse(request, "daftar.html", {
            "boleh": True, "selesai": ok, "langsung": False})
    if optional_session(request):
        return RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
    # Mode hanya-undangan: tanpa tautan undangan bertoken yang sah, tak ada form
    # sama sekali -- cuma keterangan bahwa akun dibuat lewat undangan. Yang
    # datang dari tautan undangan (next=/undangan/<token sah>) tetap dapat form.
    if settings.daftar_hanya_undangan and not await _diundang(settings, next):
        return templates.TemplateResponse(
            request, "daftar.html", {"boleh": False, "hanya_undangan": True},
            status_code=status.HTTP_403_FORBIDDEN)
    return templates.TemplateResponse(request, "daftar.html", {
        "boleh": settings.daftar_sendiri, "akun": akun, "email": email,
        "next": next,
        # Teks bebas hanya diterima bersama kode "layanan"; selain itu
        # kalimatnya diambil dari kamus, bukan dari query.
        "error": (pesan[:120] if e == "layanan" and pesan
                  else GALAT_DAFTAR.get(e, ""))})


def _token_undangan(next: str) -> str:
    """Token undangan dari `next` kalau ia menunjuk /undangan/<token>, else ''."""
    if next.startswith("/undangan/"):
        return next[len("/undangan/"):].split("?", 1)[0].split("/", 1)[0]
    return ""


async def _diundang(settings: Settings, next: str) -> bool:
    """True kalau `next` membawa tautan undangan bertoken yang SAH (tokennya
    benar-benar ada di uploads_root). Inilah yang membedakan pendaftar diundang
    dari pendaftar liar di mode hanya-undangan, sekaligus yang membuat akun
    diundang langsung aktif tanpa menunggu persetujuan."""
    token = _token_undangan(next)
    if not token:
        return False
    return (await asyncio.to_thread(
        svc_projek.cari_undangan, settings.uploads_root, token)) is not None


@router.post("/daftar")
async def daftar_kirim(request: Request, user: str = Form(""),
                       email: str = Form(""), pw: str = Form(""),
                       pw2: str = Form(""), next: str = Form(""),
                       settings: Settings = Depends(get_settings)):
    """
    Pendaftaran mandiri. Hasilnya akun yang MENUNGGU persetujuan admin.

    Tanpa verifikasi email, tidak ada yang membuktikan alamat yang diketik
    milik pendaftarnya. Persetujuan admin yang menggantikan pembuktian itu,
    dan itu juga yang membuat rute ini aman kalau portnya ternyata terbuka
    ke luar jaringan kantor.

    KECUALI kalau mendaftar lewat tautan UNDANGAN bertoken yang sah (`next` =
    /undangan/<token>): token itu rahasia dan diberikan pemilik projek, jadi
    pendaftarnya SUDAH diizinkan. Akunnya langsung aktif (tak perlu persetujuan
    admin terpisah), lalu langsung dibawa menerima undangannya -- satu alur,
    satu klik: tautan email -> daftar -> langsung gabung.
    """
    def salah(kode, pesan=""):
        """POST lalu REDIRECT lalu GET, sama seperti halaman masuk.

        Nama akun dan email dibawa kembali supaya tak perlu diketik ulang;
        KATA SANDI tidak pernah ikut. Sandi di query berarti sandi di riwayat
        peramban, di log proxy, dan di header Referer halaman berikutnya.
        """
        dari = {"e": kode}
        if pesan:
            dari["pesan"] = pesan[:120]
        if user:
            dari["akun"] = user
        if email:
            dari["email"] = email
        if next:
            dari["next"] = next
        return RedirectResponse("/daftar?" + urlencode(dari),
                                status_code=status.HTTP_303_SEE_OTHER)

    # Dua penolakan di bawah TETAP merender dengan 403, tidak diredirect.
    # Keduanya menolak sebelum apa pun diproses: tak ada akun yang lahir, tak
    # ada isian yang perlu dibawa kembali, dan memuat ulangnya cuma menolak
    # lagi. 403 di sini keterangan yang berguna; menukarnya dengan 303 berarti
    # membuang sinyal demi kenyamanan yang tak pernah dibutuhkan.
    if not settings.daftar_sendiri:
        return templates.TemplateResponse(
            request, "daftar.html", {"boleh": False},
            status_code=status.HTTP_403_FORBIDDEN)

    # Diundang atau tidak diperiksa LEBIH DULU: di mode hanya-undangan, pendaftar
    # tanpa token sah ditolak sebelum apa pun disentuh -- tak ada akun "menunggu"
    # yang lahir dari internet, dan pesan galat form pun tak bocor.
    diundang = await _diundang(settings, next)
    if settings.daftar_hanya_undangan and not diundang:
        return templates.TemplateResponse(
            request, "daftar.html", {"boleh": False, "hanya_undangan": True},
            status_code=status.HTTP_403_FORBIDDEN)

    if not (user or "").strip():
        return salah("akun")
    if len(pw) < 8:
        return salah("pendek")
    if pw != pw2:
        return salah("ulang")
    if email and "@" not in email:
        return salah("email")

    try:
        akun = await asyncio.to_thread(
            daftar_sendiri, settings.users_file, user, pw, email,
            settings.daftar_langsung or diundang)
    except ValueError as e:
        # Pesannya datang dari layanan (mis. "nama akun itu sudah ada"), jadi
        # ia tak bisa jadi kode tetap. Dioper lewat query sebagai teks yang
        # SUDAH dibatasi panjangnya, dan halaman tujuan hanya menampilkannya
        # kalau kode galatnya memang "layanan" -- jadi tautan buatan orang lain
        # tak bisa menyuntik kalimat tanpa ikut menyebut kodenya.
        return salah("layanan", str(e).capitalize() + ".")
    except OSError as e:
        return salah("layanan", f"Gagal menyimpan: {str(e)[:80]}")

    if diundang:
        # Diundang: langsung masuk + diteruskan ke halaman terima undangan, yang
        # menambahkannya jadi anggota projek. Tak ada layar "menunggu" di tengah.
        log.info("pendaftaran via undangan: %r (email=%r) aktif + diteruskan",
                 akun, (email or "").strip())
        sid, _sess = store.create(akun, settings)
        resp = RedirectResponse(next, status_code=status.HTTP_303_SEE_OTHER)
        # Baru mendaftar lewat undangan -> langsung diingat (persisten 30 hari).
        resp.set_cookie(COOKIE_NAME, sid, httponly=True, samesite="lax", path="/",
                        secure=kuki_aman(request), max_age=30 * 86400)
        return resp

    if settings.daftar_langsung:
        # Akun LANGSUNG aktif -> langsung masuk, sama seperti alur undangan.
        # Dulu malah menampilkan layar "selesai" tanpa cookie, jadi orang yang
        # baru mendaftar harus mengetik ulang kredensialnya di halaman login.
        log.info("pendaftaran mandiri: %r (email=%r) langsung aktif + masuk",
                 akun, (email or "").strip())
        sid, _sess = store.create(akun, settings)
        # Sama seperti /login: hanya jalur di dalam aplikasi ini. '//' dan skema
        # apa pun dibuang, supaya kolom tersembunyi ini tak bisa dipakai
        # mengarahkan orang yang baru mendaftar ke situs lain.
        tujuan = next if next.startswith("/") and not next.startswith("//") else "/"
        resp = RedirectResponse(tujuan, status_code=status.HTTP_303_SEE_OTHER)
        resp.set_cookie(COOKIE_NAME, sid, httponly=True, samesite="lax", path="/",
                        secure=kuki_aman(request), max_age=30 * 86400)
        return resp

    log.info("pendaftaran mandiri: %r (email=%r) menunggu persetujuan", akun,
             (email or "").strip())
    # Halaman berhasil pun lewat redirect. Dirender langsung dari POST, menekan
    # muat ulang MENGIRIM ULANG pendaftarannya -- percobaan kedua lalu ditolak
    # "nama akun itu sudah ada", dan orang yang baru saja berhasil mendaftar
    # justru melihat pesan kegagalan.
    return RedirectResponse("/daftar?" + urlencode({"ok": akun}),
                            status_code=status.HTTP_303_SEE_OTHER)
