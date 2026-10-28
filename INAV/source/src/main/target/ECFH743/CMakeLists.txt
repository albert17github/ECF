target_stm32h743xi(ECFH743 HSE_MHZ 8 COMPILE_DEFINITIONS AFATFS_NUM_CACHE_SECTORS=32)

target_compile_definitions(ECFH743.elf PRIVATE USE_DRONECAN HAL_FDCAN_MODULE_ENABLED ECF_CAN_DIAGNOSTICS)
set(ECF_CAN_DIR ${MAIN_SRC_DIR}/drivers/dronecan)
file(GLOB ECF_CAN_MESSAGES CONFIGURE_DEPENDS ${MAIN_LIB_DIR}/main/Dronecan/dsdlc_generated/src/*.c)
target_sources(ECFH743.elf PRIVATE
    ${ECF_CAN_DIR}/dronecan.c
    ${ECF_CAN_DIR}/ecf_can_diagnostics.c
    ${ECF_CAN_DIR}/dronecan_async.c
    ${ECF_CAN_DIR}/dronecan_dna_server.c
    ${ECF_CAN_DIR}/dronecan_node_status.c
    ${ECF_CAN_DIR}/libcanard/canard.c
    ${ECF_CAN_DIR}/libcanard/canard_stm32_timing.c
    ${ECF_CAN_DIR}/libcanard/canard_stm32h7xx_driver.c
    ${MAIN_SRC_DIR}/fc/fc_msp_dronecan.c
    ${MAIN_SRC_DIR}/io/gps_dronecan.c
    ${MAIN_SRC_DIR}/sensors/battery_sensor_dronecan.c
    ${MAIN_LIB_DIR}/main/STM32H7/Drivers/STM32H7xx_HAL_Driver/Src/stm32h7xx_hal_fdcan.c
    ${ECF_CAN_MESSAGES})
target_include_directories(ECFH743.elf PRIVATE ${MAIN_LIB_DIR}/main/Dronecan/dsdlc_generated/include)
