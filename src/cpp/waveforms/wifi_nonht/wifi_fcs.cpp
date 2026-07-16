#include "waveforms/wifi_nonht/wifi_fcs.hpp"

#include <cstddef>
#include <cstdint>

namespace sensing::wifi_nonht {

std::uint32_t calculate_wifi_crc32(
    const std::span<const std::uint8_t> bytes
) {
    /*
     * CRC-32 usado por IEEE 802.11:
     *
     * Polynomial reflected: 0xEDB88320
     * Initial value:         0xFFFFFFFF
     * Final XOR:             0xFFFFFFFF
     *
     * El FCS se transmite little-endian.
     */
    std::uint32_t crc = 0xFFFFFFFFU;

    for (const std::uint8_t byte : bytes) {
        crc ^= static_cast<std::uint32_t>(byte);

        for (std::size_t bit = 0; bit < 8U; ++bit) {
            const bool least_significant_bit =
                (crc & 1U) != 0U;

            crc >>= 1U;

            if (least_significant_bit) {
                crc ^= 0xEDB88320U;
            }
        }
    }

    return crc ^ 0xFFFFFFFFU;
}

bool validate_wifi_fcs(
    const std::span<const std::uint8_t> psdu
) {
    if (psdu.size() < 4U) {
        return false;
    }

    const std::size_t payload_size =
        psdu.size() - 4U;

    const std::uint32_t calculated =
        calculate_wifi_crc32(
            psdu.first(payload_size)
        );

    const std::uint32_t received =
        static_cast<std::uint32_t>(
            psdu[payload_size]
        )
        | (
            static_cast<std::uint32_t>(
                psdu[payload_size + 1U]
            ) << 8U
        )
        | (
            static_cast<std::uint32_t>(
                psdu[payload_size + 2U]
            ) << 16U
        )
        | (
            static_cast<std::uint32_t>(
                psdu[payload_size + 3U]
            ) << 24U
        );

    return calculated == received;
}

}  // namespace sensing::wifi_nonht
