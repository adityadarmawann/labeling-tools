"""
Sesi BERTAHAN melewati restart proses (deploy) -> tak perlu login ulang, tapi
DIBUANG saat mesin reboot (boot berubah) atau LABELAPP_SESI_RESET. Ganti proses
disimulasikan dengan membuat SessionStore baru yang membaca berkas yang sama.
"""
from __future__ import annotations

from app.config import get_settings
from app import session as S


def test_sesi_bertahan_deploy_tapi_reboot_logout(lingkungan, monkeypatch):
    st = get_settings()

    a = S.SessionStore(); a.siapkan(st)
    sid, _sess = a.create("paul", st)
    assert (st.uploads_root / "_sesi.json").exists()

    # Deploy: proses baru, boot mesin SAMA -> sesi dipulihkan (tak login ulang).
    b = S.SessionStore(); b.siapkan(st)
    assert b.get(sid) is not None and b.get(sid).user == "paul"

    # Reboot mesin: boot BERBEDA -> sesi dibuang (login ulang + landing).
    real = S._btime()
    monkeypatch.setattr(S, "_btime", lambda: real + 10000)
    c = S.SessionStore(); c.siapkan(st)
    assert c.get(sid) is None
    assert not (st.uploads_root / "_sesi.json").exists()


def test_sesi_reset_manual(lingkungan):
    st = get_settings()
    a = S.SessionStore(); a.siapkan(st)
    sid, _ = a.create("paul", st)
    # LABELAPP_SESI_RESET -> cabut semua tanpa reboot.
    b = S.SessionStore(); b.siapkan(st, reset=True)
    assert b.get(sid) is None
    assert not (st.uploads_root / "_sesi.json").exists()


def test_sesi_akun_dihapus_tak_dipulihkan(lingkungan):
    st = get_settings()
    a = S.SessionStore(); a.siapkan(st)
    sid, _ = a.create("anggi", st)
    # Akun anggi dihapus dari users.json SEBELUM restart.
    import json
    u = json.loads(st.users_file.read_text()); u.pop("anggi", None)
    st.users_file.write_text(json.dumps(u))
    b = S.SessionStore(); b.siapkan(st)
    assert b.get(sid) is None          # sesi akun yang sudah hilang tak dipulihkan


def test_src_projek_pulih_sesudah_restart(lingkungan):
    """Projek yang terbuka ikut bertahan melewati restart: path-nya disimpan,
    lalu dimuat lagi LAZY di permintaan pertama (deps._lupakan_kalau_dicabut).
    Ini yang membuat rute mutasi tak putus 'belum ada dataset terbuka' pada
    halaman yang dibiarkan terbuka melewati deploy."""
    import cv2
    import numpy as np
    from app import deps
    st = get_settings()
    proj = st.uploads_root / "paul" / "sesi-src"
    proj.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(proj / "g.jpg"), np.zeros((20, 20, 3), np.uint8))

    a = S.SessionStore(); a.siapkan(st)
    sid, sess = a.create("paul", st)
    sess.load(proj)                         # buka projek -> src tersimpan ke disk
    assert sess.src == proj.resolve()

    # Restart: store baru membaca _sesi.json -> src di-stash, belum dimuat.
    b = S.SessionStore(); b.siapkan(st)
    pulih = b.get(sid)
    assert pulih is not None and pulih.src is None
    assert pulih._src_simpan == str(proj.resolve())

    # Permintaan pertama sesudah restart memulihkan projeknya (lazy).
    deps._lupakan_kalau_dicabut(pulih)
    assert pulih.src == proj.resolve(), "projek tak dipulihkan sesudah restart"
    assert pulih._src_simpan is None
