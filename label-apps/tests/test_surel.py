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
