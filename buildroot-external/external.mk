################################################################################
# PengyOS external tree (BR2_EXTERNAL)
#
# We own the recipes. Packages we author live under package/<name>/<name>.mk
# and are included here automatically.
################################################################################

include $(sort $(wildcard $(BR2_EXTERNAL_PENGYOS_PATH)/package/*/*.mk))

################################################################################
# Overrides of stock Buildroot recipes (this file is included after them).
################################################################################

# crates.io sources (<name>-<version>.crate) are gzipped tarballs; Buildroot
# picks the decompressor from the file extension.
INFLATE.crate = $(ZCAT)

# The carried upstream fixes we declare to pkg-stats live in their own file, written by
# pengyos-nightly from the patches that actually survived pre-flight (see
# patches/README.md rule 3 and patches/cve-declarations.mk). Kept separate from this
# file so a human override and a generated declaration are not the same thing - and so
# a patch taken out of the tree takes its declaration with it. $(wildcard) so a tree
# with no carried patches still builds.
include $(wildcard $(BR2_EXTERNAL_PENGYOS_PATH)/patches/cve-declarations.mk)

# getent: glibc builds it, Buildroot never installs it (BR2_PACKAGE_GLIBC_UTILS
# is getconf + ldd only). Every distro has it; COSMIC Settings' Users page runs
# `getent passwd` to list accounts and showed nobody without it.
define PENGYOS_GLIBC_INSTALL_GETENT
	$(INSTALL) -D -m 0755 $(@D)/build/nss/getent $(TARGET_DIR)/usr/bin/getent
endef
# ldd likewise (a bash script glibc generates). Installers probe the C library
# with `ldd --version`; uv's install.sh found none and took a path that broke.
# Installed here rather than via BR2_PACKAGE_GLIBC_UTILS, which would rebuild
# glibc in every output tree for one script.
define PENGYOS_GLIBC_INSTALL_LDD
	$(INSTALL) -D -m 0755 $(@D)/build/elf/ldd $(TARGET_DIR)/usr/bin/ldd
endef
GLIBC_POST_INSTALL_TARGET_HOOKS += PENGYOS_GLIBC_INSTALL_LDD
GLIBC_POST_INSTALL_TARGET_HOOKS += PENGYOS_GLIBC_INSTALL_GETENT
# locale likewise. COSMIC Settings' Region & Language page lists the languages
# `locale -a` prints and the launcher's calculator asks `locale` for the decimal
# separator; with no binary the language list was empty (behoweather, 26.10.22).
define PENGYOS_GLIBC_INSTALL_LOCALE
	$(INSTALL) -D -m 0755 $(@D)/build/locale/locale $(TARGET_DIR)/usr/bin/locale
endef
GLIBC_POST_INSTALL_TARGET_HOOKS += PENGYOS_GLIBC_INSTALL_LOCALE
# ldconfig where the NVIDIA container toolkit is: the CDI spec's update-ldcache
# hook runs the host's /sbin/ldconfig inside the container (so a container gets
# the driver libraries in its ld cache), and fails without one.
ifeq ($(BR2_PACKAGE_NVIDIA_CONTAINER_TOOLKIT),y)
define PENGYOS_GLIBC_INSTALL_LDCONFIG
	$(INSTALL) -D -m 0755 $(@D)/build/elf/ldconfig $(TARGET_DIR)/usr/bin/ldconfig
endef
GLIBC_POST_INSTALL_TARGET_HOOKS += PENGYOS_GLIBC_INSTALL_LDCONFIG
endif

# podman: Buildroot builds only cmd/podman. Rootless containers on a
# user-defined network (podman network create, every podman-compose project)
# publish ports through the `rootlessport` helper, and without it `-p` failed
# with 'could not find "rootlessport"'. Build it with podman's own Go
# environment and put it where podman looks. (Not via PODMAN_BUILD_TARGETS:
# pkg-golang fixes the single-target binary name when podman.mk is evaluated,
# so a second target there yields `-o bin/podman rootlessport`.)
ifeq ($(BR2_PACKAGE_PODMAN),y)
define PENGYOS_PODMAN_BUILD_ROOTLESSPORT
	cd $(@D); $(HOST_GO_TARGET_ENV) $(PODMAN_GO_ENV) \
		$(GO_BIN) build -v $(PODMAN_BUILD_OPTS) \
		-o $(@D)/bin/rootlessport $(PODMAN_GOMOD)/cmd/rootlessport
endef
PODMAN_POST_BUILD_HOOKS += PENGYOS_PODMAN_BUILD_ROOTLESSPORT
define PENGYOS_PODMAN_INSTALL_ROOTLESSPORT
	$(INSTALL) -D -m 0755 $(@D)/bin/rootlessport $(TARGET_DIR)/usr/libexec/podman/rootlessport
endef
PODMAN_POST_INSTALL_TARGET_HOOKS += PENGYOS_PODMAN_INSTALL_ROOTLESSPORT
endif

# polkit: Buildroot hardcodes -Dsession_tracking=ConsoleKit, so polkit could not
# see logind sessions — every user counted as "not in an active local session"
# and NetworkManager, Settings, Flatpak asked for admin auth (or refused) even
# for the person sitting at the desktop. Track sessions with logind instead.
ifeq ($(BR2_PACKAGE_POLKIT)$(BR2_PACKAGE_SYSTEMD_LOGIND),yy)
POLKIT_CONF_OPTS := $(filter-out -Dsession_tracking=%,$(POLKIT_CONF_OPTS)) -Dsession_tracking=logind
# polkit now links libsystemd: configure it only after systemd is in staging.
$(POLKIT_TARGET_CONFIGURE): $(SYSTEMD_TARGET_INSTALL_STAGING)
# systemd's polkit support (policy files; logind/hostnamed/... authorize callers
# through polkit over D-Bus). BR2_PACKAGE_SYSTEMD_POLKIT would add polkit to
# SYSTEMD_DEPENDENCIES and, with the edge above, make a cycle (make drops an
# edge and runs systemd's staging install before its build). systemd only looks
# polkit up at build time to detect pre-0.106 .pkla support, so set the option
# without the dependency.
SYSTEMD_CONF_OPTS := $(filter-out -Dpolkit=%,$(SYSTEMD_CONF_OPTS)) -Dpolkit=enabled
endif

# Mesa names each Vulkan ICD manifest after the host CPU (radeon_icd.x86_64.json),
# but Buildroot's meson cross file leaves `cpu` empty for generic x86_64, giving
# radeon_icd..json. The loader reads any *.json, so this is cosmetic; one
# manifest per driver is all a single-arch image needs.
ifeq ($(BR2_PACKAGE_MESA3D_VULKAN_DRIVER),y)
MESA3D_CONF_OPTS += -Dvulkan-manifest-per-architecture=false
endif

# Force Mesa's native EGL platform to DRM so a KMS compositor (cosmic-comp) gets
# EGL_EXT_image_dma_buf_import; Buildroot leaves it on auto. This used to be a
# hand edit to the build host's Buildroot checkout (package/mesa3d/mesa3d.mk),
# so a fresh checkout built a different Mesa.
MESA3D_CONF_OPTS += -Degl-native-platform=drm

# linux-firmware: Buildroot's Intel options miss the Alder Lake-N / Raptor Lake
# CNVi pairings — iwlwifi-so-a0-hr-b0 (AX101/AX201 on the "so" CNVi, the N100's
# Wi-Fi: "no suitable firmware found!", no wlan interface) and so-a0-gf4-a0.
# The files are in the linux-firmware tarball; just install them too.
ifeq ($(BR2_PACKAGE_LINUX_FIRMWARE_IWLWIFI_22000),y)
LINUX_FIRMWARE_FILES += intel/iwlwifi/iwlwifi-so-a0-hr-b0*.ucode
endif
ifeq ($(BR2_PACKAGE_LINUX_FIRMWARE_IWLWIFI_6E),y)
LINUX_FIRMWARE_FILES += intel/iwlwifi/iwlwifi-so-a0-gf4-a0*.{ucode,pnvm}
endif
# Intel Wi-Fi 7 beyond the BE200 (IWLWIFI_7 ships only gl-c0-fm-c0): BE201 and
# the Meteor/Arrow Lake CNVi ("bz") and Lunar Lake ("sc") pairings. Kernel 7.2
# asks for core API 106 first for these MACs (cfg/bz.c, cfg/sc.c; 102 is the
# floor) and API 100 for their older-RF pairings, and every pairing has a c106 or
# a 100 build, so only those are installed (~16 MB, not all 68 MB).
ifeq ($(BR2_PACKAGE_LINUX_FIRMWARE_IWLWIFI_7),y)
LINUX_FIRMWARE_FILES += \
	intel/iwlwifi/iwlwifi-bz-b0-*-c106.ucode \
	intel/iwlwifi/iwlwifi-bz-b0-*-100.ucode \
	intel/iwlwifi/iwlwifi-bz-b0-*.pnvm \
	intel/iwlwifi/iwlwifi-sc-a0-*-c106.ucode \
	intel/iwlwifi/iwlwifi-sc-a0-*-100.ucode
endif
# The Bluetooth half of the Qualcomm WCN7850 (btusb's QCA support).
ifeq ($(BR2_PACKAGE_LINUX_FIRMWARE_QUALCOMM_WCN7850),y)
LINUX_FIRMWARE_FILES += qca/hmtbtfw20.tlv qca/hmtnv20.*
endif
# Laptop speaker amplifiers behind HDA (Cirrus CS35L41/CS35L56, TI TAS2781;
# linux.fragment): the per-model tuning files. Without them the amps run with
# no DSP protection or stay silent. Wherever there is a desktop audio stack.
ifeq ($(BR2_PACKAGE_PIPEWIRE),y)
LINUX_FIRMWARE_DIRS += cirrus ti/audio/tas2781
endif

# CPU microcode, built into the kernel (core.config says why). The early loader
# looks built-in firmware up by name (intel-ucode/ff-mm-ss,
# amd-ucode/microcode_amd_famXXh.bin), so CONFIG_EXTRA_FIRMWARE lists every file
# both packages provide, from one directory: intel-microcode installs to
# $(BINARIES_DIR)/intel-ucode, and the AMD files are copied next to it. The
# list is computed when the kernel config is fixed up, so the order-only rule
# makes sure both packages are there by then (KCONFIG_DEPENDENCIES is read when
# linux.mk is evaluated, before this file).
ifeq ($(BR2_LINUX_KERNEL)$(BR2_PACKAGE_INTEL_MICROCODE)$(BR2_PACKAGE_LINUX_FIRMWARE_AMD_UCODE),yyy)
$(LINUX_DIR)/.stamp_kconfig_fixup_done: | intel-microcode linux-firmware
# (The list is built by the shell: make's $(wildcard) caches directory contents
# and can miss a directory created during the same run. The empty first line
# keeps `+=`, which joins with a space, from gluing the first command onto the
# last line of linux.mk's own fixups.)
define PENGYOS_LINUX_BUILTIN_MICROCODE

	mkdir -p $(BINARIES_DIR)/amd-ucode
	cp $(LINUX_FIRMWARE_DIR)/amd-ucode/*.bin $(BINARIES_DIR)/amd-ucode/
	ls $(BINARIES_DIR)/intel-ucode/* >/dev/null
	$(SED) '/^\(# \)\?CONFIG_EXTRA_FIRMWARE\>/d' $(call KCONFIG_DOT_CONFIG)
	echo "CONFIG_EXTRA_FIRMWARE=\"$$(cd $(BINARIES_DIR) && echo intel-ucode/* amd-ucode/*.bin)\"" \
		>> $(call KCONFIG_DOT_CONFIG)
	$(call KCONFIG_SET_OPT,CONFIG_EXTRA_FIRMWARE_DIR,"$${BR_BINARIES_DIR}")
endef
LINUX_KCONFIG_FIXUP_CMDS += $(PENGYOS_LINUX_BUILTIN_MICROCODE)
endif

# gvfs: Buildroot hardcodes -Dmtp=false. With our libmtp, enable the MTP backend
# so phones show up in COSMIC Files.
ifeq ($(BR2_PACKAGE_GVFS)$(BR2_PACKAGE_LIBMTP),yy)
GVFS_CONF_OPTS := $(filter-out -Dmtp=%,$(GVFS_CONF_OPTS)) -Dmtp=true
$(GVFS_TARGET_CONFIGURE): $(LIBMTP_TARGET_INSTALL_STAGING)
endif

# sane-backends: with libgphoto2 present it builds the gphoto2 backend (use a
# camera *as a scanner*), whose code writes through strchr()'s result — an error
# with glibc's C23 const-correct strchr. Scanners don't need it (cameras are
# handled by libgphoto2/gvfs); build without that one backend.
ifeq ($(BR2_PACKAGE_SANE_BACKENDS)$(BR2_PACKAGE_LIBGPHOTO2),yy)
SANE_BACKENDS_CONF_OPTS += --without-gphoto2
endif

# shared-mime-info: Buildroot installs ONLY the compiled glib `mime.cache` to the
# target, NOT the text DB files that the freedesktop spec (and the Rust xdg-mime
# crate) read: `globs`, `globs2`, `magic`, `aliases`, the per-type `subclasses`
# directories, etc. cosmic-files identifies a file's type via cosmic::mime_icon
# -> xdg_mime::SharedMimeInfo, which parses THOSE files, not mime.cache — so with
# them absent it cannot identify any file and falls back to a special/uncertain
# mime (CosmicEdit handles text/plain + application/x-zerosize), which is why
# double-clicking a PNG opened the text editor while "Open With -> Swayimg"
# worked. Install the full compiled DB from staging so type detection works.
ifeq ($(BR2_PACKAGE_SHARED_MIME_INFO),y)
define SHARED_MIME_INFO_INSTALL_DB_TARGET
	cp -a $(STAGING_DIR)/usr/share/mime/. $(TARGET_DIR)/usr/share/mime/
endef
SHARED_MIME_INFO_POST_INSTALL_TARGET_HOOKS += SHARED_MIME_INFO_INSTALL_DB_TARGET
endif

# gsettings-desktop-schemas: PengyOS's GSettings defaults
# (board/desktop/gsettings/*.gschema.override; today, location services on).
# libglib2 compiles the target's schemas from STAGING at target-finalize, so
# the override has to be in staging's schema dir, not in an overlay (overlays
# land after that step and would never be compiled).
ifeq ($(BR2_PACKAGE_GSETTINGS_DESKTOP_SCHEMAS),y)
define PENGYOS_GSETTINGS_INSTALL_OVERRIDES
	$(INSTALL) -m 0644 -t $(STAGING_DIR)/usr/share/glib-2.0/schemas \
		$(BR2_EXTERNAL_PENGYOS_PATH)/board/desktop/gsettings/*.gschema.override
endef
GSETTINGS_DESKTOP_SCHEMAS_POST_INSTALL_STAGING_HOOKS += PENGYOS_GSETTINGS_INSTALL_OVERRIDES
endif

# PipeWire's aptX / aptX HD and LDAC Bluetooth codecs: Buildroot's recipe has
# no switches for them (meson "auto" would race the libraries' build).
ifeq ($(BR2_PACKAGE_PIPEWIRE)$(BR2_PACKAGE_BLUEZ5_UTILS)$(BR2_PACKAGE_LIBFREEAPTX),yyy)
PIPEWIRE_CONF_OPTS += -Dbluez5-codec-aptx=enabled
# DEPENDENCIES is read when pipewire's recipe is evaluated, before this file:
# order the configure step explicitly (as polkit/gvfs above).
$(PIPEWIRE_TARGET_CONFIGURE): $(LIBFREEAPTX_TARGET_INSTALL_STAGING)
endif
ifeq ($(BR2_PACKAGE_PIPEWIRE)$(BR2_PACKAGE_BLUEZ5_UTILS)$(BR2_PACKAGE_LDACBT),yyy)
PIPEWIRE_CONF_OPTS += -Dbluez5-codec-ldac=enabled -Dbluez5-codec-ldac-dec=disabled
$(PIPEWIRE_TARGET_CONFIGURE): $(LDACBT_TARGET_INSTALL_STAGING)
endif

# PipeWire: Buildroot hardcodes -Dflatpak=disabled, which compiles out the
# /proc/PID/root/.flatpak-info parser (it returns -ENOTSUP: "flatpak-info parsing
# failed: Operation not supported"). Sandboxed clients were still detected and
# restricted, but without their app id and instance id (per-app stream names,
# WirePlumber's mpris-pause matching), and pipewire-pulse never saw
# devices=all, so apps granted every device (OBS, voice chat) did not get the
# Manager role. meson takes the last -D; libglib2 is the parser's only dependency.
ifeq ($(BR2_PACKAGE_PIPEWIRE)$(BR2_PACKAGE_FLATPAK),yy)
PIPEWIRE_CONF_OPTS += -Dflatpak=enabled
$(PIPEWIRE_TARGET_CONFIGURE): $(LIBGLIB2_TARGET_INSTALL_STAGING)
endif

# PulseAudio, client side only, next to PipeWire: the Desktop wants libpulse and
# pactl (desktop.config says why), never the PulseAudio daemon, whose user
# socket/service would race pipewire-pulse for $XDG_RUNTIME_DIR/pulse/native.
# Buildroot always builds the daemon; daemon=false drops it with its units,
# autostart file and config.
ifeq ($(BR2_PACKAGE_PULSEAUDIO)$(BR2_PACKAGE_PIPEWIRE),yy)
PULSEAUDIO_CONF_OPTS += -Ddaemon=false
endif

# --- Reproducible builds: inputs Buildroot takes from the build host ----------
# Two builds of one commit on different hosts must give the same image
# (BR2_REPRODUCIBLE). Comparing behoserver's against a build in a distrobox on
# behoweather found these recipes recording something about the host:

# lvm2's configure records `modprobe` only if the *build host* has one
# (/usr/sbin/modprobe on behoserver, none in a container), and lvm then loads
# dm modules itself or not. The image has kmod: say so.
LVM2_CONF_ENV += ac_cv_path_MODPROBE_CMD=/usr/sbin/modprobe
# ...and `systemd-run` the same way, from $PATH: Buildroot's host/bin comes
# first, so 69-dm-lvm.rules ran /home/<user>/build/.../host/bin/systemd-run,
# which exists on no installed machine (LVM volume groups never autoactivated)
# and made the image differ by output path. Name the target's.
LVM2_CONF_OPTS += --with-systemd-run=/usr/bin/systemd-run

# ncurses compiles the build environment's $TERMINFO_DIRS in as its search path
# (distrobox exports one with /run/host/usr/share/terminfo). Pin it.
NCURSES_CONF_OPTS += --with-terminfo-dirs=/usr/share/terminfo

# NetworkManager's meson probes the *build host* (`test -f`) for polkit's
# helper and records the first path it finds (/usr/libexec on behoserver,
# /usr/lib/polkit-1 in a distrobox). Name the image's own copy.
ifeq ($(BR2_PACKAGE_NETWORK_MANAGER)$(BR2_PACKAGE_POLKIT),yy)
NETWORK_MANAGER_CONF_OPTS += -Dpolkit_agent_helper_1=/usr/lib/polkit-1/polkit-agent-helper-1
endif
# ...and dnsmasq (connection sharing / hotspot) the same way: found on
# behoserver, not in the distrobox. Name where the image would carry it.
ifeq ($(BR2_PACKAGE_NETWORK_MANAGER),y)
NETWORK_MANAGER_CONF_OPTS += -Ddnsmasq=/usr/sbin/dnsmasq
endif

# libclc's nvptx64 bitcode: clang picks the default PTX version from a CUDA
# install it finds on the *build host* (/usr/local/cuda on behoserver: +ptx88;
# none in a distrobox: +ptx42). Point it at no CUDA, as on a host without one
# (CCC_OVERRIDE_OPTIONS appends to every clang run, '#' = quietly.
# -Qunused-arguments: most targets ignore the flag, as they already ignored
# Buildroot's -pie, and each run warned about it.)
LIBCLC_BUILD_ENV += CCC_OVERRIDE_OPTIONS="\# +--cuda-path=/nonexistent +-Qunused-arguments"

# Python records the `install` configure found on the build host's PATH (a GNU
# shim in ~/.local/bin, uutils in /usr/bin, ...) in sysconfig, which
# pip/setuptools read on the target. Record the target's own.
define PYTHON3_TARGET_INSTALL_PATH
	sed -i -E 's#[^[:space:]"'\''=]*/install -c#/usr/bin/install -c#g' \
		$(TARGET_DIR)/usr/lib/python$(PYTHON3_VERSION_MAJOR)/_sysconfigdata_*.py \
		$(TARGET_DIR)/usr/lib/python$(PYTHON3_VERSION_MAJOR)/_sysconfig_vars_*.json \
		$(TARGET_DIR)/usr/lib/python$(PYTHON3_VERSION_MAJOR)/config-*/Makefile
endef
PYTHON3_POST_INSTALL_TARGET_HOOKS += PYTHON3_TARGET_INSTALL_PATH

# Python bytecode: Buildroot compiles the whole target library in one
# `compileall` run, and marshal decides which objects to share by reference
# count — which depends on everything that process compiled before. Two hosts
# built different functools.pyc (identical code objects, different bytes).
# Compile each file in its own isolated interpreter instead (sorted, in
# parallel): the bytes then depend on the file alone.
ifeq ($(BR2_PACKAGE_PYTHON3),y)
define PYTHON3_CREATE_PYC_FILES
	$(PYTHON3_FIX_TIME)
	cd $(TARGET_DIR) && find usr/lib/python$(PYTHON3_VERSION_MAJOR) -name '*.py' -print0 | \
		LC_ALL=C sort -z | \
		xargs -0 -r -n 1 -P $(PARALLEL_JOBS) env PYTHONNOUSERSITE=1 \
		$(HOST_DIR)/bin/python$(PYTHON3_VERSION_MAJOR) -s \
		$(BR2_EXTERNAL_PENGYOS_PATH)/board/common/pyc-compile.py \
		$(if $(BR2_PACKAGE_PYTHON3_PYC_ONLY),--legacy)
endef
endif

# mke2fs picks a random filesystem UUID and directory hash seed, and post-image
# reuses the UUID as the root PARTUUID (grub.cfg) and derives the disk images'
# other IDs from it (board/common/image-ids.sh). Derive it from the finished
# os-release instead (IMAGE_ID, IMAGE_VERSION, BUILD_ID): one build, one UUID;
# every build still a new one. Recursive '=': expanded when the rootfs is made,
# after target-finalize has written os-release. BASE_TARGET_DIR, not TARGET_DIR:
# the fs rules point TARGET_DIR at a per-filesystem copy that is still empty
# when make expands the recipe.
PENGYOS_ROOTFS_UUID = $(shell sha256sum $(BASE_TARGET_DIR)/usr/lib/os-release | \
	sed -E 's/^(.{8})(.{4}).(.{3}).(.{3})(.{12}).*/\1-\2-4\3-8\4-\5/')
ROOTFS_EXT2_OPTS += -U $(PENGYOS_ROOTFS_UUID) -E hash_seed=$(PENGYOS_ROOTFS_UUID)
