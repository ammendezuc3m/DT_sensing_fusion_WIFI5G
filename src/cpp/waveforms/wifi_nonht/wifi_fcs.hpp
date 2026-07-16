#pragma once

#include <cstdint>
#include <span>

namespace sensing::wifi_nonht {

[[nodiscard]]
std::uint32_t calculate_wifi_crc32(
    std::span<const std::uint8_t> bytes
);

[[nodiscard]]
bool validate_wifi_fcs(
    std::span<const std::uint8_t> psdu
);

}  // namespace sensing::wifi_nonht
