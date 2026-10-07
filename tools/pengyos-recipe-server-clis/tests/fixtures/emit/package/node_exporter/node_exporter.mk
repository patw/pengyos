################################################################################
#
# node_exporter
#
################################################################################

NODE_EXPORTER_VERSION = 1.12.1
NODE_EXPORTER_SITE = $(call github,prometheus,node_exporter,v1.12.1)
NODE_EXPORTER_GOMOD = https://github.com/prometheus/node_exporter
NODE_EXPORTER_LICENSE = Apache-2.0
NODE_EXPORTER_LICENSE_FILES = LICENSE

$(eval $(golang-package))
