#pragma once

#include "waveform_types.hpp"

#include <cstdint>

namespace sensing {

struct PacketMetadata {
    std::uint8_t protocol_version{1};
    WaveformType waveform_type{WaveformType::Unknown};
    std::uint16_t profile_id{0};

    std::uint16_t transmitter_id{0};
    std::uint32_t experiment_id{0};
    std::uint64_t packet_counter{0};

    std::uint16_t sequence_number{0};

    std::uint64_t tx_timestamp_ns{0};
    std::uint64_t rx_timestamp_ns{0};

    double center_frequency_hz{0.0};
    double sample_rate_hz{0.0};

    float snr_db{0.0F};
    float cfo_hz{0.0F};
    float rssi_proxy_dbfs{0.0F};
};

}  // namespace sensing
