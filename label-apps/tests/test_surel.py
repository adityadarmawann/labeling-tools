"""
Kirim email undangan lewat SMTP (services/surel.py).

SMTP-nya dipalsukan: yang dijaga adalah pesan yang dibangun benar (From/To/
Subject + login), fitur mati kalau tak dikonfigurasi, dan kegagalan kirim tidak
melempar ke pemanggil (link undangan tetap dikembalikan).
"""
from __future__ import annotations

from types import SimpleNamespace

from app.services import surel


class _FakeSMTP:
    instances: list = []

    def __init__(self, host, port, timeout=0):
        self.host = host
        self.port = port
        self.logged = None
        self.tls = False
        self.sent = []
        _FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def ehlo(self):
        pass

    def starttls(self, context=None):
        self.tls = True

    def login(self, u, p):
        self.logged = (u, p)

    def send_message(self, msg):
        self.sent.append(msg)


def _s(**kw):
    base = dict(smtp_host="smtp.test", smtp_port=587, smtp_user="u",
                smtp_pass="p", smtp_from="from@x.id", nama_app="HIGOLAB")
    base.update(kw)
    return SimpleNamespace(**base)


def test_aktif_butuh_host_dan_pengirim():
    assert not surel.aktif(_s(smtp_host=""))
    assert not surel.aktif(_s(smtp_from=""))
    assert surel.aktif(_s())


def test_kirim_starttls_login_dan_pesan(monkeypatch):
    _FakeSMTP.instances = []
    monkeypatch.setattr(surel.smtplib, "SMTP", _FakeSMTP)
    surel.kirim(_s(), "ke@x.id", "Halo", "isi", html="<b>isi</b>")
    assert len(_FakeSMTP.instances) == 1
    f = _FakeSMTP.instances[0]
    assert f.host == "smtp.test" and f.port == 587 and f.tls
    assert f.logged == ("u", "p")
    assert len(f.sent) == 1
    msg = f.sent[0]
    assert msg["To"] == "ke@x.id"
    assert msg["From"] == "from@x.id"
    assert msg["Subject"] == "Halo"


def test_kirim_undangan_sukses_lalu_gagal_tak_melempar(monkeypatch):
    _FakeSMTP.instances = []
    monkeypatch.setattr(surel.smtplib, "SMTP", _FakeSMTP)
    assert surel.kirim_undangan(_s(), "ke@x.id",
                                "http://s/undangan/t", "ProjekA") is True
    # Template resmi ikut: judul + link + catatan Wi-Fi/VPN.
    msg = _FakeSMTP.instances[-1].sent[-1]
    assert "HIGOLAB" in msg["Subject"] and "ProjekA" in msg["Subject"]
    body = msg.get_body(preferencelist=("plain",)).get_content()
    assert "http://s/undangan/t" in body and "VPN" in body

    def _boom(*a, **k):
        raise OSError("tolak")
    monkeypatch.setattr(surel.smtplib, "SMTP", _boom)
    # Gagal kirim -> False, BUKAN melempar (undangan/link tetap selamat).
    assert surel.kirim_undangan(_s(), "ke@x.id",
                                "http://s/undangan/t", "ProjekA") is False


# ---------------------------------------------------------------- hasil training

def test_hasil_subjek_dan_label(monkeypatch):
    _FakeSMTP.instances = []
    monkeypatch.setattr(surel.smtplib, "SMTP", _FakeSMTP)
    assert surel.kirim_hasil_latih(_s(), "ke@x.id", "frisian-flag",
                                   "ff-v1", "gagal") is True
    msg = _FakeSMTP.instances[-1].sent[-1]
    assert msg["Subject"] == '[HIGOLAB] Training "ff-v1" GAGAL (frisian-flag)'


def test_hasil_catatan_kosong_tak_ada_baris(monkeypatch):
    _FakeSMTP.instances = []
    monkeypatch.setattr(surel.smtplib, "SMTP", _FakeSMTP)
    # catatan kosong -> tak boleh ada kata "Catatan" di badan (permintaan tim).
    surel.kirim_hasil_latih(_s(), "ke@x.id", "P", "v1", "selesai", catatan="")
    body = _FakeSMTP.instances[-1].sent[-1].get_body(
        preferencelist=("plain",)).get_content()
    assert "Catatan" not in body
    # catatan ada -> muncul.
    surel.kirim_hasil_latih(_s(), "ke@x.id", "P", "v1", "selesai",
                            catatan="uji 10 epoch")
    body2 = _FakeSMTP.instances[-1].sent[-1].get_body(
        preferencelist=("plain",)).get_content()
    assert "Catatan" in body2 and "uji 10 epoch" in body2


