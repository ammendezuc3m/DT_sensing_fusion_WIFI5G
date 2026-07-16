#pragma once

#include "common/waveform_types.hpp"

#include <complex>
#include <cstdint>
#include <string>
#include <unordered_map>
#include <vector>

namespace sensing {

struct FeatureFrame {
    std::uint8_t protocol_version{1};
    WaveformType waveform_type{WaveformType::Unknown};

    std::uint16_t profile_id{0};
    std::uint16_t transmitter_id{0};
    std::uint32_t experiment_id{0};
    std::uint64_t packet_counter{0};

    std::uint64_t sample_offset{0};
    std::uint64_t rx_timestamp_ns{0};

    double sample_rate_hz{0.0};
    double center_frequency_hz{0.0};

    float snr_db{0.0F};
    float cfo_hz{0.0F};
    float power_dbfs{0.0F};

    /*
     * Metadatos escalares específicos de la waveform.
     *
     * Ejemplos WiFi:
     *   sequence_number
     *   mpdu_length
     *   data_rate_mbps
     *
     * Ejemplos beamforming:
     *   beam_id
     *   codebook_id
     *   azimuth_index
     */
    std::unordered_map<std::string,double>
        numeric_metadata;

    std::unordered_map<std::string,std::string>
        text_metadata;

    /*
     * Vector complejo principal.
     *
     * WiFi: CSI por subportadora.
     * BF: respuesta por beam o canal.
     * SSB: estimación de canal.
     */
    std::vector<std::complex<float>>
        complex_features;

    /*
     * Vector real auxiliar.
     *
     * Potencias, correlaciones, Doppler, etc.
     */
    std::vector<float> real_features;

    std::vector<std::uint8_t> payload;
};

}  // namespace sensing
