#!/usr/bin/env bash
#
# Hadirkan projek prod di dev.
#
#   ./sinkron-dev.sh                                semua akun yang ada di keduanya
#   ./sinkron-dev.sh darma --ke darma-dev           salin ke akun dev bernama lain
#   ./sinkron-dev.sh darma --ke darma-dev --projek paragon
#   ./sinkron-dev.sh --tautan                       dev dan prod berbagi folder
#   ./sinkron-dev.sh --lepas                        lepas tautan, kembali menyalin
#   ./sinkron-dev.sh --lihat                        keadaan sekarang, tanpa mengubah
#   -y                                              tanpa bertanya
#
# DUA CARA, DAN PILIHLAH SESUAI YANG SEDANG DIKERJAKAN
# ----------------------------------------------------
# SALIN (bawaan) memberi dev berkasnya sendiri. Itu yang dipakai kalau dev
# dijadikan simulasi: apa pun yang dilakukan di sana — termasuk membuang dan
# menggabungkan projek — berhenti di dev.
#
# TAUTAN membuat folder akun dev menunjuk langsung ke folder prod, jadi apa
# yang terlihat di dev persis isi prod tanpa perlu menyinkronkan lagi. Harganya
# nyata: buang, gabung, dan ganti nama di dev mengenai berkas prod SUNGGUHAN.
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

MODE=salin; LIHAT=0; TANYA=1; AKUN=""; KE=""; PROJEK=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --tautan)  MODE=tautan ;;
    --lepas)   MODE=lepas ;;
    --lihat)   LIHAT=1 ;;
    -y|--ya)   TANYA=0 ;;
    --ke)      KE="${2:-}"; shift ;;
    --projek)  PROJEK="${2:-}"; shift ;;
    -h|--help) sed -n '3,11p' "$0" | sed 's/^# \?//'; exit 0 ;;
    -*) echo "  Argumen tidak dikenal: $1"; exit 2 ;;
    *)  AKUN="$1" ;;
  esac
  shift
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
if [[ -n "$KE" && -z "$AKUN" ]]; then
  merah "  --ke perlu tahu akun prod mana yang disalin. Contoh:"
  echo   "    ./sinkron-dev.sh darma --ke darma-dev"
  exit 2
fi

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

# Nama akun di sisi dev. Sengaja boleh berbeda: akun dev yang namanya sendiri
# tidak bisa tertukar dengan akun prod saat menguji, dan tidak ada yang salah
# kira sedang menyunting pekerjaan tim.
ke_akun() { [[ -n "$KE" ]] && echo "$KE" || echo "$1"; }

keadaan() {
  local d="$TUJUAN/$1"
  if   [[ -L "$d" ]];                       then echo "TAUTAN ke $(readlink "$d")"
  elif [[ -d "$d" && -n "$(ls -A "$d")" ]]; then
    echo "salinan, $(find "$d" -mindepth 1 -maxdepth 1 -type d | wc -l) projek, $(find "$d" -type f | wc -l) berkas"
  else echo "kosong"; fi
}

