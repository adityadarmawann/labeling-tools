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
