################################################################################
#
# xdg-utils
#
################################################################################

XDG_UTILS_VERSION = 1.2.1
XDG_UTILS_SITE = https://gitlab.freedesktop.org/xdg/xdg-utils/-/archive/v$(XDG_UTILS_VERSION)
XDG_UTILS_SOURCE = xdg-utils-v$(XDG_UTILS_VERSION).tar.gz
XDG_UTILS_LICENSE = MIT
XDG_UTILS_LICENSE_FILES = LICENSE
XDG_UTILS_DEPENDENCIES = host-python3

XDG_UTILS_SCRIPTS = xdg-desktop-menu xdg-desktop-icon xdg-mime \
	xdg-icon-resource xdg-open xdg-email xdg-screensaver xdg-settings

# The scripts embed their --help/--manual text, which upstream renders from
# scripts/desc/*.xml with `xmlto txt` (DocBook XSL + a text browser; neither is
# in Buildroot). docbook2txt.py renders the same text; the release tarballs
# with prebuilt scripts stopped at 1.1.3.
define XDG_UTILS_BUILD_CMDS
	$(foreach s,$(XDG_UTILS_SCRIPTS), \
		$(HOST_DIR)/bin/python3 $(XDG_UTILS_PKGDIR)/docbook2txt.py \
			$(@D)/scripts/desc/$(s).xml $(@D)/scripts/$(s).txt$(sep))
	cd $(@D)/scripts && $(foreach s,$(XDG_UTILS_SCRIPTS), \
		awk -f generate-help-script.awk $(s).in | sed -e 's/@NAME@/$(s)/g' > $(s) &&) true
endef

define XDG_UTILS_INSTALL_TARGET_CMDS
	$(foreach s,$(XDG_UTILS_SCRIPTS), \
		$(INSTALL) -D -m 0755 $(@D)/scripts/$(s) $(TARGET_DIR)/usr/bin/$(s)$(sep))
endef

$(eval $(generic-package))
