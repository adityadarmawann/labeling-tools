"""
Kirim email lewat SMTP pihak ketiga (Brevo / SendGrid / Mailgun / dll).

Dipakai mengirim link undangan projek otomatis. Sengaja TIDAK terikat ke satu
layanan: yang dibutuhkan cuma setelan SMTP standar (host/port/user/pass/from),
jadi layanan mana pun bisa dipakai tanpa ubah kode. Lihat Settings.smtp_* di
config.py; smtp_host kosong = fitur MATI, dan undangan jatuh ke "salin link".

Kenapa lewat layanan, bukan akun Google langsung: Google mematikan login SMTP
pakai password biasa, dan App Password menuntut 2-Step Verification yang di
higo.id tidak diizinkan. Layanan transaksional memberi kredensial SMTP sendiri
tanpa menyentuh akun Google.
"""
from __future__ import annotations

import smtplib
import re
import ssl
from email.message import EmailMessage

from ..log import catat

log = catat("labelapp.surel")


def aktif(settings) -> bool:
    """True kalau SMTP sudah dikonfigurasi (host + alamat pengirim)."""
    return bool(getattr(settings, "smtp_host", "")
                and getattr(settings, "smtp_from", ""))


def _esc(s: str) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def kirim(settings, ke: str, subjek: str, teks: str, html: str | None = None) -> None:
    """Kirim satu email. MELEMPAR kalau gagal; pemanggil yang memutuskan efeknya.

    Port 465 -> SMTP_SSL; selain itu STARTTLS (587 umum di Brevo/SendGrid).
    """
    msg = EmailMessage()
    msg["From"] = settings.smtp_from
    msg["To"] = ke
    msg["Subject"] = subjek
    msg.set_content(teks)
    if html:
        msg.add_alternative(html, subtype="html")
    port = int(settings.smtp_port or 587)
    ctx = ssl.create_default_context()
    if port == 465:
        with smtplib.SMTP_SSL(settings.smtp_host, port, timeout=20, context=ctx) as s:
            if settings.smtp_user:
                s.login(settings.smtp_user, settings.smtp_pass)
            s.send_message(msg)
    else:
        with smtplib.SMTP(settings.smtp_host, port, timeout=20) as s:
            s.ehlo()
            s.starttls(context=ctx)
            s.ehlo()
            if settings.smtp_user:
                s.login(settings.smtp_user, settings.smtp_pass)
            s.send_message(msg)


# Identitas pengirim di badan email (template resmi dari tim, bukan nama akun
# pengundang: undangan datang atas nama perusahaan).
ORG = "PT Higo Fitur Indonesia"
TIM = "IoT Team"


def _nama_dari_email(email: str) -> str:
    """Tebak nama sapaan dari alamat email (bagian sebelum @), karena saat
    mengundang lewat email nama asli belum diketahui. "aditya.darma@higo.id"
    -> "Aditya Darma". Kosong kalau tak masuk akal."""
    lokal = str(email or "").split("@")[0]
    nama = re.sub(r"[._\-]+", " ", lokal).strip()
    return nama.title() if nama else ""


def _teks_undangan(nama: str, projek: str, tautan: str) -> str:
    sapa = f"Halo {nama}," if nama else "Halo,"
    return (
        f"{sapa}\n\n"
        "Selamat datang di HIGOLAB!\n\n"
        f"Anda telah diundang oleh {ORG} untuk bergabung dengan tim IoT pada "
        f"proyek \"{projek}\" melalui HIGOLAB, platform kolaborasi labeling "
        "kami.\n\n"
        "Untuk memulai, silakan klik tautan di bawah ini untuk mengakses "
        "undangan dan bergabung ke proyek:\n\n"
        f"Tautan Undangan: {tautan}\n\n"
        "Catatan Penting:\n"
        "HIGOLAB hanya dapat diakses melalui jaringan Wi-Fi kantor HIGO. "
        "Apabila Anda ingin mengakses platform dari luar jaringan kantor HIGO, "
        "pastikan untuk mengaktifkan VPN perusahaan terlebih dahulu.\n\n"
        "Jika Anda memiliki pertanyaan atau membutuhkan bantuan lebih lanjut, "
        "silakan balas email ini. Kami akan dengan senang hati membantu Anda.\n\n"
        "Kami menantikan kolaborasi yang baik dan menyenangkan bersama Anda di "
        "HIGOLAB!\n\n"
        f"Salam hangat,\n\n{TIM}\n{ORG}\n")


