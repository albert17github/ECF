add_library(drivers_board bootcheck.c shared_interfaces.cpp i2c.cpp init.c led.cpp sdio.c spi.cpp timer_config.cpp usb.c)
add_dependencies(drivers_board arch_board_hw_info)
target_link_libraries(drivers_board PRIVATE arch_io_pins arch_spi arch_board_hw_info
 drivers__led nuttx_arch nuttx_drivers nuttx_fs px4_layer platform_srgbled)

