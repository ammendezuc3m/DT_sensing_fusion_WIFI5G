#include "waveforms/wifi_nonht/data_decoder.hpp"

#include <algorithm>
#include <array>
#include <cstddef>
#include <cstdint>
#include <limits>
#include <stdexcept>
#include <vector>

namespace sensing::wifi_nonht {

namespace {

constexpr std::size_t kCodedBitsPerSymbol = 48;
constexpr std::size_t kDataBitsPerSymbol = 24;
constexpr std::size_t kServiceBits = 16;
constexpr std::size_t kTailBits = 6;
constexpr std::size_t kViterbiStates = 64;

std::uint8_t parity(const std::uint8_t value) {
    return static_cast<std::uint8_t>(
        __builtin_parity(
            static_cast<unsigned int>(value)
        )
    );
}

}  // namespace

DataDecoder::DataDecoder(DataDecoderConfig config)
    : config_(config) {

    if (config_.scrambler_seed == 0U
        || config_.scrambler_seed > 0x7FU) {

        throw std::invalid_argument(
            "scrambler_seed debe estar entre 1 y 127"
        );
    }
}

DataDecodeResult DataDecoder::decode(
    const DataSymbolsResult& symbols,
    const LegacySignalResult& lsig
) const {
    DataDecodeResult result;
    result.scrambler_seed = config_.scrambler_seed;

    if (!symbols.valid || !lsig.valid) {
        return result;
    }

    if (lsig.data_rate_mbps != 6) {
        /*
         * Primera implementación:
         * Non-HT 6 Mb/s, BPSK, coding rate 1/2.
         */
        return result;
    }

    const std::size_t expected_constellation_points =
        lsig.number_of_data_symbols
        * kCodedBitsPerSymbol;

    if (symbols.equalized_data_subcarriers.size()
        != expected_constellation_points) {

        return result;
    }

    const auto interleaved_bits =
        hard_demodulate_bpsk(
            symbols.equalized_data_subcarriers
        );

    const auto coded_bits =
        deinterleave_all_symbols(
            interleaved_bits
        );

    const std::size_t decoded_bit_count =
        lsig.number_of_data_symbols
        * kDataBitsPerSymbol;

    result.decoded_scrambled_bits =
        viterbi_decode_rate_half(
            coded_bits,
            decoded_bit_count
        );

    if (result.decoded_scrambled_bits.size()
        != decoded_bit_count) {

        return result;
    }

    result.total_decoded_bits = decoded_bit_count;

    const std::size_t psdu_bit_count =
        static_cast<std::size_t>(
            lsig.length_bytes
        ) * 8U;

    const std::size_t tail_start =
        kServiceBits + psdu_bit_count;

    const std::size_t tail_end =
        tail_start + kTailBits;

    if (tail_end > decoded_bit_count) {
        return result;
    }

    /*
     * El transmisor fuerza los seis bits TAIL a cero
     * después del scrambling y antes del codificador.
     *
     * Por tanto, se validan sobre la salida Viterbi
     * todavía scrambled.
     */
    result.encoded_tail_valid =
        std::all_of(
            result.decoded_scrambled_bits.begin()
                + static_cast<std::ptrdiff_t>(
                    tail_start
                ),
            result.decoded_scrambled_bits.begin()
                + static_cast<std::ptrdiff_t>(
                    tail_end
                ),
            [](const std::uint8_t bit) {
                return bit == 0U;
            }
        );

    result.descrambled_bits =
        descramble(
            result.decoded_scrambled_bits,
            config_.scrambler_seed
        );

    if (result.descrambled_bits.size()
        != decoded_bit_count) {

        return result;
    }

    result.service_valid =
        std::all_of(
            result.descrambled_bits.begin(),
            result.descrambled_bits.begin()
                + static_cast<std::ptrdiff_t>(
                    kServiceBits
                ),
            [](const std::uint8_t bit) {
                return bit == 0U;
            }
        );

    const auto psdu_begin =
        result.descrambled_bits.begin()
        + static_cast<std::ptrdiff_t>(
            kServiceBits
        );

    const auto psdu_end =
        psdu_begin
        + static_cast<std::ptrdiff_t>(
            psdu_bit_count
        );

    result.psdu_bytes =
        bits_to_bytes_lsb_first(
            std::span<const std::uint8_t>{
                psdu_begin,
                psdu_end
            }
        );

    result.pad_bits =
        decoded_bit_count - tail_end;

    const bool service_ok =
        !config_.require_zero_service
        || result.service_valid;

    const bool tail_ok =
        !config_.require_zero_encoded_tail
        || result.encoded_tail_valid;

    result.valid =
        service_ok
        && tail_ok
        && result.psdu_bytes.size()
            == lsig.length_bytes;

    return result;
}

std::vector<std::uint8_t>
DataDecoder::hard_demodulate_bpsk(
    const std::span<const std::complex<float>> symbols
) {
    std::vector<std::uint8_t> bits;
    bits.reserve(symbols.size());

    /*
     * Convención del generador de pablosito:
     *
     * bit 0 -> -1
     * bit 1 -> +1
     */
    for (const auto value : symbols) {
        bits.push_back(
            value.real() > 0.0F ? 1U : 0U
        );
    }

    return bits;
}

std::vector<std::uint8_t>
DataDecoder::deinterleave_all_symbols(
    const std::span<const std::uint8_t> interleaved_bits
) {
    if (interleaved_bits.size()
        % kCodedBitsPerSymbol != 0U) {

        throw std::invalid_argument(
            "El número de bits intercalados no es múltiplo de 48"
        );
    }

    std::vector<std::uint8_t> coded_bits(
        interleaved_bits.size(),
        0U
    );

    const std::size_t number_of_symbols =
        interleaved_bits.size()
        / kCodedBitsPerSymbol;

    for (std::size_t symbol = 0;
         symbol < number_of_symbols;
         ++symbol) {

        const std::size_t base =
            symbol * kCodedBitsPerSymbol;

        /*
         * TX:
         *
         * i = 3 * (k mod 16) + floor(k / 16)
         * interleaved[i] = coded[k]
         *
         * RX:
         *
         * coded[k] = interleaved[i]
         */
        for (std::size_t k = 0;
             k < kCodedBitsPerSymbol;
             ++k) {

            const std::size_t i =
                (kCodedBitsPerSymbol / 16U)
                    * (k % 16U)
                + k / 16U;

            coded_bits[base + k] =
                interleaved_bits[base + i];
        }
    }

    return coded_bits;
}

std::vector<std::uint8_t>
DataDecoder::viterbi_decode_rate_half(
    const std::span<const std::uint8_t> coded_bits,
    const std::size_t output_bit_count
) {
    if (coded_bits.size()
        != output_bit_count * 2U) {

        throw std::invalid_argument(
            "Viterbi 1/2: longitud codificada inválida"
        );
    }

    constexpr int infinity =
        std::numeric_limits<int>::max() / 4;

    std::array<int,kViterbiStates> metrics{};
    std::array<int,kViterbiStates> next_metrics{};

    metrics.fill(infinity);
    metrics[0] = 0;

    std::vector<std::array<std::uint8_t,kViterbiStates>>
        predecessor(output_bit_count);

    std::vector<std::array<std::uint8_t,kViterbiStates>>
        decision(output_bit_count);

    constexpr std::uint8_t generator0 = 0133;
    constexpr std::uint8_t generator1 = 0171;

    for (std::size_t time = 0;
         time < output_bit_count;
         ++time) {

        next_metrics.fill(infinity);

        const std::uint8_t received0 =
            coded_bits[2U * time];

        const std::uint8_t received1 =
            coded_bits[2U * time + 1U];

        for (std::size_t state = 0;
             state < kViterbiStates;
             ++state) {

            if (metrics[state] >= infinity) {
                continue;
            }

            for (std::uint8_t input_bit = 0U;
                 input_bit <= 1U;
                 ++input_bit) {

                /*
                 * Misma convención que el TX:
                 *
                 * state = ((state >> 1)
                 *          | (input << 6)) & 0x7F
                 *
                 * El estado Viterbi guarda los seis bits
                 * anteriores. El registro completo tiene
                 * siete bits.
                 */
                const std::uint8_t shift_register =
                    static_cast<std::uint8_t>(
                        (
                            static_cast<std::uint8_t>(
                                input_bit << 6U
                            )
                            | static_cast<std::uint8_t>(
                                state
                            )
                        )
                        & 0x7FU
                    );

                const std::uint8_t encoded0 =
                    parity(
                        static_cast<std::uint8_t>(
                            shift_register
                            & generator0
                        )
                    );

                const std::uint8_t encoded1 =
                    parity(
                        static_cast<std::uint8_t>(
                            shift_register
                            & generator1
                        )
                    );

                const int branch_metric =
                    static_cast<int>(
                        encoded0 != received0
                    )
                    + static_cast<int>(
                        encoded1 != received1
                    );

                const std::size_t next_state =
                    static_cast<std::size_t>(
                        shift_register >> 1U
                    );

                const int candidate_metric =
                    metrics[state]
                    + branch_metric;

                if (candidate_metric
                    < next_metrics[next_state]) {

                    next_metrics[next_state] =
                        candidate_metric;

                    predecessor[time][next_state] =
                        static_cast<std::uint8_t>(
                            state
                        );

                    decision[time][next_state] =
                        input_bit;
                }
            }
        }

        metrics = next_metrics;
    }

    /*
     * No podemos forzar el estado final a cero.
     *
     * El TX fuerza a cero los seis bits TAIL, pero después
     * todavía codifica los bits PAD, que permanecen scrambled.
     * Por ello el codificador convolucional puede terminar en
     * cualquier estado.
     *
     * Elegimos el estado con menor métrica acumulada.
     */
    const std::size_t final_state_best =
        static_cast<std::size_t>(
            std::distance(
                metrics.begin(),
                std::min_element(
                    metrics.begin(),
                    metrics.end()
                )
            )
        );

    std::size_t final_state = final_state_best;

    std::vector<std::uint8_t> decoded(
        output_bit_count,
        0U
    );

    for (std::size_t reverse = output_bit_count;
         reverse > 0U;
         --reverse) {

        const std::size_t time =
            reverse - 1U;

        decoded[time] =
            decision[time][final_state];

        final_state =
            predecessor[time][final_state];
    }

    return decoded;
}

std::vector<std::uint8_t>
DataDecoder::descramble(
    const std::span<const std::uint8_t> bits,
    const std::uint8_t initial_state
) {
    if (initial_state == 0U
        || initial_state > 0x7FU) {

        throw std::invalid_argument(
            "Estado inicial de scrambler inválido"
        );
    }

    std::array<std::uint8_t,7> state{};

    for (std::size_t index = 0;
         index < state.size();
         ++index) {

        state[index] =
            static_cast<std::uint8_t>(
                (initial_state >> index) & 1U
            );
    }

    std::vector<std::uint8_t> output;
    output.reserve(bits.size());

    for (const std::uint8_t bit : bits) {
        const std::uint8_t feedback =
            static_cast<std::uint8_t>(
                state[6] ^ state[3]
            );

        output.push_back(
            static_cast<std::uint8_t>(
                bit ^ feedback
            )
        );

        for (std::size_t index = 6U;
             index > 0U;
             --index) {

            state[index] =
                state[index - 1U];
        }

        state[0] = feedback;
    }

    return output;
}

std::vector<std::uint8_t>
DataDecoder::bits_to_bytes_lsb_first(
    const std::span<const std::uint8_t> bits
) {
    if (bits.size() % 8U != 0U) {
        throw std::invalid_argument(
            "El número de bits PSDU no es múltiplo de 8"
        );
    }

    std::vector<std::uint8_t> bytes(
        bits.size() / 8U,
        0U
    );

    for (std::size_t byte_index = 0;
         byte_index < bytes.size();
         ++byte_index) {

        std::uint8_t value = 0U;

        for (std::size_t bit = 0;
             bit < 8U;
             ++bit) {

            value |= static_cast<std::uint8_t>(
                bits[byte_index * 8U + bit]
                << bit
            );
        }

        bytes[byte_index] = value;
    }

    return bytes;
}

}  // namespace sensing::wifi_nonht