def _html_undangan(nama: str, projek: str, tautan: str) -> str:
    t = _esc(tautan)
    sapa = f"Halo {_esc(nama)}," if nama else "Halo,"
    return (
        '<div style="font-family:Arial,Helvetica,sans-serif;font-size:14px;'
        'color:#1f2937;line-height:1.6;max-width:560px">'
        f"<p>{sapa}</p>"
        "<p><b>Selamat datang di HIGOLAB!</b></p>"
        f"<p>Anda telah diundang oleh <b>{_esc(ORG)}</b> untuk bergabung dengan "
        f"tim IoT pada proyek <b>{_esc(projek)}</b> melalui HIGOLAB, platform "
        "kolaborasi labeling kami.</p>"
        "<p>Untuk memulai, silakan klik tautan di bawah ini untuk mengakses "
        "undangan dan bergabung ke proyek:</p>"
        f'<p><a href="{t}" style="display:inline-block;padding:10px 18px;'
        'background:#1f8a70;color:#ffffff;text-decoration:none;border-radius:6px">'
        "Buka undangan &amp; bergabung</a></p>"
        f'<p style="color:#667085;font-size:12px">Atau salin tautan ini: {t}</p>'
        "<p><b>Catatan Penting:</b><br>HIGOLAB hanya dapat diakses melalui "
        "jaringan Wi-Fi kantor HIGO. Apabila Anda ingin mengakses platform dari "
        "luar jaringan kantor HIGO, pastikan untuk mengaktifkan VPN perusahaan "
        "terlebih dahulu.</p>"
        "<p>Jika Anda memiliki pertanyaan atau membutuhkan bantuan lebih lanjut, "
        "silakan balas email ini. Kami akan dengan senang hati membantu Anda.</p>"
        "<p>Kami menantikan kolaborasi yang baik dan menyenangkan bersama Anda "
        "di HIGOLAB!</p>"
        f"<p>Salam hangat,<br><br><b>{_esc(TIM)}</b><br>{_esc(ORG)}</p>"
        "</div>")


def kirim_undangan(settings, ke: str, tautan: str, projek: str) -> bool:
    """Kirim email undangan projek (template resmi tim, Bahasa Indonesia). True
    kalau terkirim; False (dan tercatat) kalau gagal -- link tetap dikembalikan
    ke pemanggil jadi undangan tak hilang.
    """
    nama = _nama_dari_email(ke)
    subjek = f'Undangan proyek "{projek}" di HIGOLAB'
    try:
        kirim(settings, ke, subjek, _teks_undangan(nama, projek, tautan),
              _html_undangan(nama, projek, tautan))
        return True
    except Exception as e:                            # noqa: BLE001
        log.warning("gagal kirim email undangan ke %s: %s", ke, e)
        return False


def _teks_ditambahkan(nama, projek, tautan):
    sapa = f"Halo {nama}," if nama else "Halo,"
    ekor = f"\nBuka HIGOLAB: {tautan}\n" if tautan else ""
    return (
        f"{sapa}\n\n"
        f"Anda baru saja ditambahkan sebagai anggota proyek \"{projek}\" di "
        "HIGOLAB oleh tim.\n\n"
        "Akun Anda sudah terdaftar, jadi tak perlu mendaftar lagi: cukup masuk, "
        "dan proyek ini akan muncul di daftar \"Projek yang dibagikan\".\n"
        f"{ekor}\n"
        "Catatan: HIGOLAB hanya bisa diakses lewat jaringan Wi-Fi kantor HIGO "
        "(atau VPN perusahaan).\n\n"
        f"Salam,\n{TIM}\n{ORG}\n")


def _html_ditambahkan(nama, projek, tautan):
    sapa = f"Halo {_esc(nama)}," if nama else "Halo,"
    tombol = (
        f'<p><a href="{_esc(tautan)}" style="display:inline-block;padding:10px 18px;'
        'background:#1f8a70;color:#ffffff;text-decoration:none;border-radius:6px">'
        "Buka HIGOLAB</a></p>" if tautan else "")
    return (
        '<div style="font-family:Arial,Helvetica,sans-serif;font-size:14px;'
        'color:#1f2937;line-height:1.6;max-width:560px">'
        f"<p>{sapa}</p>"
        f"<p>Anda baru saja ditambahkan sebagai anggota proyek "
        f"<b>{_esc(projek)}</b> di HIGOLAB oleh tim.</p>"
        "<p>Akun Anda sudah terdaftar, jadi tak perlu mendaftar lagi: cukup "
        "masuk, dan proyek ini akan muncul di daftar "
        "<b>Projek yang dibagikan</b>.</p>"
        f"{tombol}"
        '<p style="color:#667085;font-size:12px">HIGOLAB hanya bisa diakses lewat '
        "jaringan Wi-Fi kantor HIGO (atau VPN perusahaan).</p>"
        f"<p>Salam,<br><br><b>{_esc(TIM)}</b><br>{_esc(ORG)}</p>"
        "</div>")


