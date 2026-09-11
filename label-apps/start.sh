#!/usr/bin/env bash
#
# Nyalakan HIGOLAB (papan periksa anotasi).
#
#   ./start.sh              mode dev (bawaan) — localhost, muat ulang otomatis
#   ./start.sh dev
#   ./start.sh prod         mode produksi — dipakai tim
#
# Setelan tiap mode ada di env/dev.env dan env/prod.env. Argumen tambahan
# diteruskan ke run.py, mis:  ./start.sh prod --open-mode dir
#
# dev BAWAAN dan prod harus diminta, bukan sebaliknya: menyalakan produksi
# adalah tindakan yang perlu disengaja.
#
# Ctrl+C untuk berhenti. Supaya tetap hidup setelah SSH ditutup:
#   tmux new -s label   lalu   ./start.sh prod   lalu Ctrl+B D
#
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"

MODE="dev"
PASS=()
while (($#)); do
  case "$1" in
    dev|prod) MODE="$1" ;;
    -h|--help) sed -n '3,16p' "$0" | sed 's/^# \?//'; exit 0 ;;
    *) PASS+=("$1") ;;
  esac
  shift
done

BERKAS="env/${MODE}.env"
merah()  { printf '\033[31m%s\033[0m\n' "$1"; }
kuning() { printf '\033[33m%s\033[0m\n' "$1"; }

[[ -f "$BERKAS" ]] || { merah "  $BERKAS tidak ada."; exit 1; }

# Muat setelan mode ini. Nilai yang sudah ada di environment TIDAK ditimpa,
# supaya LABELAPP_PORT=9000 ./start.sh dev tetap berlaku (awalan LABELAPP_ wajib).
set -a
while IFS='=' read -r k v; do
  [[ "$k" =~ ^LABELAPP_ ]] || continue
  [[ -n "${!k-}" ]] || printf -v "$k" '%s' "$v"
  export "$k"
done < <(grep -E '^LABELAPP_' "$BERKAS")

# Kredensial yang tidak boleh masuk git dibaca dari berkas terpisah.
# env/prod.env ikut ter-commit; menaruh client secret di situ berarti
# mengunggahnya ke repositori.
if [[ -f env/rahasia.env ]]; then
  while IFS='=' read -r k v; do
    [[ "$k" =~ ^LABELAPP_ ]] || continue
    [[ -n "${!k-}" ]] || printf -v "$k" '%s' "$v"
    export "$k"
  done < <(grep -E '^LABELAPP_' env/rahasia.env)
fi
set +a

# ---------------------------------------------------------------- CPU / GPU
#
# DUA TATA LETAK yang sama-sama sah, dan yang menentukan ISI venv-nya, bukan
# namanya:
#
#   dua venv   .venv (tanpa torch) + .venv-gpu (torch + CUDA)
#              Dipakai di mesin pengembangan. Nilainya bukan kerapian
#              melainkan BUKTI: suite yang dijalankan di .venv membuktikan
#              aplikasi ini benar-benar hidup tanpa torch. Digabung, jaminan
#              itu hilang diam-diam — cukup satu `import torch` di tingkat
#              atas modul dan tidak ada yang tahu sampai ada pemasangan
#              tanpa torch.
#
#   satu venv  .venv berisi tumpukan GPU sekalian
#              Dipakai di server yang memang harus melatih. Terukur: satu
#              venv GPU menjalankan 863 tes dalam mode CPU tanpa satu pun
#              gagal — torch+cu130 jatuh ke CPU sendiri, dan onnxruntime-gpu
#              membawa CPUExecutionProvider. Ia juga membuat deploy.sh
#              menguji interpreter yang PERSIS dijalankan prod.
#
# Karena itu 'gpu' memilih .venv-gpu kalau ada, dan kalau tidak memakai .venv
# — lalu memeriksa venv itu benar-benar berisi torch. Kode aplikasinya satu
# dan sama; jalur mana yang dipakai augmentasi ditentukan services/olah_gpu.py
# yang membaca saklar yang sama.
OLAH="${LABELAPP_OLAH:-cpu}"
case "$OLAH" in
  cpu) VENV=".venv" ;;
  gpu) VENV=".venv-gpu"
       [[ -x ".venv-gpu/bin/python" ]] || VENV=".venv" ;;
  *)   merah "  LABELAPP_OLAH='$OLAH' tidak dikenal — isinya 'cpu' atau 'gpu'."
       exit 1 ;;
esac

pasang_gpu() {
  echo "    $1/bin/python -m pip install -r requirements.txt -r requirements-gpu.txt"
  echo "    $1/bin/python -m pip uninstall -y onnxruntime"
  echo "    $1/bin/python -m pip install --force-reinstall --no-deps onnxruntime-gpu"
}

