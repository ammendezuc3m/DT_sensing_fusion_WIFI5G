#pragma once

#include "waveforms/wifi_nonht/packet_detector.hpp"

#include <complex>
#include <cstddef>
#include <span>
#include <vector>

namespace sensing::wifi_nonht {

struct SyncResult {
    bool valid{false};

    std::size_t coarse_offset{0};
    std::size_t packet_start{0};
    std::size_t lltf_start{0};

    float coarse_cfo_hz{0.0F};
    float preamble_metric{0.0F};
    float signal_power{0.0F};
};

struct SynchronizerConfig {
    double sample_rate_hz{20.0e6};

    std::size_t search_before{256};
    std::size_t search_after{512};

    float minimum_preamble_metric{0.55F};

    // Rechazo preliminar de candidatos absurdos.
    float maximum_absolute_coarse_cfo_hz{200000.0F};
};

class Synchronizer {
public:
    Synchronizer(
        SynchronizerConfig config,
        std::vector<std::complex<float>> preamble_reference
    );

    [[nodiscard]]
    SyncResult synchronize(
        std::span<const std::complex<float>> samples,
        const Detection& detection
    ) const;

private:
    SynchronizerConfig config_;
    std::vector<std::complex<float>> reference_;
    float reference_energy_{0.0F};
};

}  // namespace sensing::wifi_nonht