def kirim_ditambahkan(settings, ke: str, projek: str, tautan: str = "") -> bool:
    """Email ke anggota yang AKUNNYA SUDAH ADA, baru ditambahkan ke sebuah
    proyek. Bukan tautan undangan (tak perlu daftar/terima), hanya pemberitahuan
    supaya ia tahu ada proyek baru yang dibagikan. True kalau terkirim."""
    nama = _nama_dari_email(ke)
    subjek = f'Anda ditambahkan ke proyek "{projek}" di HIGOLAB'
    try:
        kirim(settings, ke, subjek, _teks_ditambahkan(nama, projek, tautan),
              _html_ditambahkan(nama, projek, tautan))
        return True
    except Exception as e:                            # noqa: BLE001
        log.warning("gagal kirim email 'ditambahkan' ke %s: %s", ke, e)
        return False


def _frasa_tugas(jumlah, judul, satuan="gambar"):
    """"5 gambar (Batch pagi)" atau sekadar "5 gambar" kalau tak berjudul."""
    inti = f"{jumlah} {satuan}" if jumlah else satuan
    return f"{inti} ({judul})" if judul else inti


def _teks_ditugaskan(nama, projek, jumlah, judul, tautan, satuan="gambar"):
    sapa = f"Halo {nama}," if nama else "Halo,"
    ekor = f"\nBuka HIGOLAB: {tautan}\n" if tautan else ""
    return (
        f"{sapa}\n\n"
        f"Anda baru saja mendapat tugas melabeli "
        f"{_frasa_tugas(jumlah, judul, satuan)} "
        f"di proyek \"{projek}\" di HIGOLAB.\n\n"
        "Masuk ke HIGOLAB, buka proyek ini, dan tugas Anda akan muncul di daftar "
        "pekerjaan.\n"
        f"{ekor}\n"
        "Catatan: HIGOLAB hanya bisa diakses lewat jaringan Wi-Fi kantor HIGO "
        "(atau VPN perusahaan).\n\n"
        f"Salam,\n{TIM}\n{ORG}\n")


def _html_ditugaskan(nama, projek, jumlah, judul, tautan, satuan="gambar"):
    sapa = f"Halo {_esc(nama)}," if nama else "Halo,"
    tombol = (
        f'<p><a href="{_esc(tautan)}" style="display:inline-block;padding:10px 18px;'
        'background:#1f8a70;color:#ffffff;text-decoration:none;border-radius:6px">'
        "Buka HIGOLAB</a></p>" if tautan else "")
    return (
        '<div style="font-family:Arial,Helvetica,sans-serif;font-size:14px;'
        'color:#1f2937;line-height:1.6;max-width:560px">'
        f"<p>{sapa}</p>"
        f"<p>Anda baru saja mendapat tugas melabeli "
        f"<b>{_esc(_frasa_tugas(jumlah, judul, satuan))}</b> di proyek "
        f"<b>{_esc(projek)}</b> di HIGOLAB.</p>"
        "<p>Masuk ke HIGOLAB, buka proyek ini, dan tugas Anda akan muncul di "
        "daftar pekerjaan.</p>"
        f"{tombol}"
        '<p style="color:#667085;font-size:12px">HIGOLAB hanya bisa diakses lewat '
        "jaringan Wi-Fi kantor HIGO (atau VPN perusahaan).</p>"
        f"<p>Salam,<br><br><b>{_esc(TIM)}</b><br>{_esc(ORG)}</p>"
        "</div>")


def kirim_ditugaskan(settings, ke: str, projek: str, jumlah: int = 0,
                     judul: str = "", tautan: str = "",
                     satuan: str = "gambar") -> bool:
    """Email ke pelabel saat sebuah batch/job BARU ditugaskan kepadanya, supaya
    ia tahu ada pekerjaan baru tanpa harus membuka web dulu. `satuan` = "gambar"
    atau "klip". True kalau terkirim."""
    nama = _nama_dari_email(ke)
    subjek = f'Tugas baru di proyek "{projek}" di HIGOLAB'
    try:
        kirim(settings, ke, subjek,
              _teks_ditugaskan(nama, projek, jumlah, judul, tautan, satuan),
              _html_ditugaskan(nama, projek, jumlah, judul, tautan, satuan))
        return True
    except Exception as e:                            # noqa: BLE001
        log.warning("gagal kirim email 'ditugaskan' ke %s: %s", ke, e)
        return False


