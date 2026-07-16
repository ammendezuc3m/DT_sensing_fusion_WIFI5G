#include "waveforms/wifi_nonht/legacy_signal_decoder.hpp"

#include <fftw3.h>

#include <algorithm>
#include <array>
#include <cmath>
#include <complex>
#include <cstddef>
#include <cstdint>
#include <limits>
#include <stdexcept>

namespace sensing::wifi_nonht {

namespace {

constexpr std::size_t kLegacyPreambleSamples = 320;
constexpr std::size_t kSignalCpSamples = 16;
constexpr std::size_t kFftLength = 64;

constexpr std::array<int,48> kDataSubcarriers = {
    -26,-25,-24,-23,-22,
    -20,-19,-18,-17,-16,-15,-14,-13,-12,-11,-10,-9,-8,
    -6,-5,-4,-3,-2,-1,
     1,2,3,4,5,6,
     8,9,10,11,12,13,14,15,16,17,18,19,20,
     22,23,24,25,26
};

std::size_t fft_index(const int subcarrier) {
    return subcarrier < 0
        ? static_cast<std::size_t>(
            static_cast<int>(kFftLength) + subcarrier
        )
        : static_cast<std::size_t>(subcarrier);
}

std::array<std::complex<float>,64> fft64(
    const std::array<std::complex<float>,64>& input
) {
    std::array<std::complex<float>,64> output{};

    auto* inputPointer = reinterpret_cast<fftwf_complex*>(
        const_cast<std::complex<float>*>(input.data())
    );

    auto* outputPointer = reinterpret_cast<fftwf_complex*>(
        output.data()
    );

    fftwf_plan plan = fftwf_plan_dft_1d(
        64,
        inputPointer,
        outputPointer,
        FFTW_FORWARD,
        FFTW_ESTIMATE
    );

    if (plan == nullptr) {
        throw std::runtime_error("No se pudo crear FFT L-SIG");
    }

    fftwf_execute(plan);
    fftwf_destroy_plan(plan);

    return output;
}

std::uint8_t parity(const std::uint8_t value) {
    return static_cast<std::uint8_t>(
        __builtin_parity(static_cast<unsigned int>(value))
    );
}

}  // namespace

LegacySignalResult LegacySignalDecoder::decode(
    const std::span<const std::complex<float>> samples,
    const SyncResult& sync,
    const ChannelEstimate& channel
) const {
    LegacySignalResult result;

    if (!sync.valid || !channel.valid) {
        return result;
    }

    const std::size_t signalStart =
        sync.packet_start
        + kLegacyPreambleSamples
        + kSignalCpSamples;

    if (signalStart + kFftLength > samples.size()) {
        return result;
    }

    std::array<std::complex<float>,64> signalTime{};

    for (std::size_t index = 0; index < 64; ++index) {
        signalTime[index] = samples[signalStart + index];
    }

    const auto signalFrequency = fft64(signalTime);

    std::array<std::uint8_t,48> interleavedBits{};

    for (std::size_t index = 0;
         index < kDataSubcarriers.size();
         ++index) {

        const std::size_t bin =
            fft_index(kDataSubcarriers[index]);

        const auto h = channel.frequency_response[bin];

        if (std::norm(h) < 1.0e-12F) {
            return result;
        }

        const auto equalized =
            signalFrequency[bin] / h;

        // BPSK: real positivo -> 0, real negativo -> 1.
        /*
         * Convención usada por nuestro generador:
         * BPSK positivo -> bit 1
         * BPSK negativo -> bit 0
         */
        interleavedBits[index] =
            equalized.real() > 0.0F ? 1U : 0U;
    }

    const auto codedBits = deinterleave(interleavedBits);
    const auto decodedBits =
        viterbi_decode_rate_half(codedBits);

    result.decoded_bits = decodedBits;

    std::uint8_t rate = 0U;
    for (std::size_t bit = 0; bit < 4; ++bit) {
        rate |= static_cast<std::uint8_t>(
            decodedBits[bit] << bit
        );
    }

    std::uint16_t length = 0U;
    for (std::size_t bit = 0; bit < 12; ++bit) {
        length |= static_cast<std::uint16_t>(
            decodedBits[5 + bit] << bit
        );
    }

    std::uint8_t parityValue = 0U;
    for (std::size_t bit = 0; bit <= 17; ++bit) {
        parityValue ^= decodedBits[bit];
    }

    bool tailValid = true;
    for (std::size_t bit = 18; bit < 24; ++bit) {
        tailValid = tailValid && decodedBits[bit] == 0U;
    }

    const int rateMbps = rate_field_to_mbps(rate);

    result.rate_field = rate;
    result.length_bytes = length;
    result.parity_valid = parityValue == 0U;
    result.tail_valid = tailValid;
    result.data_rate_mbps = rateMbps;

    result.rate_valid = rateMbps > 0;
    result.length_valid =
        length > 0U && length < 4096U;

    if (!result.rate_valid
        || !result.parity_valid
        || !result.tail_valid
        || !result.length_valid) {
        return result;
    }

    const std::size_t nDbps =
        data_bits_per_symbol(rateMbps);

    const std::size_t requiredBits =
        16U
        + 8U * static_cast<std::size_t>(length)
        + 6U;

    result.number_of_data_symbols =
        (requiredBits + nDbps - 1U) / nDbps;

    result.valid = true;
    return result;
}

std::array<std::uint8_t,48>
LegacySignalDecoder::deinterleave(
    const std::array<std::uint8_t,48>& bits
) {
    std::array<std::uint8_t,48> output{};

    constexpr std::size_t nCbps = 48;

    for (std::size_t k = 0; k < nCbps; ++k) {
        const std::size_t i =
            (nCbps / 16U) * (k % 16U)
            + k / 16U;

        output[k] = bits[i];
    }

    return output;
}

std::array<std::uint8_t,24>
LegacySignalDecoder::viterbi_decode_rate_half(
    const std::array<std::uint8_t,48>& codedBits
) {
    constexpr std::size_t states = 64;
    constexpr int infinity = 1000000;

    std::array<int,states> metrics{};
    std::array<int,states> nextMetrics{};

    metrics.fill(infinity);
    metrics[0] = 0;

    std::array<std::array<std::uint8_t,states>,24>
        predecessor{};

    std::array<std::array<std::uint8_t,states>,24>
        decision{};

    constexpr std::uint8_t generator0 = 0133;
    constexpr std::uint8_t generator1 = 0171;

    for (std::size_t time = 0; time < 24; ++time) {
        nextMetrics.fill(infinity);

        const std::uint8_t received0 =
            codedBits[2U * time];

        const std::uint8_t received1 =
            codedBits[2U * time + 1U];

        for (std::size_t state = 0;
             state < states;
             ++state) {

            if (metrics[state] >= infinity) {
                continue;
            }

            for (std::uint8_t inputBit = 0;
                 inputBit <= 1;
                 ++inputBit) {

                /*
                 * Registro convolucional K=7.
                 *
                 * El bit de entrada nuevo se introduce
                 * por el MSB. Los seis bits anteriores
                 * ocupan las posiciones inferiores.
                 */
                const std::uint8_t shiftRegister =
                    static_cast<std::uint8_t>(
                        (
                            static_cast<std::uint8_t>(
                                inputBit << 6U
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
                            shiftRegister & generator0
                        )
                    );

                const std::uint8_t encoded1 =
                    parity(
                        static_cast<std::uint8_t>(
                            shiftRegister & generator1
                        )
                    );

                const int branchMetric =
                    (encoded0 != received0)
                    + (encoded1 != received1);

                const std::size_t nextState =
                    static_cast<std::size_t>(
                        shiftRegister >> 1U
                    );

                const int candidateMetric =
                    metrics[state] + branchMetric;

                if (candidateMetric
                    < nextMetrics[nextState]) {

                    nextMetrics[nextState] =
                        candidateMetric;

                    predecessor[time][nextState] =
                        static_cast<std::uint8_t>(state);

                    decision[time][nextState] =
                        inputBit;
                }
            }
        }

        metrics = nextMetrics;
    }

    std::size_t finalState = 0U;

    if (metrics[finalState] >= infinity) {
        finalState = static_cast<std::size_t>(
            std::distance(
                metrics.begin(),
                std::min_element(
                    metrics.begin(),
                    metrics.end()
                )
            )
        );
    }

    std::array<std::uint8_t,24> decoded{};

    for (std::size_t reverse = 24;
         reverse > 0;
         --reverse) {

        const std::size_t time = reverse - 1U;

        decoded[time] =
            decision[time][finalState];

        finalState =
            predecessor[time][finalState];
    }

    return decoded;
}

int LegacySignalDecoder::rate_field_to_mbps(
    const std::uint8_t rate
) {
    /*
     * Los cuatro bits RATE se reciben LSB-first y se
     * reconstruyen como:
     *
     * rate = bit0 | bit1<<1 | bit2<<2 | bit3<<3
     *
     * Por eso las representaciones numéricas aparecen
     * invertidas respecto a cómo suelen escribirse en
     * las tablas del estándar.
     */
    switch (rate) {
        case 0b1011: return 6;
        case 0b1111: return 9;
        case 0b1010: return 12;
        case 0b1110: return 18;
        case 0b1001: return 24;
        case 0b1101: return 36;
        case 0b1000: return 48;
        case 0b1100: return 54;
        default: return 0;
    }
}

std::size_t LegacySignalDecoder::data_bits_per_symbol(
    const int rateMbps
) {
    switch (rateMbps) {
        case 6:  return 24;
        case 9:  return 36;
        case 12: return 48;
        case 18: return 72;
        case 24: return 96;
        case 36: return 144;
        case 48: return 192;
        case 54: return 216;
        default: return 0;
    }
}

}  // namespace sensing::wifi_nonht
