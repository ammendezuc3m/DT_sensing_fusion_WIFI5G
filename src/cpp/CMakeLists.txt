cmake_minimum_required(VERSION 3.20)

project(
    dt_sensing_waveforms
    VERSION 0.1.0
    LANGUAGES CXX
)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

find_package(PkgConfig REQUIRED)
pkg_check_modules(FFTW3F REQUIRED IMPORTED_TARGET fftw3f)

option(
    ENABLE_STRICT_WARNINGS
    "Enable strict compiler warnings"
    ON
)

add_library(
    sensing_offline
    STATIC
    io/sc16_reader.cpp
    io/cf32_reader.cpp
    io/csi_csv_writer.cpp
    io/constellation_csv_writer.cpp
    waveforms/wifi_nonht/packet_detector.cpp
    waveforms/wifi_nonht/synchronizer.cpp
    waveforms/wifi_nonht/channel_estimator.cpp
    waveforms/wifi_nonht/legacy_signal_decoder.cpp
    waveforms/wifi_nonht/data_symbol_extractor.cpp
)

target_include_directories(
    sensing_offline
    PUBLIC
    ${CMAKE_CURRENT_SOURCE_DIR}
)

target_link_libraries(
    sensing_offline
    PUBLIC
    PkgConfig::FFTW3F
)

if(ENABLE_STRICT_WARNINGS)
    target_compile_options(
        sensing_offline
        PRIVATE
        -Wall
        -Wextra
        -Wpedantic
        -Wconversion
        -Wsign-conversion
        -Wshadow
    )
endif()

add_executable(
    offline_wifi_decode
    ${CMAKE_CURRENT_SOURCE_DIR}/../../apps/waveform_rx/offline_wifi_decode.cpp
)

target_link_libraries(
    offline_wifi_decode
    PRIVATE
    sensing_offline
)

if(ENABLE_STRICT_WARNINGS)
    target_compile_options(
        offline_wifi_decode
        PRIVATE
        -Wall
        -Wextra
        -Wpedantic
        -Wconversion
        -Wsign-conversion
        -Wshadow
    )
endif()
