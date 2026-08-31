#!/usr/bin/env bash
#
# Hadirkan projek prod di dev. Dua cara, dan keduanya bisa ditukar kapan saja.
#
#   ./sinkron-dev.sh             SALIN  — potret hard link, dev terpisah dari prod
#   ./sinkron-dev.sh --tautan    TAUTAN — dev dan prod berbagi folder yang sama
#   ./sinkron-dev.sh --lepas     lepas tautan, kembali ke salinan
#   ./sinkron-dev.sh --lihat     keadaan sekarang, tanpa mengubah apa pun
#   ./sinkron-dev.sh darma       batasi ke satu akun   (-y = tanpa bertanya)
#
# MANA YANG DIPAKAI KAPAN
# -----------------------
# SALIN untuk mengerjakan services/projek.py, ekspor, atau apa pun yang
# menghapus dan memindahkan berkas: kesalahan di dev berhenti di dev.
#
# TAUTAN untuk mengerjakan tata letak dan fitur: apa yang kamu lihat di dev
# persis apa yang ada di prod, tanpa perlu menyinkronkan lagi. Harganya nyata —
# buang, gabung, dan ganti nama di dev mengenai berkas prod SUNGGUHAN. Yang
# tidak ada jalan pulangnya adalah gabung; ke_sampah masih memindah, bukan
# menghapus.
#
# KENAPA SALINANNYA TIDAK MEMAKAN RUANG
# -------------------------------------
# Yang dibuat hard link, bukan salinan isi: 3 GB projek menambah pemakaian
# disk sekitar 2 MB. Itu aman karena setiap penulisan di aplikasi ini lewat
# berkas sementara lalu diganti namanya (annotations.tulis_aman, annotate.py,
# scanner.py), dan mengganti nama memutus tautannya — berkas baru lahir di sisi
# dev, berkas prod tidak tersentuh.
#
# Karena itu SALIN adalah potret, bukan cermin. Jalankan lagi supaya dev
# menyusul isi prod terbaru.
set -euo pipefail
cd "$(dirname "$0")"

MODE=salin; LIHAT=0; TANYA=1; AKUN=""
for a in "$@"; do
  case "$a" in
    --tautan)  MODE=tautan ;;
    --lepas)   MODE=lepas ;;
    --lihat)   LIHAT=1 ;;
    -y|--ya)   TANYA=0 ;;
    -h|--help) sed -n '3,10p' "$0" | sed 's/^# \?//'; exit 0 ;;
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

keadaan() {  # keadaan folder dev satu akun
  local d="$TUJUAN/$1"
  if   [[ -L "$d" ]];                     then echo "TAUTAN ke $(readlink "$d")"
  elif [[ -d "$d" && -n "$(ls -A "$d")" ]]; then echo "salinan, $(find "$d" -type f | wc -l) berkas"
  else echo "kosong"; fi
}

echo "  Prod : $SUMBER"
echo "  Dev  : $TUJUAN"
echo
for n in "${DAFTAR[@]}"; do
  printf "  %-10s prod: %-5s projek   dev: %s\n" "$n" \
         "$(find "$SUMBER/$n" -mindepth 1 -maxdepth 1 -type d 2>/dev/null | wc -l)" "$(keadaan "$n")"
done
echo
[[ "$LIHAT" == 1 ]] && exit 0

tanya() {
  [[ "$TANYA" == 0 ]] && return 0
  read -rp "  $1 Ketik 'ya' untuk lanjut: " j
  [[ "$j" == "ya" ]] || { echo "  Dibatalkan."; exit 1; }
}

# ------------------------------------------------------------------ TAUTAN
if [[ "$MODE" == tautan ]]; then
  kuning "  Setelah ini, buang/gabung/ganti nama di dev mengenai berkas prod SUNGGUHAN."
  tanya "Pasang tautan untuk: ${DAFTAR[*]}."
  for n in "${DAFTAR[@]}"; do
    d="$TUJUAN/$n"
    if [[ -L "$d" ]]; then
      rm "$d"
    elif [[ -d "$d" ]]; then
      # Salinan lama DISINGKIRKAN, bukan dihapus. Isinya cuma hard link, jadi
      # menyimpannya nyaris tidak memakan ruang — dan selagi dev menulis
      # langsung ke berkas prod, potret hari ini adalah jaring pengaman yang
      # paling murah yang bisa ada.
      cad="$d.salinan-$(date +%Y%m%d-%H%M%S)"
      mv "$d" "$cad"
      echo "  $n: salinan lama disingkirkan ke $(basename "$cad")"
    fi
    ln -sfn "$SUMBER/$n" "$d"
    echo "  $n -> $(readlink "$d")"
  done
  hijau "  Dev dan prod kini berbagi folder yang sama."
  echo  "  Kembali ke salinan terpisah:  ./sinkron-dev.sh --lepas"
  exit 0
fi

# ------------------------------------------------------------------ LEPAS
if [[ "$MODE" == lepas ]]; then
  for n in "${DAFTAR[@]}"; do
    d="$TUJUAN/$n"
    [[ -L "$d" ]] && { rm "$d"; echo "  $n: tautan dilepas"; }
  done
fi

# ------------------------------------------------------------------ SALIN
for n in "${DAFTAR[@]}"; do
  d="$TUJUAN/$n"
  # rsync --delete dengan sumber dan tujuan yang sebenarnya satu folder adalah
  # cara tercepat kehilangan data. Ini yang mencegahnya.
  if [[ -L "$d" ]]; then
    merah "  $n masih berupa TAUTAN ke prod; menyalin ke situ berarti menyalin"
    merah "  folder prod ke dirinya sendiri. Lepas dulu: ./sinkron-dev.sh --lepas"
    exit 1
  fi
done

ADA=0
for n in "${DAFTAR[@]}"; do
  [[ -d "$TUJUAN/$n" && -n "$(ls -A "$TUJUAN/$n" 2>/dev/null)" ]] && ADA=1
done
[[ "$ADA" == 1 ]] && kuning "  Dev dibuat PERSIS seperti prod: projek yang hanya ada di dev akan hilang."
tanya "Salin ${DAFTAR[*]} dari prod ke dev."

SEBELUM=$(df --output=used /home | tail -1)
for n in "${DAFTAR[@]}"; do
  s="$SUMBER/$n"
  [[ -d "$s" ]] || { kuning "  $n: tidak punya folder di prod, dilewati"; continue; }
  mkdir -p "$TUJUAN/$n"
  # --link-dest ke sumbernya sendiri: tiap berkas yang isinya sama dibuat
  # sebagai hard link, bukan disalin isinya.
  rsync -a --delete --link-dest="$s/" "$s/" "$TUJUAN/$n/"
  echo "  $n: $(find "$TUJUAN/$n" -type f | wc -l) berkas siap di dev"
done
SESUDAH=$(df --output=used /home | tail -1)
hijau "  Selesai. Tambahan pemakaian disk: $(( (SESUDAH - SEBELUM) / 1024 )) MB"
echo  "  Jalankan lagi kapan pun ingin dev menyusul isi prod terbaru."