# ------------------------------------------------------------ hasil training
# Label & kalimat untuk tiap keadaan akhir training. Dipakai menyusun subjek
# yang langsung kelihatan di kotak masuk tanpa membuka email.
_LABEL_LATIH = {
    "selesai": ("BERHASIL",   "selesai dengan berhasil"),
    "gagal":   ("GAGAL",      "berhenti karena gagal"),
    "batal":   ("DIBATALKAN", "dibatalkan"),
    "hilang":  ("TERPUTUS",   "terputus karena prosesnya berhenti mendadak"),
}


def _teks_hasil(nama_sapa, projek, nama, keadaan, catatan, rincian, tautan):
    label, kalimat = _LABEL_LATIH.get(keadaan, (keadaan.upper(), keadaan))
    sapa = f"Halo {nama_sapa}," if nama_sapa else "Halo,"
    baris = [
        f"Projek    : {projek}",
        f"Training  : {nama}",
        f"Status    : {label}",
    ]
    # Catatan hanya dimunculkan kalau benar-benar ada isinya (permintaan tim:
    # kalau kosong, jangan ada baris "Catatan ..." sama sekali).
    if catatan:
        baris.append(f"Catatan   : {catatan}")
    if rincian:
        baris.append(f"{'Hasil' if keadaan == 'selesai' else 'Penyebab':<9} : {rincian}")
    isi = "\n".join(baris)
    ekor = f"\nBuka HIGOLAB: {tautan}\n" if tautan else ""
    return (
        f"{sapa}\n\n"
        f"Training di HIGOLAB sudah {kalimat}.\n\n"
        f"{isi}\n"
        f"{ekor}\n"
        f"Email ini otomatis dari HIGOLAB.\n\n"
        f"Salam,\n{TIM}\n{ORG}\n")


def _html_hasil(nama_sapa, projek, nama, keadaan, catatan, rincian, tautan):
    label, kalimat = _LABEL_LATIH.get(keadaan, (keadaan.upper(), keadaan))
    warna = "#1f8a70" if keadaan == "selesai" else (
        "#b42318" if keadaan in ("gagal", "hilang") else "#b54708")
    sapa = f"Halo {_esc(nama_sapa)}," if nama_sapa else "Halo,"
    baris = [("Projek", projek), ("Training", nama)]
    if catatan:
        baris.append(("Catatan", catatan))
    if rincian:
        baris.append(("Hasil" if keadaan == "selesai" else "Penyebab", rincian))
    sel = "".join(
        f'<tr><td style="padding:3px 12px 3px 0;color:#667085;'
        f'vertical-align:top">{_esc(k)}</td>'
        f'<td style="padding:3px 0;color:#1f2937">{_esc(v)}</td></tr>'
        for k, v in baris)
    tombol = (
        f'<p><a href="{_esc(tautan)}" style="display:inline-block;padding:10px 18px;'
        'background:#1f8a70;color:#ffffff;text-decoration:none;border-radius:6px">'
        "Buka HIGOLAB</a></p>" if tautan else "")
    return (
        '<div style="font-family:Arial,Helvetica,sans-serif;font-size:14px;'
        'color:#1f2937;line-height:1.6;max-width:560px">'
        f"<p>{sapa}</p>"
        f"<p>Training di HIGOLAB sudah {_esc(kalimat)}.</p>"
        f'<p><span style="display:inline-block;padding:3px 10px;border-radius:5px;'
        f'background:{warna};color:#fff;font-weight:700;font-size:12px">{label}</span></p>'
        f'<table style="border-collapse:collapse;font-size:14px">{sel}</table>'
        f"{tombol}"
        '<p style="color:#98a2b3;font-size:12px">Email ini otomatis dari HIGOLAB.</p>'
        f"<p>Salam,<br><br><b>{_esc(TIM)}</b><br>{_esc(ORG)}</p>"
        "</div>")


def kirim_hasil_latih(settings, ke: str, projek: str, nama: str, keadaan: str,
                      catatan: str = "", rincian: str = "", tautan: str = "") -> bool:
    """Kirim email hasil satu training ke pemiliknya. True kalau terkirim; False
    (dan tercatat) kalau gagal -- kegagalan email tak boleh merusak apa pun di
    pemanggil, jadi tidak pernah melempar."""
    label = _LABEL_LATIH.get(keadaan, (keadaan.upper(),))[0]
    subjek = f'[HIGOLAB] Training "{nama}" {label} ({projek})'
    sapa = _nama_dari_email(ke)
    try:
        kirim(settings, ke, subjek,
              _teks_hasil(sapa, projek, nama, keadaan, catatan, rincian, tautan),
              _html_hasil(sapa, projek, nama, keadaan, catatan, rincian, tautan))
        return True
    except Exception as e:                            # noqa: BLE001
        log.warning("gagal kirim email hasil training ke %s: %s", ke, e)
        return False
