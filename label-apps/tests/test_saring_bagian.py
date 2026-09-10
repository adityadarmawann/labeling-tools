"""
Saringan "Bagian": split (train/valid/test) dan asal gambar (asli/aug/bal).

Dataset yang diunggah dalam keadaan sudah dibelah dan sudah ber-aug-bal adalah
kasus yang paling sering dibawa masuk dari Roboflow, dan di sanalah gridnya
paling tidak bisa dibaca: satu foto sungguhan tenggelam di antara belasan
salinan hasil augmentasi, dan tidak ada satu pun cara untuk bertanya "yang
valid saja" atau "yang asli saja".

Dua saringan ini di satu menu karena keduanya menjawab pertanyaan yang sama:
bagian mana dari dataset ini yang sedang saya lihat.
"""
import cv2
import numpy as np
import pytest

from app.services import scanner


# ============================================================
# PENGGOLONG NAMA BERKAS
# ============================================================

@pytest.mark.parametrize("nama,diharap", [
    # Foto asli: tidak ada satu pun ruas turunan.
    ("botol-01.jpg", "asli"),
    ("IMG_20240517_093012.jpg", "asli"),
    ("sesi3_botol.jpg", "asli"),
    # Ruas PERTAMA tidak pernah diperiksa: yang menjadikan sebuah nama turunan
    # adalah ruas yang ditambahkan di belakangnya, bukan namanya sendiri.
    ("aug1.jpg", "asli"),
    ("bal2.jpg", "asli"),
    # Bentuk-bentuk yang benar-benar ditulis aug-bal-v14.py. Setiap baris di
    # bawah ini ada wujud nyatanya di keluaran 55.580 berkas yang dipakai
    # memeriksa penggolong ini.
    ("cup-720_jpg.rf.429a5032_aug1.jpg", "aug"),
    ("cup-720_jpg.rf.429a5032_swout0.jpg", "aug"),
    ("cup-720_jpg.rf.429a5032_swout1.jpg", "aug"),
    ("cup-720_jpg.rf.429a5032_swin2.jpg", "aug"),
    ("cup-720_jpg.rf.429a5032_p5crop_normal.jpg", "aug"),
    ("cup-720_jpg.rf.429a5032_p5zoom_normal.jpg", "aug"),
    ("cup-720_jpg.rf.429a5032_p5combo_normal.jpg", "aug"),
    # Fase 5, varian tambahan: ruas p5* yang membawa tandanya, bukan ekornya.
    ("cup-720_p5crop_ds.jpg", "aug"),
    ("cup-720_p5zoom_vgn.jpg", "aug"),
    ("cup-720_p5crop_fish.jpg", "aug"),
    # Fase 5B, penyeimbangan skala: _sc<c>b<bin>_<n>.
    ("cup-720_sc2b10_96.jpg", "aug"),
    ("cup-720_swout0_sc1b0_387.jpg", "aug"),
    # Fase 3 dan Fase 7: penyeimbangan kelas dan contoh negatif.
    ("cup-720_bal3_643.jpg", "bal"),
    ("cup-720_neg1.jpg", "bal"),
    # Berantai. Kalau salah satu fasenya penyeimbangan, jawabannya "bal":
    # fase itu yang menentukan berapa banyak salinan ini ada, dan itulah yang
    # dicari orang saat menyaringnya.
    ("cup-720_swout1_bal4_1080.jpg", "bal"),
    ("cup-720_swout1_sc1b2_11_bal4_774.jpg", "bal"),
    ("cup-720_swout0_sc1b0_207_bal3_49.jpg", "bal"),
    ("cup-720_bal1_aug2.jpg", "bal"),
])
def test_asal_gambar_dibaca_dari_ruas_nama(nama, diharap):
    assert scanner.jenis_turunan(nama) == diharap


