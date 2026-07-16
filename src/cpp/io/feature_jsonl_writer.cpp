#include "io/feature_jsonl_writer.hpp"

#include <nlohmann/json.hpp>

#include <complex>
#include <cstdint>
#include <filesystem>
#include <stdexcept>
#include <vector>

namespace sensing::io {

FeatureJsonlWriter::FeatureJsonlWriter(
    const std::filesystem::path& path
) {
    if (
        path.has_parent_path()
        && !path.parent_path().empty()
    ) {
        std::filesystem::create_directories(
            path.parent_path()
        );
    }

    output_.open(path);

    if (!output_) {
        throw std::runtime_error(
            "No se pudo crear el fichero JSONL: "
            + path.string()
        );
    }
}

void FeatureJsonlWriter::write(
    const FeatureFrame& frame
) {
    nlohmann::json object;

    object["protocol_version"] =
        frame.protocol_version;

    object["waveform_type"] =
        static_cast<std::uint8_t>(
            frame.waveform_type
        );

    object["profile_id"] =
        frame.profile_id;

    object["transmitter_id"] =
        frame.transmitter_id;

    object["experiment_id"] =
        frame.experiment_id;

    object["packet_counter"] =
        frame.packet_counter;

    object["sample_offset"] =
        frame.sample_offset;

    object["rx_timestamp_ns"] =
        frame.rx_timestamp_ns;

    object["sample_rate_hz"] =
        frame.sample_rate_hz;

    object["center_frequency_hz"] =
        frame.center_frequency_hz;

    object["snr_db"] =
        frame.snr_db;

    object["cfo_hz"] =
        frame.cfo_hz;

    object["power_dbfs"] =
        frame.power_dbfs;

    object["numeric_metadata"] =
        frame.numeric_metadata;

    object["text_metadata"] =
        frame.text_metadata;

    nlohmann::json complex_features =
        nlohmann::json::array();

    for (const auto& value :
         frame.complex_features) {

        complex_features.push_back({
            {"real", value.real()},
            {"imag", value.imag()}
        });
    }

    object["complex_features"] =
        std::move(complex_features);

    object["real_features"] =
        frame.real_features;

    object["payload"] =
        frame.payload;

    output_ << object.dump() << '\n';
    output_.flush();

    if (!output_) {
        throw std::runtime_error(
            "Error escribiendo FeatureFrame"
        );
    }
}

}  // namespace sensing::io
