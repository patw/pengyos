#!/usr/bin/env bash
# PengyOS — launch the newest disk image under QEMU + OVMF (UEFI).
#
#   run-qemu-disk.sh [IMAGES_DIR | IMAGE.img]
#
# With no argument it looks at every image pengyos-build has produced
# ($PENGYOS_BUILD_ROOT/output/*/images/pengyos*.img, default ~/build/pengyos) and
# boots the newest; with a directory, the newest pengyos*.img in it.
#
# If the directory holds MORE THAN ONE image, a picker (zenity/yad) is shown so you
# can choose which to boot; a single image boots straight through with no prompt.
#
# Non-destructive by default (snapshot=on) — the image file is never modified.
#
# Layout: GPT + EFI System Partition (GRUB2) + ext4 root; boots by PARTUUID, so
# we just hand the whole .img to QEMU as one virtio disk.
#
# Env overrides:
#   PENGYOS_RW=1         boot read-write (persist changes into the image)
#   PENGYOS_RAM=2048     guest RAM in MB          (default 1024)
#   PENGYOS_SMP=4        guest vCPUs              (default 2)
#   PENGYOS_DISPLAY=gtk  display backend          (default gtk; sdl/none ok)
#   PENGYOS_VGA=virtio   emulated GPU             (default virtio; see USE-CASES US-DSK-15)
#   PENGYOS_DRYRUN=1     print the QEMU command and exit (no boot)
set -euo pipefail

die() {
  echo "[pengyos] $*" >&2
  command -v notify-send >/dev/null 2>&1 && \
    notify-send -a PengyOS -u critical "PengyOS launcher" "$*" 2>/dev/null || true
  exit 1
}

BUILD_ROOT="${PENGYOS_BUILD_ROOT:-$HOME/build/pengyos}"
TARGET="${1:-$BUILD_ROOT/output}"

# --- picker (only used when several images exist) ---------------------------
have_picker() {
  [ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ] || return 1
  command -v zenity >/dev/null 2>&1 || command -v yad >/dev/null 2>&1
}

pick_image() {                     # $@ = candidate paths, newest first
  local picker; picker="$(command -v zenity 2>/dev/null || command -v yad 2>/dev/null)"
  local args=( --title="PengyOS — choose an image" \
    --text="Multiple PengyOS images in:\n$TARGET\n\nBoot which one?" \
    --column="Image" --column="Modified" --column="Size" )
  local p
  for p in "$@"; do
    args+=( "${p#"$TARGET"/}" \
            "$(date -r "$p" '+%Y-%m-%d %H:%M' 2>/dev/null)" \
            "$(du -h -- "$p" 2>/dev/null | cut -f1)" )
  done
  "$picker" --list "${args[@]}" --width=620 --height=380 2>/dev/null
}

# --- resolve the image ------------------------------------------------------
IMG=""
if [ -f "$TARGET" ]; then
  IMG="$TARGET"
else
  [ -d "$TARGET" ] || die "no such image or directory: $TARGET"
  shopt -s nullglob
  cand=("$TARGET"/pengyos*.img)
  [ "${#cand[@]}" -gt 0 ] || cand=("$TARGET"/*/images/pengyos*.img)
  [ "${#cand[@]}" -gt 0 ] || cand=("$TARGET"/*.img)
  shopt -u nullglob
  [ "${#cand[@]}" -gt 0 ] || die "no *.img found in $TARGET"

  mapfile -t sorted < <(ls -1t -- "${cand[@]}")   # newest first

  if [ "${#sorted[@]}" -eq 1 ]; then
    IMG="${sorted[0]}"
  elif have_picker; then
    sel="$(pick_image "${sorted[@]}")" || true     # zenity exits 1 on cancel
    [ -n "${sel:-}" ] || { echo "[pengyos] no image selected (cancelled)." >&2; exit 0; }
    IMG="$TARGET/$sel"
  else
    IMG="${sorted[0]}"
    echo "[pengyos] ${#sorted[@]} images, no picker available; booting newest: $(basename -- "$IMG")" >&2
  fi
fi
[ -r "$IMG" ] || die "image not readable: $IMG"

# --- firmware (OVMF): writable VARS copy, read-only CODE ---------------------
FW=(); VARS=""
if [ -f /usr/share/OVMF/OVMF_CODE_4M.fd ] && [ -f /usr/share/OVMF/OVMF_VARS_4M.fd ]; then
  VARS="$(mktemp "${TMPDIR:-/tmp}/pengyos-OVMF_VARS.XXXXXX.fd")"
  cp -f /usr/share/OVMF/OVMF_VARS_4M.fd "$VARS"
  FW=(-drive if=pflash,format=raw,readonly=on,file=/usr/share/OVMF/OVMF_CODE_4M.fd
      -drive if=pflash,format=raw,file="$VARS")
elif [ -f /usr/share/ovmf/OVMF.fd ]; then
  FW=(-bios /usr/share/ovmf/OVMF.fd)
else
  die "no OVMF UEFI firmware found (/usr/share/OVMF/ or /usr/share/ovmf/)"
fi

# --- machine + accelerator (KVM if available, else TCG) ----------------------
MACHINE=(-machine q35)
if [ -w /dev/kvm ]; then MACHINE+=(-accel kvm -cpu host); else MACHINE+=(-cpu max); fi

# --- disk (snapshot unless PENGYOS_RW=1) ------------------------------------
if [ "${PENGYOS_RW:-0}" = "1" ]; then
  DISK=(-drive "file=$IMG,if=virtio,format=raw")
else
  DISK=(-drive "file=$IMG,if=virtio,format=raw,snapshot=on")
fi

RAM="${PENGYOS_RAM:-1024}"; SMP="${PENGYOS_SMP:-2}"
# -vga std corrupts under QEMU: the emulated VGA's mode registers disagree with
# the GOP framebuffer (USE-CASES.md US-DSK-15, "test with ramfb"). virtio-gpu is
# built into the kernel (CONFIG_DRM_VIRTIO_GPU=y) and gives a clean virtio_gpudrmfb.
DISP="${PENGYOS_DISPLAY:-gtk}"; VGA="${PENGYOS_VGA:-virtio}"

CMD=(qemu-system-x86_64
  -name "PengyOS — $(basename "$IMG")"
  "${MACHINE[@]}" -m "$RAM" -smp "$SMP"
  "${FW[@]}"
  "${DISK[@]}"
  -netdev user,id=n0 -device virtio-net-pci,netdev=n0
  -vga "$VGA" -display "$DISP"
  -serial vc          # ttyS0 as VC2 (Ctrl+Alt+2) — always-working fallback
  -no-reboot)

echo "[pengyos] image:   $IMG"
echo "[pengyos] cmdline: ${CMD[*]}"
[ "${PENGYOS_DRYRUN:-0}" = "1" ] && exit 0

cleanup() { [ -n "$VARS" ] && rm -f "$VARS"; }
trap cleanup EXIT
"${CMD[@]}"
