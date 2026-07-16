#include "waveforms/wifi_nonht/data_symbol_extractor.hpp"

#include <fftw3.h>

#include <array>
#include <complex>
#include <cstddef>
#include <stdexcept>

namespace sensing::wifi_nonht {

namespace {

constexpr std::size_t kFftLength = 64;
constexpr std::size_t kCpLength = 16;
constexpr std::size_t kOfdmSymbolLength = 80;
constexpr std::size_t kLegacyPreambleLength = 320;
constexpr std::size_t kLegacySignalLength = 80;

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

    auto* input_pointer =
        reinterpret_cast<fftwf_complex*>(
            const_cast<std::complex<float>*>(
                input.data()
            )
        );

    auto* output_pointer =
        reinterpret_cast<fftwf_complex*>(
            output.data()
        );

    fftwf_plan plan = fftwf_plan_dft_1d(
        64,
        input_pointer,
        output_pointer,
        FFTW_FORWARD,
        FFTW_ESTIMATE
    );

    if (plan == nullptr) {
        throw std::runtime_error(
            "No se pudo crear FFT DATA"
        );
    }

    fftwf_execute(plan);
    fftwf_destroy_plan(plan);

    return output;
}

}  // namespace

DataSymbolsResult DataSymbolExtractor::extract(
    const std::span<const std::complex<float>> samples,
    const SyncResult& sync,
    const ChannelEstimate& channel,
    const LegacySignalResult& lsig
) const {
    DataSymbolsResult result;

    if (!sync.valid || !channel.valid || !lsig.valid) {
        return result;
    }

    const std::size_t data_start =
        sync.packet_start
        + kLegacyPreambleLength
        + kLegacySignalLength;

    const std::size_t required_samples =
        lsig.number_of_data_symbols
        * kOfdmSymbolLength;

    if (data_start + required_samples > samples.size()) {
        return result;
    }

    result.number_of_symbols =
        lsig.number_of_data_symbols;

    result.equalized_data_subcarriers.reserve(
        lsig.number_of_data_symbols
        * kDataSubcarriers.size()
    );

    for (std::size_t symbol_index = 0;
         symbol_index < lsig.number_of_data_symbols;
         ++symbol_index) {

        const std::size_t symbol_start =
            data_start
            + symbol_index * kOfdmSymbolLength
            + kCpLength;

        std::array<std::complex<float>,64> time_symbol{};

        for (std::size_t sample_index = 0;
             sample_index < kFftLength;
             ++sample_index) {

            time_symbol[sample_index] =
                samples[symbol_start + sample_index];
        }

        const auto frequency_symbol = fft64(time_symbol);

        for (const int subcarrier : kDataSubcarriers) {
            const std::size_t bin =
                fft_index(subcarrier);

            const auto h =
                channel.frequency_response[bin];

            if (std::norm(h) < 1.0e-12F) {
                return DataSymbolsResult{};
            }

            result.equalized_data_subcarriers.push_back(
                frequency_symbol[bin] / h
            );
        }
    }

    result.valid = true;
    return result;
}

}  // namespace sensing::wifi_nonht
