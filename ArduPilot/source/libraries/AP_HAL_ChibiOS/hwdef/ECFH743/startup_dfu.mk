include $(CHIBIOS)/os/common/startup/ARMCMx/compilers/GCC/mk/startup_stm32h7xx.mk
ALLXASMSRC := $(filter-out $(CHIBIOS)/os/common/startup/ARMCMx/compilers/GCC/crt0_v7m.S,$(ALLXASMSRC))
ALLXASMSRC += $(ECF_BOARD_DIR)bootloader_crt0.S
