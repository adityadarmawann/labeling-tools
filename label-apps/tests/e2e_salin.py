"""
Tombol salin di konteks yang TIDAK aman — lewat alamat IP, HTTP biasa.

KENAPA TERPISAH DARI e2e_kanvas.py
----------------------------------
e2e_kanvas menjalankan servernya sendiri di 127.0.0.1, dan localhost adalah
secure context menurut peramban. Di sana navigator.clipboard ADA dan semua
tombol salin bekerja — jadi berkas itu tidak akan pernah bisa menangkap bug
ini, berapa kali pun dijalankan.

Satu tim membuka aplikasi ini lewat alamat IP di jaringan kantor dengan HTTP
biasa. Di sanalah navigator.clipboard justru tidak ada, dan tombol salin
tautan undangan — satu-satunya jalan seseorang masuk ke sebuah projek — jadi
tombol yang selalu gagal.

    .venv/bin/python tests/e2e_salin.py <sandi-darma-dev>
    .venv/bin/python tests/e2e_salin.py <sandi> --base http://103.182.240.28:8043

Menembak server dev yang SEDANG BERJALAN. Membuat satu undangan uji lalu
membatalkannya lagi.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from websockets.sync.client import connect  # noqa: E402

from e2e_kanvas import Cdp  # noqa: E402

BASE = "http://103.182.240.28:8043"
AKUN = "darma-dev"
PROJEK = "paragon"
CDP = 9334

gagal: list[str] = []
n_ok = 0


def cek(nama, syarat, detail=""):
    global n_ok
    hijau, merah, mati = "\033[32m", "\033[31m", "\033[0m"
    tanda = f"{hijau}OK   {mati}" if syarat else f"{merah}GAGAL{mati}"
    print(f"  {tanda} {nama}" + (f"  {detail}" if detail else ""))
    if syarat:
        n_ok += 1
    else:
        gagal.append(nama)


def jalankan(base: str, sandi: str) -> int:
    tmp = Path(tempfile.mkdtemp(prefix="salin-"))
    chrome = subprocess.Popen(
        ["google-chrome", "--headless=new", "--disable-gpu", "--no-sandbox",
         f"--remote-debugging-port={CDP}", f"--user-data-dir={tmp}",
         "--window-size=1500,950", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        ws = None
        for _ in range(60):
            try:
                ws = json.load(urllib.request.urlopen(
                    f"http://127.0.0.1:{CDP}/json/version"))["webSocketDebuggerUrl"]
                break
            except Exception:
                time.sleep(.25)
        if not ws:
            raise RuntimeError("chrome tidak menyala")
        d = Cdp(connect(ws, max_size=None))
        tid = d.kirim("Target.createTarget", url="about:blank")["targetId"]
        info = json.load(urllib.request.urlopen(f"http://127.0.0.1:{CDP}/json"))
        p = Cdp(connect([x for x in info if x["id"] == tid][0]["webSocketDebuggerUrl"],
                        max_size=None))
        p.kirim("Page.enable")
        p.kirim("Runtime.enable")

        def gestur(kode):
            """
            Jalankan dengan tanda gestur pengguna.

            execCommand('copy') menolak dijalankan tanpa gestur, dan klik
            tombol sungguhan memberikannya. Tanpa tanda ini yang teruji bukan
            keadaan yang dialami orang, melainkan keadaan yang lebih ketat
            dari mana pun.
            """
            r = p.kirim("Runtime.evaluate", expression=kode, returnByValue=True,
                        awaitPromise=True, userGesture=True)
            return r.get("result", {}).get("value")

        p.kirim("Page.navigate", url=f"{base}/login")
        time.sleep(1.5)
        p.js(f"""(() => {{
            document.querySelector('[name=user]').value = {AKUN!r};
            document.querySelector('[name=pw]').value = {sandi!r};
            document.querySelector('form').submit();
        }})()""")
        time.sleep(2)
        p.kirim("Page.navigate", url=f"{base}/bagi?ds={PROJEK}")
        time.sleep(3)

        # Keadaan yang membuat tombolnya gagal harus benar-benar ada di sini.
        # Kalau tidak, ujinya menguji hal lain dan lolos tanpa arti.
        cek("konteks memang tidak aman", p.js("window.isSecureContext") is False)
        cek("navigator.clipboard memang tidak ada", p.js("!navigator.clipboard"),
            "inilah sebabnya tombol salin dulu selalu gagal")
        cek("salinTeks tersedia", p.js("typeof salinTeks") == "function")
        cek("salinTeks berhasil tanpa konteks aman",
            gestur("salinTeks('uji-tautan-undangan-123')") is True)

        # Isinya benar-benar sampai ke papan klip, bukan cuma "tidak melempar".
        p.js("""(() => { const t = document.createElement('textarea');
                 t.id = 'tempel-uji'; document.body.appendChild(t); t.focus(); })()""")

        def tempel():
            p.js("document.getElementById('tempel-uji').value=''")
            p.js("document.getElementById('tempel-uji').focus()")
            p.kirim("Input.dispatchKeyEvent", type="keyDown", modifiers=2,
                    key="v", code="KeyV", windowsVirtualKeyCode=86,
                    nativeVirtualKeyCode=86, commands=["paste"])
            time.sleep(.4)
            return p.js("document.getElementById('tempel-uji').value")

        cek("isinya benar-benar di papan klip",
            tempel() == "uji-tautan-undangan-123")

        # Lalu lewat jalur yang benar-benar dipakai orang.
        gestur("document.getElementById('bg-tambah-orang').click()")
        time.sleep(.3)
        gestur("""(() => {
            document.getElementById('bg-email').value = 'uji-salin@contoh.id';
            document.getElementById('bg-undang-kirim').click();
        })()""")
        time.sleep(3)
        cek("tautan undangan muncul", bool(p.js("!!document.getElementById('bg-salin')")))
        tautan = p.js("(document.getElementById('bg-tautan-teks')||{}).textContent||''")
        gestur("document.getElementById('bg-salin').click()")
        time.sleep(.8)
        pesan = p.js("(document.getElementById('t')||{}).textContent||''")
        cek("tombol Salin menyatakan berhasil", "disalin" in pesan.lower(), repr(pesan))
        cek("yang tersalin adalah tautan undangannya", tempel() == tautan,
            tautan[:56])

        # Undangan uji dibatalkan lagi: uji yang meninggalkan jejak membuat
        # daftar undangan projek sungguhan penuh sisa yang tidak ada yang tahu
        # asalnya.
        p.js("""(async () => {
            const j = await (await fetch('/api/tugas/calon')).json();
            for (const u of (j.undangan || [])) {
                if ((u.email || '').includes('uji-salin')) {
                    await fetch('/api/tugas/batalkan-undangan?token='
                                + encodeURIComponent(u.token), {method: 'POST'});
                }
            }
        })()""", tunggu=True)
        time.sleep(.6)
        sisa = p.js("""(async () => {
            const j = await (await fetch('/api/tugas/calon')).json();
            return (j.undangan || []).filter(u => (u.email||'').includes('uji-salin')).length;
        })()""", tunggu=True)
        cek("undangan uji dibereskan", sisa == 0, f"tersisa {sisa}")
    finally:
        chrome.terminate()

    print(f"\n  {n_ok} lolos, {len(gagal)} gagal")
    for g in gagal:
        print(f"  gagal: {g}")
    return 1 if gagal else 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    base = BASE
    if "--base" in sys.argv:
        base = sys.argv[sys.argv.index("--base") + 1]
    raise SystemExit(jalankan(base, sys.argv[1]))
