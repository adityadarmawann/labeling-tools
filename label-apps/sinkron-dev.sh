#!/usr/bin/env bash
#
# Salin projek dari prod ke dev, supaya akun yang sama melihat projek yang sama
# di kedua tempat.
#
#   ./sinkron-dev.sh              semua akun yang ada di dev DAN di prod
#   ./sinkron-dev.sh darma        satu akun saja
#   ./sinkron-dev.sh --lihat      tampilkan yang akan disalin, tanpa menyalin
#   ./sinkron-dev.sh -y           tanpa bertanya
#
# KENAPA MENYALIN, BUKAN MENUNJUK LANGSUNG KE FOLDER PROD
# -------------------------------------------------------
# Dev punya tombol yang benar-benar mengubah berkas: ganti nama, gandakan,
# gabungkan, buang. Kalau dev menunjuk langsung ke folder tim, satu percobaan
# yang salah — atau satu bug yang sedang kamu tulis — mengenai anotasi
# sungguhan. Itu justru keadaan yang dihindari sejak awal.
#
# KENAPA TIDAK MEMAKAN RUANG
# --------------------------
# Yang dibuat adalah HARD LINK, bukan salinan isi: dev dan prod menunjuk blok
# disk yang sama, jadi 3 GB projek hampir tidak menambah pemakaian disk sama
# sekali. Itu aman karena SETIAP penulisan di aplikasi ini lewat berkas
# sementara lalu diganti namanya (annotations.tulis_aman, annotate.py,
# scanner.py). Mengganti nama memutus tautannya: berkas baru lahir di sisi dev,
# dan berkas prod tidak tersentuh sedikit pun.
#
# Karena itu ia POTRET, bukan cermin hidup. Kalau tim menambah gambar di prod,
# jalankan lagi supaya dev menyusul.
set -euo pipefail
cd "$(dirname "$0")"

LIHAT=0; TANYA=1; AKUN=""
for a in "$@"; do
  case "$a" in
    --lihat)   LIHAT=1 ;;
    -y|--ya)   TANYA=0 ;;
    -h|--help) sed -n '3,9p' "$0" | sed 's/^# \?//'; exit 0 ;;
    -*) echo "  Argumen tidak dikenal: $a"; exit 2 ;;
    *)  AKUN="$a" ;;
  esac
done

merah()  { printf '\033[31m%s\033[0m\n' "$1"; }
hijau()  { printf '\033[32m%s\033[0m\n' "$1"; }
kuning() { printf '\033[33m%s\033[0m\n' "$1"; }

nilai() { grep -oP "^LABELAPP_$2=\K.*" "env/$1.env" | tail -1; }

SUMBER=$(realpath -m "$(nilai prod UPLOADS_ROOT)")
TUJUAN=$(realpath -m "$(nilai dev  UPLOADS_ROOT)")

[[ -d "$SUMBER" ]] || { merah "  Folder prod tidak ada: $SUMBER"; exit 1; }
# Tanpa penjagaan ini, satu salah ketik di env membuat rsync --delete berjalan
# DI DALAM folder tim.
if [[ "$TUJUAN" == "$SUMBER" || "$TUJUAN" == "$SUMBER"/* ]]; then
  merah "  Folder dev ($TUJUAN) ada di dalam folder prod. Dihentikan."
  exit 1
fi

# Akun yang dipakai: harus ada di kedua berkas akun. Menyalin projek untuk akun
# yang tidak bisa login di dev tidak ada gunanya.
punya_dev() {
  .venv/bin/python -c "import json,sys;print(sys.argv[1] in json.load(open('users.dev.json')))" "$1"
}

if [[ -n "$AKUN" ]]; then
  DAFTAR=("$AKUN")
else
  DAFTAR=()
  for d in "$SUMBER"/*/; do
    n=$(basename "$d")
    [[ "$(punya_dev "$n")" == "True" ]] && DAFTAR+=("$n")
  done
fi

if [[ ${#DAFTAR[@]} -eq 0 ]]; then
  kuning "  Tidak ada akun prod yang juga ada di users.dev.json."
  echo   "  Buat dulu:  .venv/bin/python run.py --users users.dev.json --adduser <nama>"
  exit 1
fi

echo "  Dari : $SUMBER"
echo "  Ke   : $TUJUAN"
echo "  Akun : ${DAFTAR[*]}"
echo
for n in "${DAFTAR[@]}"; do
  s="$SUMBER/$n"
  [[ -d "$s" ]] || { kuning "  $n: tidak punya folder di prod, dilewati"; continue; }
  echo "  $n"
  for p in "$s"/*/; do
    [[ -d "$p" ]] || continue
    printf "    %-42s %7s  %s berkas\n" "$(basename "$p")" \
           "$(du -sh "$p" | cut -f1)" "$(find "$p" -type f | wc -l)"
  done
done
echo

if [[ "$LIHAT" == 1 ]]; then exit 0; fi

ADA=0
for n in "${DAFTAR[@]}"; do
  [[ -d "$TUJUAN/$n" ]] && [[ -n "$(ls -A "$TUJUAN/$n" 2>/dev/null)" ]] && ADA=1
done
if [[ "$ADA" == 1 ]]; then
  kuning "  Folder dev untuk akun itu sudah berisi sesuatu."
  kuning "  Sinkron membuatnya PERSIS seperti prod: yang hanya ada di dev akan hilang."
fi
if [[ "$TANYA" == 1 ]]; then
  read -rp "  Ketik 'ya' untuk menyalin: " j
  [[ "$j" == "ya" ]] || { echo "  Dibatalkan."; exit 1; }
fi

SEBELUM=$(df --output=used /home | tail -1)
for n in "${DAFTAR[@]}"; do
  s="$SUMBER/$n"
  [[ -d "$s" ]] || continue
  mkdir -p "$TUJUAN/$n"
  # --link-dest ke sumbernya sendiri: tiap berkas yang isinya sama dibuat
  # sebagai hard link, bukan disalin isinya.
  rsync -a --delete --link-dest="$s/" "$s/" "$TUJUAN/$n/"
  echo "  $n: $(find "$TUJUAN/$n" -type f | wc -l) berkas siap di dev"
done
SESUDAH=$(df --output=used /home | tail -1)

hijau "  Selesai. Tambahan pemakaian disk: $(( (SESUDAH - SEBELUM) / 1024 )) MB"
echo  "  Jalankan lagi kapan pun ingin dev menyusul isi prod terbaru."