@pytest.mark.parametrize("nama", [
    # Ini yang membuat penggolongnya tidak boleh memakai awalan. "sweater" dan
    # "swalayan" diawali "sw", "balkon" diawali "bal", "scan" diawali "sc",
    # "negatif" diawali "neg" — dan semuanya foto sungguhan yang akan hilang
    # dari saringan "asli" kalau ruasnya dicocokkan sepotong-sepotong.
    "foto_sweater.jpg",
    "meja_swalayan.jpg",
    "augustus_pagi.jpg",
    "rak_balkon.jpg",
    "sudut_negatif.jpg",
    "hasil_scan_2.jpg",
    "dus_augustus.jpg",
])
def test_kata_yang_kebetulan_mirip_akhiran_tetap_asli(nama):
    assert scanner.jenis_turunan(nama) == "asli"


# ============================================================
# SARINGAN DI GRID
# ============================================================

def _dataset_bersplit(akar):
    """Ekspor bergaya Roboflow: train/valid/test, masing-masing images+labels.

    Isinya sengaja tidak seimbang supaya tiap jawaban punya angka yang
    berbeda — kalau train, valid dan test sama banyak, saringan yang salah
    kolom pun akan lolos.
    """
    rencana = {
        "train": ["a1.jpg", "a2.jpg", "a1_aug1.jpg", "a1_aug2.jpg",
                  "a2_p5crop1.jpg", "a1_bal1.jpg", "meja_swalayan.jpg"],
        "valid": ["v1.jpg", "v1_aug1.jpg", "v2_neg1.jpg"],
        "test": ["t1.jpg", "t2.jpg", "augustus_pagi.jpg"],
    }
    for bagian, berkas in rencana.items():
        (akar / bagian / "images").mkdir(parents=True, exist_ok=True)
        (akar / bagian / "labels").mkdir(parents=True, exist_ok=True)
        for i, n in enumerate(berkas):
            im = np.full((48, 64, 3), (40 + i * 17) % 256, np.uint8)
            cv2.imwrite(str(akar / bagian / "images" / n), im)
            (akar / bagian / "labels" / (n.rsplit(".", 1)[0] + ".txt")
             ).write_text("0 0.5 0.5 0.3 0.3\n")
    (akar / "data.yaml").write_text("names:\n  0: botol\n")
    return akar


def _kartu(klien, kueri=""):
    return klien.get("/" + kueri).text.count('class="card"')


@pytest.fixture
def klien_bersplit(klien, lingkungan):
    from tests.test_data import masuk, PW_PAUL

    masuk(klien, "paul", PW_PAUL)
    src = _dataset_bersplit(lingkungan["ruang"] / "ds-split")
    r = klien.post(f"/setsrc?path={src}")
    assert r.json().get("ok"), r.text
    assert r.json()["n"] == 13
    # Dititipkan di kliennya supaya tes yang perlu menandai gambar tidak harus
    # menyusun ulang jalurnya sendiri.
    klien.src = src
    return klien


def test_menyaring_menurut_split(klien_bersplit):
    k = klien_bersplit
    assert _kartu(k) == 13
    assert _kartu(k, "?sp=train") == 7
    assert _kartu(k, "?sp=valid") == 3
    assert _kartu(k, "?sp=test") == 3
    # Beberapa centang berarti "punya salah satu": satu gambar cuma bisa
    # berada di satu split, jadi menuntut keduanya sekaligus selalu nol dan
    # tidak pernah itu yang dimaksud.
    assert _kartu(k, "?sp=valid&sp=test") == 6


def test_menyaring_menurut_asal_gambar(klien_bersplit):
    k = klien_bersplit
    # 13 gambar: 7 asli, 4 hasil augmentasi, 2 hasil penyeimbangan.
    assert _kartu(k, "?tu=asli") == 7
    assert _kartu(k, "?tu=aug") == 4
    assert _kartu(k, "?tu=bal") == 2
    assert _kartu(k, "?tu=aug&tu=bal") == 6


