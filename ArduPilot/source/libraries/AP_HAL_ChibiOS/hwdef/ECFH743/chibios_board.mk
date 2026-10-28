# Keep the native startup for the application. The bootloader's DFU-enabled
# startup also calls the existing ArduPilot entry hook before C runtime setup.
ECF_BOARD_DIR := $(dir $(lastword $(MAKEFILE_LIST)))
ifneq ($(findstring -DCRT0_ENTRY_HOOK=TRUE,$(USE_ASXOPT)),)
override CHIBIOS_STARTUP_MK := ../../libraries/AP_HAL_ChibiOS/hwdef/ECFH743/startup_dfu.mk
endif
include $(ECF_BOARD_DIR)../common/chibios_board.mk
