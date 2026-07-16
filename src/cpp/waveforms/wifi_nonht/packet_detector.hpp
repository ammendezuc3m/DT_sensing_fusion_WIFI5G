#pragma once

#include <complex>
#include <cstddef>
#include <span>
#include <vector>

namespace sensing::wifi_nonht {

struct Detection {
    std::size_t sample_offset{0};
    float metric{0.0F};
    float power{0.0F};
    float coarse_cfo_hz{0.0F};
};

struct DetectorConfig {
    double sample_rate_hz{20.0e6};

    // Periodicidad del L-STF a 20 Msps.
    std::size_t lag_samples{16};

    // Ventana de correlación.
    std::size_t correlation_window{64};

    float metric_threshold{0.70F};

    // Número mínimo de muestras consecutivas
    // por encima del umbral.
    std::size_t minimum_plateau{32};

    // Evita divisiones inestables en zonas sin señal.
    float minimum_power{1.0e-7F};

    // Evita varias detecciones del mismo paquete.
    std::size_t minimum_packet_spacing{2000};
};

class PacketDetector {
public:
    explicit PacketDetector(DetectorConfig config);

    [[nodiscard]]
    std::vector<Detection> detect(
        std::span<const std::complex<float>> samples
    ) const;

private:
    DetectorConfig config_;
};

}  // namespace sensing::wifi_nonht