def test_split_dan_asal_dipakai_bersamaan(klien_bersplit):
    """Keduanya menyempit, bukan melebar.

    "train" saja 7 dan "aug" saja 4; bersama-sama hasilnya 3, bukan 11.
    """
    k = klien_bersplit
    assert _kartu(k, "?sp=train&tu=aug") == 3
    assert _kartu(k, "?sp=train&tu=asli") == 3
    assert _kartu(k, "?sp=valid&tu=bal") == 1
    assert _kartu(k, "?sp=test&tu=aug") == 0


def test_nilai_yang_tidak_dikenal_tidak_merusak_halaman(klien_bersplit):
    k = klien_bersplit
    # Split ngawur memang tidak cocok dengan apa pun — itu jawaban yang benar.
    assert _kartu(k, "?sp=ngawur") == 0
    # Asal ngawur DIABAIKAN, bukan dijadikan saringan kosong: hanya tiga nilai
    # yang punya arti, dan URL yang salah ketik tidak boleh memutuskan
    # halamannya menampilkan nol gambar tanpa keterangan apa pun.
    assert _kartu(k, "?tu=ngawur") == 13


def test_menu_bagian_dan_saringan_lain_hidup_bersama(klien_bersplit):
    """Menu Bagian tidak boleh membuang titipan saringan yang sudah berlaku."""
    h = klien_bersplit.get("/?sp=train").text
    assert 'id="menu-split"' in h
    assert 'name="sp" value="train"' in h
    assert 'name="tu" value="aug"' in h
    # Tombolnya menyala dan menyebutkan yang sedang dipilih.
    assert "data-on" in h.split('id="split-tombol"')[1][:80]
    # "Bersihkan saringan" menghitung Bagian juga; tanpa ini angkanya bohong
    # dan tautannya meninggalkan saringan yang katanya sudah dibersihkan.
    assert "bagian train" in h
    bersih = h.split('class="chip saring-bersih"')[1].split(">")[0]
    assert "sp=" not in bersih and "tu=" not in bersih, bersih


def test_menu_tag_tidak_membuang_saringan_bagian(klien_bersplit):
    """Mencentang sebuah tag tidak boleh mengembalikan grid ke seluruh split.

    Form di dalam menu Tag adalah GET biasa: apa yang tidak ia titipkan sebagai
    field tersembunyi akan hilang dari URL berikutnya. Sebelum ini ia menulis
    sendiri daftar titipannya, dan daftar itu ditulis sebelum saringan Bagian
    ada — jadi memilih "train" lalu mencentang satu tag diam-diam melebar lagi
    ke train+valid+test, tanpa satu pun tanda bahwa itu terjadi.
    """
    k = klien_bersplit
    berkas = sorted(str(p) for p in (k.src / "train" / "images").glob("*.jpg"))
    r = k.post("/api/tag/pasang", json={"paths": berkas[:2], "tambah": ["pagi"]})
    assert r.json().get("ok"), r.text

    isi_menu_tag = k.get("/?sp=train&tu=asli").text.split('id="tag-isi"')[1]
    isi_menu_tag = isi_menu_tag.split("</form>")[0]
    assert 'name="sp" value="train"' in isi_menu_tag
    assert 'name="tu" value="asli"' in isi_menu_tag
    # Sebaliknya, form Bagian tidak boleh membuang tag yang sedang berlaku.
    isi_menu_bagian = k.get("/?tg=pagi").text.split('id="split-isi"')[1]
    isi_menu_bagian = isi_menu_bagian.split("</form>")[0]
    assert 'name="tg" value="pagi"' in isi_menu_bagian


def test_menu_bagian_tidak_digambar_kalau_tidak_ada_yang_dipilih(klien,
                                                                 lingkungan):
    """Dataset labelme biasa tidak punya split dan tidak punya turunan.

    Menu kosong di sana hanya satu tombol lagi untuk dilewati.
    """
    from tests.test_data import masuk, PW_PAUL

    masuk(klien, "paul", PW_PAUL)
    klien.post(f"/setsrc?path={lingkungan['roots'] / 'ds-alpha'}")
    assert 'id="menu-split"' not in klien.get("/").text
