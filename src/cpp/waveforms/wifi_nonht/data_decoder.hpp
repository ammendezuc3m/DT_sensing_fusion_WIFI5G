#pragma once

#include "waveforms/wifi_nonht/channel_estimator.hpp"
#include "waveforms/wifi_nonht/legacy_signal_decoder.hpp"
#include "waveforms/wifi_nonht/synchronizer.hpp"

#include <complex>
#include <cstddef>
#include <cstdint>
#include <span>
#include <vector>

namespace sensing::wifi_nonht {

struct DataDecodeResult {
    bool valid{false};
    bool service_valid{false};
    bool tail_valid{false};

    std::uint8_t scrambler_seed{0};

    std::vector<std::uint8_t> psdu_bytes;
    std::vector<std::uint8_t> decoded_bits;
};

class DataDecoder {
public:
    [[nodiscard]]
    DataDecodeResult decode(
        std::span<const std::complex<float>> samples,
        const SyncResult& sync,
        const ChannelEstimate& channel,
        const LegacySignalResult& lsig
    ) const;

private:
    [[nodiscard]]
    static std::vector<std::uint8_t> demap_16qam(
        const std::vector<std::complex<float>>& symbols
    );

    [[nodiscard]]
    static std::vector<std::uint8_t> deinterleave_symbol(
        std::span<const std::uint8_t> bits,
        std::size_t n_cbps,
        std::size_t n_bpsc
    );

    [[nodiscard]]
    static std::vector<std::int8_t> depuncture_rate_three_quarters(
        std::span<const std::uint8_t> bits
    );

    [[nodiscard]]
    static std::vector<std::uint8_t> viterbi_decode_soft_erasure(
        std::span<const std::int8_t> coded_bits,
        std::size_t output_bits
    );

    [[nodiscard]]
    static std::vector<std::uint8_t> descramble(
        std::span<const std::uint8_t> bits,
        std::uint8_t& recovered_seed
    );
};

}  // namespace sensing::wifi_nonht
