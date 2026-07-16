#pragma once

#include "waveforms/wifi_nonht/data_symbol_extractor.hpp"
#include "waveforms/wifi_nonht/legacy_signal_decoder.hpp"

#include <cstddef>
#include <cstdint>
#include <span>
#include <vector>

namespace sensing::wifi_nonht {

struct DataDecodeResult {
    bool valid{false};
    bool service_valid{false};
    bool encoded_tail_valid{false};

    std::uint8_t scrambler_seed{0};

    std::size_t total_decoded_bits{0};
    std::size_t pad_bits{0};

    std::vector<std::uint8_t> psdu_bytes;
    std::vector<std::uint8_t> decoded_scrambled_bits;
    std::vector<std::uint8_t> descrambled_bits;
};

struct DataDecoderConfig {
    /*
     * El TX actual de pablosito usa 0x5D.
     *
     * Más adelante se podrá añadir recuperación automática
     * del estado inicial a partir de SERVICE.
     */
    std::uint8_t scrambler_seed{0x5D};

    bool require_zero_service{true};
    bool require_zero_encoded_tail{true};
};

class DataDecoder {
public:
    explicit DataDecoder(DataDecoderConfig config = {});

    [[nodiscard]]
    DataDecodeResult decode(
        const DataSymbolsResult& symbols,
        const LegacySignalResult& lsig
    ) const;

private:
    DataDecoderConfig config_;

    [[nodiscard]]
    static std::vector<std::uint8_t> hard_demodulate_bpsk(
        std::span<const std::complex<float>> symbols
    );

    [[nodiscard]]
    static std::vector<std::uint8_t> deinterleave_all_symbols(
        std::span<const std::uint8_t> interleaved_bits
    );

    [[nodiscard]]
    static std::vector<std::uint8_t> viterbi_decode_rate_half(
        std::span<const std::uint8_t> coded_bits,
        std::size_t output_bit_count
    );

    [[nodiscard]]
    static std::vector<std::uint8_t> descramble(
        std::span<const std::uint8_t> bits,
        std::uint8_t initial_state
    );

    [[nodiscard]]
    static std::vector<std::uint8_t> bits_to_bytes_lsb_first(
        std::span<const std::uint8_t> bits
    );
};

}  // namespace sensing::wifi_nonht
