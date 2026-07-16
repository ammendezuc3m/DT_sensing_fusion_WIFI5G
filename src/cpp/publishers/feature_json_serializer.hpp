#pragma once

#include "common/feature_frame.hpp"

#include <nlohmann/json.hpp>

#include <cstdint>
#include <string>

namespace sensing {

inline nlohmann::json feature_frame_to_json(
    const FeatureFrame& frame
) {
    nlohmann::json output;

    output["protocol_version"] =
        frame.protocol_version;

    output["waveform_type"] =
        static_cast<std::uint32_t>(
            frame.waveform_type
        );

    output["profile_id"] =
        frame.profile_id;

    output["transmitter_id"] =
        frame.transmitter_id;

    output["experiment_id"] =
        frame.experiment_id;

    output["packet_counter"] =
        frame.packet_counter;

    output["sample_offset"] =
        frame.sample_offset;

    output["rx_timestamp_ns"] =
        frame.rx_timestamp_ns;

    output["sample_rate_hz"] =
        frame.sample_rate_hz;

    output["center_frequency_hz"] =
        frame.center_frequency_hz;

    output["snr_db"] =
        frame.snr_db;

    output["cfo_hz"] =
        frame.cfo_hz;

    output["power_dbfs"] =
        frame.power_dbfs;

    output["numeric_metadata"] =
        frame.numeric_metadata;

    output["text_metadata"] =
        frame.text_metadata;

    output["real_features"] =
        frame.real_features;

    nlohmann::json complex_features =
        nlohmann::json::array();

    for (
        const auto& value :
        frame.complex_features
    ) {
        complex_features.push_back({
            {"real", value.real()},
            {"imag", value.imag()},
        });
    }

    output["complex_features"] =
        std::move(complex_features);

    if (!frame.payload.empty()) {
        output["payload"] =
            frame.payload;
    }

    return output;
}

inline std::string feature_frame_to_json_string(
    const FeatureFrame& frame
) {
    return feature_frame_to_json(frame).dump();
}

}  // namespace sensing