def test_hasil_rincian_hasil_vs_penyebab(monkeypatch):
    _FakeSMTP.instances = []
    monkeypatch.setattr(surel.smtplib, "SMTP", _FakeSMTP)
    surel.kirim_hasil_latih(_s(), "ke@x.id", "P", "v1", "gagal",
                            rincian="CUDA out of memory")
    body = _FakeSMTP.instances[-1].sent[-1].get_body(
        preferencelist=("plain",)).get_content()
    assert "Penyebab" in body and "CUDA out of memory" in body and "Hasil" not in body
    surel.kirim_hasil_latih(_s(), "ke@x.id", "P", "v1", "selesai",
                            rincian="10 epoch, mAP50 0.900")
    body2 = _FakeSMTP.instances[-1].sent[-1].get_body(
        preferencelist=("plain",)).get_content()
    assert "Hasil" in body2 and "mAP50 0.900" in body2


def _siapkan_record(tmp_path, monkeypatch, keadaan="gagal", **extra):
    """Satu record training + users.json + settings SMTP aktif, dipakai menguji
    latih.beritahu_hasil end-to-end tanpa menyentuh SMTP sungguhan."""
    from app.services import latih
    from app import config
    (tmp_path / "users.json").write_text('{"darma": {"email": "d@x.id"}}')
    rec = dict(keadaan=keadaan, nama="ff-v1", oleh="darma", catatan="",
               galat="CUDA out of memory" if keadaan in ("gagal", "hilang") else "")
    rec.update(extra)
    latih.perbarui(tmp_path, 1, **rec)
    st = _s(users_file=str(tmp_path / "users.json"),
            basis_publik="https://h.test", smtp_host="smtp.test")
    monkeypatch.setattr(config, "get_settings", lambda: st)
    _FakeSMTP.instances = []
    monkeypatch.setattr(surel.smtplib, "SMTP", _FakeSMTP)
    return latih


def test_beritahu_hasil_sekali_lalu_idempoten(tmp_path, monkeypatch):
    latih = _siapkan_record(tmp_path, monkeypatch, keadaan="gagal")
    assert latih.beritahu_hasil(tmp_path, 1) is True
    assert len(_FakeSMTP.instances) == 1
    assert latih.baca(tmp_path, 1).get("surel_terkirim") is True
    # Panggilan kedua tak mengirim lagi.
    assert latih.beritahu_hasil(tmp_path, 1) is False
    assert len(_FakeSMTP.instances) == 1


def test_beritahu_hasil_smtp_mati_tak_kirim(tmp_path, monkeypatch):
    from app import config
    latih = _siapkan_record(tmp_path, monkeypatch, keadaan="gagal")
    monkeypatch.setattr(config, "get_settings",
                        lambda: _s(users_file=str(tmp_path / "users.json"),
                                   smtp_host=""))   # SMTP mati
    assert latih.beritahu_hasil(tmp_path, 1) is False
    assert len(_FakeSMTP.instances) == 0
    assert not latih.baca(tmp_path, 1).get("surel_terkirim")


def test_beritahu_hasil_tanpa_email_pemilik(tmp_path, monkeypatch):
    latih = _siapkan_record(tmp_path, monkeypatch, keadaan="gagal", oleh="nobody")
    assert latih.beritahu_hasil(tmp_path, 1) is False
    assert len(_FakeSMTP.instances) == 0


def test_beritahu_hasil_hanya_keadaan_akhir(tmp_path, monkeypatch):
    latih = _siapkan_record(tmp_path, monkeypatch, keadaan="jalan")
    assert latih.beritahu_hasil(tmp_path, 1) is False
    assert len(_FakeSMTP.instances) == 0


def test_kirim_ditambahkan_pemberitahuan(monkeypatch):
    """Anggota yang akunnya sudah ada diberi tahu lewat email saat ditambahkan
    ke projek -- bukan tautan undangan, tapi pemberitahuan + tombol buka."""
    _FakeSMTP.instances = []
    monkeypatch.setattr(surel.smtplib, "SMTP", _FakeSMTP)
    assert surel.kirim_ditambahkan(_s(), "rizky@higo.id", "frisian-flag",
                                   "https://h.test/") is True
    msg = _FakeSMTP.instances[-1].sent[-1]
    assert msg["Subject"] == 'Anda ditambahkan ke proyek "frisian-flag" di HIGOLAB'
    body = msg.get_body(preferencelist=("plain",)).get_content()
    assert "ditambahkan" in body and "frisian-flag" in body
    assert "https://h.test/" in body            # tautan buka HIGOLAB
    assert "undangan" not in body.lower()        # bukan alur daftar/terima
