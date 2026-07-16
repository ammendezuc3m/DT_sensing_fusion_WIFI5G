#include "waveforms/wifi_nonht/synchronizer.hpp"

#include <algorithm>
#include <cmath>
#include <complex>
#include <numbers>
#include <stdexcept>

namespace sensing::wifi_nonht {

Synchronizer::Synchronizer(
    SynchronizerConfig config,
    std::vector<std::complex<float>> preamble_reference
)
    : config_(config),
      reference_(std::move(preamble_reference)) {

    if (config_.sample_rate_hz <= 0.0) {
        throw std::invalid_argument(
            "sample_rate_hz inválido"
        );
    }

    if (reference_.empty()) {
        throw std::invalid_argument(
            "Referencia de preámbulo vacía"
        );
    }

    for (const auto sample : reference_) {
        reference_energy_ += std::norm(sample);
    }

    if (reference_energy_ <= 0.0F) {
        throw std::invalid_argument(
            "Referencia sin energía"
        );
    }
}

SyncResult Synchronizer::synchronize(
    const std::span<const std::complex<float>> samples,
    const Detection& detection
) const {
    SyncResult result;
    result.coarse_offset = detection.sample_offset;
    result.coarse_cfo_hz = detection.coarse_cfo_hz;
    result.signal_power = detection.power;

    if (std::abs(detection.coarse_cfo_hz)
        > config_.maximum_absolute_coarse_cfo_hz) {
        return result;
    }

    const std::size_t reference_length =
        reference_.size();

    if (samples.size() < reference_length) {
        return result;
    }

    const std::size_t search_start =
        detection.sample_offset > config_.search_before
        ? detection.sample_offset - config_.search_before
        : 0U;

    const std::size_t maximum_start =
        samples.size() - reference_length;

    const std::size_t search_end =
        std::min(
            maximum_start,
            detection.sample_offset
                + config_.search_after
        );

    if (search_start > search_end) {
        return result;
    }

    const float phase_increment =
        -2.0F
        * std::numbers::pi_v<float>
        * detection.coarse_cfo_hz
        / static_cast<float>(
            config_.sample_rate_hz
        );

    float best_metric = 0.0F;
    std::size_t best_start = 0U;

    for (std::size_t candidate = search_start;
         candidate <= search_end;
         ++candidate) {

        std::complex<float> correlation{
            0.0F,
            0.0F
        };
        float received_energy = 0.0F;

        for (std::size_t k = 0;
             k < reference_length;
             ++k) {

            const float phase =
                phase_increment
                * static_cast<float>(k);

            const std::complex<float> correction{
                std::cos(phase),
                std::sin(phase)
            };

            const auto corrected =
                samples[candidate + k]
                * correction;

            correlation +=
                corrected
                * std::conj(reference_[k]);

            received_energy +=
                std::norm(corrected);
        }

        const float denominator =
            std::sqrt(
                received_energy
                * reference_energy_
            ) + 1.0e-20F;

        const float metric =
            std::abs(correlation)
            / denominator;

        if (metric > best_metric) {
            best_metric = metric;
            best_start = candidate;
        }
    }

    if (best_metric
        < config_.minimum_preamble_metric) {
        return result;
    }

    result.valid = true;
    result.packet_start = best_start;
    result.lltf_start = best_start + 160U;
    result.preamble_metric = best_metric;

    return result;
}

}  // namespace sensing::wifi_nonht
