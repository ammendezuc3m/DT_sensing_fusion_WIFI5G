#pragma once

#include "waveforms/wifi_nonht/synchronizer.hpp"

#include <array>
#include <complex>
#include <cstddef>
#include <span>
#include <vector>

namespace sensing::wifi_nonht {

constexpr std::size_t kWifiFftLength = 64;
constexpr std::size_t kWifiUsedSubcarriers = 52;

struct ChannelEstimate {
    bool valid{false};

    std::size_t packet_start{0};
    std::size_t lltf_start{0};

    float fine_cfo_hz{0.0F};
    float signal_power{0.0F};
    float noise_power{0.0F};
    float snr_db{0.0F};

    std::array<std::complex<float>,kWifiFftLength>
        frequency_response{};

    std::array<std::complex<float>,kWifiUsedSubcarriers>
        used_subcarrier_csi{};
};

class ChannelEstimator {
public:
    ChannelEstimator(
        double sample_rate_hz,
        std::vector<std::complex<float>> known_lltf_frequency
    );

    [[nodiscard]]
    ChannelEstimate estimate(
        std::span<const std::complex<float>> samples,
        const SyncResult& sync
    ) const;

private:
    double sample_rate_hz_{20.0e6};

    std::array<std::complex<float>,kWifiFftLength>
        known_lltf_frequency_{};
};

}  // namespace sensing::wifi_nonht
