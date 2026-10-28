px4_add_module(
	MODULE drivers__ecf_interfaces
	MAIN ecf_interfaces
	SRCS ecf_interfaces.cpp
	DEPENDS px4_layer
)