[[ -x "$VENV/bin/python" ]] || {
  merah "  Virtualenv '$VENV' belum ada (LABELAPP_OLAH=$OLAH)."
  echo  "    python3 -m venv $VENV"
  if [[ "$OLAH" == "gpu" ]]; then pasang_gpu "$VENV"
  else echo "    $VENV/bin/python -m pip install -r requirements.txt"; fi
  exit 1
}

# Yang diperiksa KEMAMPUANNYA, bukan nama foldernya. Tidak ada jatuh diam-diam
# ke CPU: orang yang minta GPU lalu mendapat kecepatan CPU tanpa satu pun
# pesan akan mengira GPU-nya yang lambat, dan mencari masalah di tempat yang
# salah. Dulu yang dipakai keberadaan folder '.venv-gpu' — itu menjawab
# pertanyaan yang salah, karena folder bisa ada tetapi isinya belum lengkap.
if [[ "$OLAH" == "gpu" ]]; then
  KURANG=()
  for paket in torch ultralytics; do
    compgen -G "$VENV/lib/python*/site-packages/$paket" >/dev/null || KURANG+=("$paket")
  done
  if (( ${#KURANG[@]} )); then
    merah "  '$VENV' belum berisi ${KURANG[*]} (LABELAPP_OLAH=gpu)."
    echo  "  Lengkapi venv ini:"
    pasang_gpu "$VENV"
    echo  "  Atau jalankan mode CPU: LABELAPP_OLAH=cpu"
    exit 1
  fi
fi

PORT="${LABELAPP_PORT:-8042}"
HOST="${LABELAPP_HOST:-127.0.0.1}"
USERS="${LABELAPP_USERS_FILE:-users.json}"

if [[ ! -s "$USERS" ]]; then
  merah "  Belum ada akun di $USERS (mode $MODE)."
  echo  "  Buat dulu:"
  echo  "    LABELAPP_USERS_FILE=$USERS $VENV/bin/python run.py --users $USERS --adduser <nama>"
  exit 1
fi

if [[ ! -d "${LABELAPP_DATASETS_ROOT:-}" ]]; then
  kuning "  Folder dataset tidak ada: ${LABELAPP_DATASETS_ROOT:-(kosong)}"
  if [[ "$MODE" == "dev" && -n "${LABELAPP_DATASETS_ROOT:-}" ]]; then
    mkdir -p "$LABELAPP_DATASETS_ROOT"
    kuning "  dibuatkan untuk dev."
  fi
fi

AKUN=$("$VENV"/bin/python run.py --users "$USERS" --list-users 2>/dev/null \
       | tail -n +2 | awk '{print $1}' | paste -sd' ')
IP=$(ip -4 -o addr show scope global 2>/dev/null | awk '{print $4}' | cut -d/ -f1 | head -1)

printf '\n  \033[1mmode %s\033[0m  ·  %s\n' "$MODE" "$BERKAS"
echo "  Akun    : ${AKUN:-(tidak terbaca)}   <- dari $USERS"
echo "  Dataset : ${LABELAPP_DATASETS_ROOT:-(tidak diisi)}"
if [[ "$HOST" == "0.0.0.0" ]]; then
  echo "  Alamat  : http://${IP:-<ip-mesin-ini>}:$PORT"
  echo "  Firewall: pastikan $PORT diizinkan  ->  sudo ufw status | grep $PORT"
else
  echo "  Alamat  : http://127.0.0.1:$PORT   (hanya mesin ini)"
fi

ARGS=(--host "$HOST" --port "$PORT" --users "$USERS")
# Bentuk `if`, bukan `[[ ... ]] && ...`: dengan set -e, baris && yang kondisinya
# salah membuat skrip berhenti tanpa pesan — dan itu justru terjadi pada mode
# prod, yang paling tidak boleh gagal diam-diam.
if [[ -n "${LABELAPP_DATASETS_ROOT:-}" ]]; then
  ARGS+=(--datasets-root "$LABELAPP_DATASETS_ROOT")
fi
if [[ -n "${LABELAPP_UPLOADS_ROOT:-}" ]]; then
  ARGS+=(--uploads-root "$LABELAPP_UPLOADS_ROOT")
fi
# Muat ulang otomatis hanya di dev: di produksi restart mendadak berarti semua
# orang kehilangan sesinya di tengah pekerjaan.
if [[ "$MODE" == "dev" ]]; then
  ARGS+=(--reload)
fi

exec "$VENV"/bin/python run.py "${ARGS[@]}" ${PASS[@]+"${PASS[@]}"}
