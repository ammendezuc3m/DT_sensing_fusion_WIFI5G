#include "waveforms/wifi_nonht/channel_estimator.hpp"

#include <fftw3.h>

#include <algorithm>
#include <array>
#include <cmath>
#include <complex>
#include <numbers>
#include <stdexcept>

namespace sensing::wifi_nonht {

namespace {

constexpr std::size_t kCyclicPrefixLength = 32;
constexpr std::size_t kLltfTotalLength = 160;

constexpr float kKnownLltfMinimumMagnitudeSquared =
    1.0e-8F;

/*
 * Subportadoras activas legacy WiFi en orden físico:
 *
 * -26, ..., -1, +1, ..., +26
 *
 * No se incluyen DC ni guardas.
 */
constexpr std::array<int,kWifiUsedSubcarriers>
    kUsedSubcarriers = {
        -26, -25, -24, -23, -22, -21, -20,
        -19, -18, -17, -16, -15, -14, -13,
        -12, -11, -10, -9, -8, -7, -6,
        -5, -4, -3, -2, -1,
         1, 2, 3, 4, 5, 6, 7,
         8, 9, 10, 11, 12, 13, 14,
         15, 16, 17, 18, 19, 20, 21,
         22, 23, 24, 25, 26
    };

std::size_t fft_bin(const int subcarrier) {
    if (subcarrier >= 0) {
        return static_cast<std::size_t>(
            subcarrier
        );
    }

    return static_cast<std::size_t>(
        static_cast<int>(kWifiFftLength)
        + subcarrier
    );
}

std::array<std::complex<float>,kWifiFftLength>
fft64(
    const std::array<std::complex<float>,kWifiFftLength>& input
) {
    std::array<std::complex<float>,kWifiFftLength> output{};

    auto* inputPtr = reinterpret_cast<fftwf_complex*>(
        const_cast<std::complex<float>*>(input.data())
    );

    auto* outputPtr = reinterpret_cast<fftwf_complex*>(
        output.data()
    );

    fftwf_plan plan = fftwf_plan_dft_1d(
        static_cast<int>(kWifiFftLength),
        inputPtr,
        outputPtr,
        FFTW_FORWARD,
        FFTW_ESTIMATE
    );

    if (plan == nullptr) {
        throw std::runtime_error(
            "No se pudo crear el plan FFTW"
        );
    }

    fftwf_execute(plan);
    fftwf_destroy_plan(plan);

    return output;
}

}  // namespace

ChannelEstimator::ChannelEstimator(
    const double sample_rate_hz,
    std::vector<std::complex<float>> known_lltf_frequency
)
    : sample_rate_hz_(sample_rate_hz) {

    if (sample_rate_hz_ <= 0.0) {
        throw std::invalid_argument(
            "sample_rate_hz invalido"
        );
    }

    if (known_lltf_frequency.size() != kWifiFftLength) {
        throw std::invalid_argument(
            "La referencia L-LTF debe contener 64 bins"
        );
    }

    for (std::size_t index = 0;
         index < kWifiFftLength;
         ++index) {

        known_lltf_frequency_[index] =
            known_lltf_frequency[index];
    }
}

ChannelEstimate ChannelEstimator::estimate(
    const std::span<const std::complex<float>> samples,
    const SyncResult& sync
) const {
    ChannelEstimate result;
    result.packet_start = sync.packet_start;
    result.lltf_start = sync.lltf_start;

    if (!sync.valid) {
        return result;
    }

    if (sync.lltf_start + kLltfTotalLength
        > samples.size()) {
        return result;
    }

    const std::size_t firstStart =
        sync.lltf_start + kCyclicPrefixLength;

    const std::size_t secondStart =
        firstStart + kWifiFftLength;

    std::array<std::complex<float>,kWifiFftLength>
        firstTime{};

    std::array<std::complex<float>,kWifiFftLength>
        secondTime{};

    std::complex<float> repeatedCorrelation{
        0.0F,
        0.0F
    };

    for (std::size_t i = 0;
         i < kWifiFftLength;
         ++i) {

        firstTime[i] = samples[firstStart + i];
        secondTime[i] = samples[secondStart + i];

        repeatedCorrelation +=
            firstTime[i]
            * std::conj(secondTime[i]);
    }

    const float phase =
        std::arg(repeatedCorrelation);

    result.fine_cfo_hz =
        phase
        * static_cast<float>(sample_rate_hz_)
        / (
            2.0F
            * std::numbers::pi_v<float>
            * static_cast<float>(kWifiFftLength)
        );

    /*
     * Corregir el segundo símbolo respecto del primero.
     */
    const std::complex<float> correction{
        std::cos(-phase),
        std::sin(-phase)
    };

    for (auto& sample : secondTime) {
        sample *= correction;
    }

    const auto firstFrequency = fft64(firstTime);
    const auto secondFrequency = fft64(secondTime);

    float signalPower = 0.0F;
    float noisePower = 0.0F;

    /*
     * Inicializar los 64 bins de la respuesta de canal.
     * DC y las guardas permanecen a cero.
     */
    for (std::size_t bin = 0;
         bin < kWifiFftLength;
         ++bin) {

        result.frequency_response[bin] = {
            0.0F,
            0.0F
        };
    }

    /*
     * Construir el CSI exclusivamente con:
     *
     * -26, ..., -1, +1, ..., +26.
     */
    for (std::size_t usedIndex = 0;
         usedIndex < kUsedSubcarriers.size();
         ++usedIndex) {

        const std::size_t bin =
            fft_bin(
                kUsedSubcarriers[usedIndex]
            );

        const auto known =
            known_lltf_frequency_[bin];

        if (
            std::norm(known)
            < kKnownLltfMinimumMagnitudeSquared
        ) {
            return result;
        }

        const auto averageReceived =
            0.5F
            * (
                firstFrequency[bin]
                + secondFrequency[bin]
            );

        const auto difference =
            firstFrequency[bin]
            - secondFrequency[bin];

        const auto h =
            averageReceived / known;

        result.frequency_response[bin] = h;

        result.used_subcarrier_csi[
            usedIndex
        ] = h;

        signalPower +=
            std::norm(averageReceived);

        noisePower +=
            0.5F
            * std::norm(difference);
    }
    signalPower /= static_cast<float>(
        kWifiUsedSubcarriers
    );

    noisePower /= static_cast<float>(
        kWifiUsedSubcarriers
    );

    result.signal_power = signalPower;
    result.noise_power = noisePower;

    result.snr_db =
        10.0F
        * std::log10(
            signalPower
            / std::max(noisePower,1.0e-20F)
        );

    result.valid = true;
    return result;
}

}  // namespace sensing::wifi_nonht
