#include "waveforms/wifi_nonht/packet_detector.hpp"

#include <algorithm>
#include <cmath>
#include <complex>
#include <numbers>
#include <limits>
#include <stdexcept>

namespace sensing::wifi_nonht {

PacketDetector::PacketDetector(
    DetectorConfig config
)
    : config_(config) {

    if (config_.sample_rate_hz <= 0.0) {
        throw std::invalid_argument(
            "sample_rate_hz inválido"
        );
    }

    if (config_.lag_samples == 0U
        || config_.correlation_window == 0U) {
        throw std::invalid_argument(
            "DetectorConfig inválida"
        );
    }
}

std::vector<Detection> PacketDetector::detect(
    const std::span<const std::complex<float>> samples
) const {
    std::vector<Detection> detections;

    const std::size_t lag =
        config_.lag_samples;

    const std::size_t window =
        config_.correlation_window;

    const std::size_t required =
        lag + window;

    if (samples.size() < required) {
        return detections;
    }

    const std::size_t metric_size =
        samples.size() - required + 1U;

    std::vector<float> metrics(metric_size, 0.0F);
    std::vector<float> powers(metric_size, 0.0F);
    std::vector<std::complex<float>> correlations(
        metric_size,
        {0.0F, 0.0F}
    );

    std::complex<float> correlation{0.0F, 0.0F};
    float delayed_power = 0.0F;

    for (std::size_t k = 0; k < window; ++k) {
        correlation +=
            samples[k]
            * std::conj(samples[k + lag]);

        delayed_power +=
            std::norm(samples[k + lag]);
    }

    for (std::size_t n = 0; n < metric_size; ++n) {
        if (n > 0U) {
            const std::size_t leaving = n - 1U;
            const std::size_t entering =
                n + window - 1U;

            correlation -=
                samples[leaving]
                * std::conj(
                    samples[leaving + lag]
                );

            correlation +=
                samples[entering]
                * std::conj(
                    samples[entering + lag]
                );

            delayed_power -=
                std::norm(
                    samples[leaving + lag]
                );

            delayed_power +=
                std::norm(
                    samples[entering + lag]
                );
        }

        correlations[n] = correlation;
        powers[n] = delayed_power
            / static_cast<float>(window);

        const float denominator =
            delayed_power * delayed_power
            + 1.0e-20F;

        metrics[n] =
            std::norm(correlation)
            / denominator;
    }

    std::size_t plateau_start = 0U;
    std::size_t plateau_length = 0U;
    std::size_t last_detection =
        std::numeric_limits<std::size_t>::max();

    for (std::size_t n = 0;
         n < metric_size;
         ++n) {

        const bool above_threshold =
            metrics[n] >= config_.metric_threshold
            && powers[n] >= config_.minimum_power;

        if (above_threshold) {
            if (plateau_length == 0U) {
                plateau_start = n;
            }

            ++plateau_length;
            continue;
        }

        if (plateau_length
            >= config_.minimum_plateau) {

            const auto begin =
                metrics.begin()
                + static_cast<std::ptrdiff_t>(
                    plateau_start
                );

            const auto end =
                begin
                + static_cast<std::ptrdiff_t>(
                    plateau_length
                );

            const auto maximum =
                std::max_element(begin, end);

            const std::size_t peak_offset =
                plateau_start
                + static_cast<std::size_t>(
                    std::distance(begin, maximum)
                );

            const bool sufficiently_separated =
                detections.empty()
                || peak_offset
                    >= last_detection
                    + config_.minimum_packet_spacing;

            if (sufficiently_separated) {
                const float phase =
                    std::arg(correlations[peak_offset]);

                // El signo exacto se verificará posteriormente
                // contra la captura dorada y MATLAB.
                const float coarse_cfo_hz =
                    phase
                    * static_cast<float>(
                        config_.sample_rate_hz
                    )
                    / (
                        2.0F
                        * std::numbers::pi_v<float>
                        * static_cast<float>(lag)
                    );

                detections.push_back({
                    .sample_offset = peak_offset,
                    .metric = metrics[peak_offset],
                    .power = powers[peak_offset],
                    .coarse_cfo_hz = coarse_cfo_hz
                });

                last_detection = peak_offset;
            }
        }

        plateau_length = 0U;
    }

    return detections;
}

}  // namespace sensing::wifi_nonht
