#pragma once

#include "waveforms/wifi_nonht/channel_estimator.hpp"
#include "waveforms/wifi_nonht/synchronizer.hpp"

#include <array>
#include <complex>
#include <cstddef>
#include <cstdint>
#include <span>

namespace sensing::wifi_nonht {

struct LegacySignalResult {
    bool valid{false};
    bool parity_valid{false};
    bool tail_valid{false};
    bool rate_valid{false};
    bool length_valid{false};

    std::uint8_t rate_field{0};
    std::uint16_t length_bytes{0};

    int data_rate_mbps{0};
    std::size_t number_of_data_symbols{0};

    std::array<std::uint8_t,24> decoded_bits{};
};

class LegacySignalDecoder {
public:
    LegacySignalDecoder() = default;

    [[nodiscard]]
    LegacySignalResult decode(
        std::span<const std::complex<float>> samples,
        const SyncResult& sync,
        const ChannelEstimate& channel
    ) const;

private:
    [[nodiscard]]
    static std::array<std::uint8_t,48> deinterleave(
        const std::array<std::uint8_t,48>& bits
    );

    [[nodiscard]]
    static std::array<std::uint8_t,24> viterbi_decode_rate_half(
        const std::array<std::uint8_t,48>& coded_bits
    );

    [[nodiscard]]
    static int rate_field_to_mbps(std::uint8_t rate);

    [[nodiscard]]
    static std::size_t data_bits_per_symbol(int rate_mbps);
};

}  // namespace sensing::wifi_nonht
