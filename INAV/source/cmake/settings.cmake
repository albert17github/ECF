set(SETTINGS_GENERATED "settings_generated")
set(SETTINGS_GENERATED_C "${SETTINGS_GENERATED}.c")
set(SETTINGS_GENERATED_H "${SETTINGS_GENERATED}.h")
set(SETTINGS_FILE "${MAIN_SRC_DIR}/fc/settings.yaml")
set(SETTINGS_GENERATOR "${MAIN_UTILS_DIR}/settings.rb")

include(CMakeParseArguments)

function(enable_settings exe name)
    get_generated_files_dir(dir ${name})
    get_target_property(options ${exe} COMPILE_OPTIONS)
    get_target_property(includes ${exe} INCLUDE_DIRECTORIES)
    foreach(include_path IN LISTS includes)
        list(APPEND settings_includes "-I'${include_path}'")
    endforeach()
    get_target_property(defs ${exe} COMPILE_DEFINITIONS)
    list(TRANSFORM defs PREPEND "-D")
    list(APPEND cflags ${options})
    list(APPEND cflags ${settings_includes})
    list(APPEND cflags ${defs})

    cmake_parse_arguments(
        args
        # Boolean arguments
        ""
        # Single value arguments
        "OUTPUTS;SETTINGS_CXX"
        # Multi-value arguments
        ""
        # Start parsing after the known arguments
        ${ARGN}
    )

    find_program(RUBY_EXECUTABLE ruby)
    if (NOT RUBY_EXECUTABLE)
        message(FATAL_ERROR "Could not find ruby")
    endif()

    if(host STREQUAL TOOLCHAIN)
        set(USE_HOST_GCC "-g")
    endif()
    string(REPLACE "\"" "" cflags "${cflags}")
    list(JOIN cflags " " settings_cflags)
    string(REPLACE ";" "\\;" escaped_path "$ENV{PATH}")
    set(output ${dir}/${SETTINGS_GENERATED_H} ${dir}/${SETTINGS_GENERATED_C})
    add_custom_command(
        OUTPUT ${output}
        COMMAND
            ${CMAKE_COMMAND} -E env "CFLAGS=${settings_cflags}" "TARGET=${name}" "PATH=${escaped_path}" "SETTINGS_CXX=${args_SETTINGS_CXX}"
            ${RUBY_EXECUTABLE} ${SETTINGS_GENERATOR} ${MAIN_DIR} ${SETTINGS_FILE} -o "${dir}" ${USE_HOST_GCC}
        DEPENDS ${SETTINGS_GENERATOR} ${SETTINGS_FILE} ${MAIN_SRC_DIR}/target/${name}/target.h
        VERBATIM
    )
    set(${args_OUTPUTS} ${output} PARENT_SCOPE)
endfunction()