echo "  Prod : $SUMBER"
echo "  Dev  : $TUJUAN"
[[ -n "$PROJEK" ]] && echo "  Projek: $PROJEK (hanya ini)"
echo
for n in "${DAFTAR[@]}"; do
  t=$(ke_akun "$n")
  printf "  %-10s -> %-12s prod: %s projek   dev: %s\n" "$n" "$t" \
         "$(find "$SUMBER/$n" -mindepth 1 -maxdepth 1 -type d 2>/dev/null | wc -l)" \
         "$(keadaan "$t")"
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
  [[ -n "$PROJEK" ]] && { merah "  --tautan hanya bisa untuk seluruh akun."
    echo "  Menautkan SATU projek gagal separuh: projeknya muncul di daftar,"
    echo "  tetapi sampulnya 404 dan menu kelolanya ditolak, karena _didalam()"
    echo "  me-resolve kedua sisi dan projek itu resolve ke luar ruang kerja dev."
    exit 2; }
  kuning "  Setelah ini, buang/gabung/ganti nama di dev mengenai berkas prod SUNGGUHAN."
  tanya "Pasang tautan untuk: ${DAFTAR[*]}."
  for n in "${DAFTAR[@]}"; do
    d="$TUJUAN/$(ke_akun "$n")"
    if [[ -L "$d" ]]; then rm "$d"
    elif [[ -d "$d" ]]; then
      # Salinan lama DISINGKIRKAN, bukan dihapus. Isinya cuma hard link, jadi
      # menyimpannya nyaris tidak memakan ruang.
      cad="$d.salinan-$(date +%Y%m%d-%H%M%S)"; mv "$d" "$cad"
      echo "  $(basename "$d"): salinan lama disingkirkan ke $(basename "$cad")"
    fi
    ln -sfn "$SUMBER/$n" "$d"
    echo "  $(basename "$d") -> $(readlink "$d")"
  done
  hijau "  Dev dan prod kini berbagi folder yang sama."
  exit 0
fi

# ------------------------------------------------------------------ LEPAS
if [[ "$MODE" == lepas ]]; then
  for n in "${DAFTAR[@]}"; do
    d="$TUJUAN/$(ke_akun "$n")"
    [[ -L "$d" ]] && { rm "$d"; echo "  $(basename "$d"): tautan dilepas"; }
  done
fi

# ------------------------------------------------------------------ SALIN
for n in "${DAFTAR[@]}"; do
  d="$TUJUAN/$(ke_akun "$n")"
  # rsync --delete dengan sumber dan tujuan yang sebenarnya satu folder adalah
  # cara tercepat kehilangan data. Ini yang mencegahnya.
  if [[ -L "$d" ]]; then
    merah "  $(basename "$d") masih berupa TAUTAN ke prod; menyalin ke situ berarti"
    merah "  menyalin folder prod ke dirinya sendiri. Lepas dulu: --lepas"
    exit 1
  fi
done

if [[ -z "$PROJEK" ]]; then
  ADA=0
  for n in "${DAFTAR[@]}"; do
    d="$TUJUAN/$(ke_akun "$n")"
    [[ -d "$d" && -n "$(ls -A "$d" 2>/dev/null)" ]] && ADA=1
  done
  [[ "$ADA" == 1 ]] && kuning "  Dev dibuat PERSIS seperti prod: projek yang hanya ada di dev akan hilang."
fi
tanya "Salin ${DAFTAR[*]} dari prod ke dev."

SEBELUM=$(df --output=used /home | tail -1)
for n in "${DAFTAR[@]}"; do
  s="$SUMBER/$n"; d="$TUJUAN/$(ke_akun "$n")"
  [[ -d "$s" ]] || { kuning "  $n: tidak punya folder di prod, dilewati"; continue; }
  if [[ -n "$PROJEK" ]]; then
    [[ -d "$s/$PROJEK" ]] || { merah "  projek '$PROJEK' tidak ada di $s"; exit 1; }
    mkdir -p "$d/$PROJEK"
    # Tanpa --delete: menyalin SATU projek tidak boleh menghapus projek lain
    # yang sudah ada di dev.
    rsync -a --link-dest="$s/$PROJEK/" "$s/$PROJEK/" "$d/$PROJEK/"
    echo "  $(basename "$d")/$PROJEK: $(find "$d/$PROJEK" -type f | wc -l) berkas"
  else
    mkdir -p "$d"
    rsync -a --delete --link-dest="$s/" "$s/" "$d/"
    echo "  $(basename "$d"): $(find "$d" -type f | wc -l) berkas siap di dev"
  fi
done
SESUDAH=$(df --output=used /home | tail -1)
hijau "  Selesai. Tambahan pemakaian disk: $(( (SESUDAH - SEBELUM) / 1024 )) MB"
