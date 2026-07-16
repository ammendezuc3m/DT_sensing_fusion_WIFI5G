#include "waveforms/wifi_nonht/wifi_nonht_processor.hpp"

#include <algorithm>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <utility>
#include <vector>

namespace sensing::wifi_nonht {

WifiNonHtProcessor::WifiNonHtProcessor(
    WifiNonHtProcessorConfig config,
    const double sample_rate_hz
)
    : config_(std::move(config)),
      detector_(config_.detector),
      synchronizer_(
          config_.synchronizer,
          config_.preamble_reference
      ),
      channel_estimator_(
          sample_rate_hz,
          config_.lltf_frequency_reference
      ),
      legacy_signal_decoder_(),
      data_symbol_extractor_(),
      data_decoder_(config_.data_decoder),
      beacon_parser_() {
}

std::string WifiNonHtProcessor::name() const {
    return "wifi_nonht";
}

std::vector<FeatureFrame>
WifiNonHtProcessor::process(
    const IqBlock& block,
    ProcessingStats& stats
) {
    std::vector<FeatureFrame> frames;

    const auto detections =
        detector_.detect(block.samples);

    frames.reserve(detections.size());

    for (const auto& detection : detections) {
        ++stats.candidates;

        const std::uint64_t global_detection_offset =
            block.first_sample_index
            + static_cast<std::uint64_t>(
                detection.sample_offset
            );

        if (is_duplicate(global_detection_offset)) {
            ++stats.rejected;
            continue;
        }

        const auto sync =
            synchronizer_.synchronize(
                block.samples,
                detection
            );

        if (!sync.valid) {
            ++stats.rejected;
            continue;
        }

        ++stats.synchronized;

        const auto channel =
            channel_estimator_.estimate(
                block.samples,
                sync
            );

        if (!channel.valid) {
            ++stats.rejected;
            continue;
        }

        const auto lsig =
            legacy_signal_decoder_.decode(
                block.samples,
                sync,
                channel
            );

        if (!lsig.valid) {
            ++stats.rejected;
            continue;
        }

        const auto data_symbols =
            data_symbol_extractor_.extract(
                block.samples,
                sync,
                channel,
                lsig
            );

        if (!data_symbols.valid) {
            ++stats.rejected;
            continue;
        }

        const auto data =
            data_decoder_.decode(
                data_symbols,
                lsig
            );

        if (!data.valid) {
            ++stats.rejected;
            continue;
        }

        ++stats.decoded;

        const auto beacon =
            beacon_parser_.parse(
                data.psdu_bytes
            );

        if (!passes_filters(beacon)) {
            ++stats.rejected;
            continue;
        }

        const std::uint64_t global_packet_start =
            block.first_sample_index
            + static_cast<std::uint64_t>(
                sync.packet_start
            );

        frames.push_back(
            make_feature_frame(
                block,
                global_packet_start,
                detection,
                sync,
                channel,
                lsig,
                data,
                beacon
            )
        );

        ++stats.published;
    }

    return frames;
}

void WifiNonHtProcessor::reset() {
    has_last_detection_ = false;
    last_global_detection_ = 0U;
}

bool WifiNonHtProcessor::is_duplicate(
    const std::uint64_t global_detection_offset
) {
    if (!has_last_detection_) {
        has_last_detection_ = true;
        last_global_detection_ =
            global_detection_offset;

        return false;
    }

    const bool duplicate =
        global_detection_offset
        <= last_global_detection_
            + config_.duplicate_tolerance_samples;

    if (!duplicate) {
        last_global_detection_ =
            global_detection_offset;
    }

    return duplicate;
}

bool WifiNonHtProcessor::passes_filters(
    const ParsedBeacon& beacon
) const {
    const auto& filters = config_.filters;

    if (
        filters.require_valid_fcs
        && !beacon.fcs_valid
    ) {
        return false;
    }

    if (
        filters.require_beacon
        && !beacon.valid
    ) {
        return false;
    }

    if (
        !filters.ssid.empty()
        && beacon.ssid != filters.ssid
    ) {
        return false;
    }

    if (
        !filters.bssid.empty()
        && beacon.bssid_string
            != filters.bssid
    ) {
        return false;
    }

    if (
        filters.require_vendor_oui
        && beacon.vendor_oui
            != filters.vendor_oui
    ) {
        return false;
    }

    if (
        filters.require_vendor_type
        && beacon.vendor_type
            != filters.vendor_type
    ) {
        return false;
    }

    if (
        !filters.vendor_magic.empty()
        && beacon.vendor_magic
            != filters.vendor_magic
    ) {
        return false;
    }

    if (
        filters.require_vendor_version
        && beacon.vendor_version
            != filters.vendor_version
    ) {
        return false;
    }

    if (
        filters.require_transmitter_id
        && beacon.transmitter_id.value_or(0U)
            != filters.transmitter_id
    ) {
        return false;
    }

    if (
        filters.require_experiment_id
        && beacon.experiment_id.value_or(0U)
            != filters.experiment_id
    ) {
        return false;
    }

    return true;
}

FeatureFrame WifiNonHtProcessor::make_feature_frame(
    const IqBlock& block,
    const std::uint64_t global_packet_start,
    const Detection& detection,
    const SyncResult& sync,
    const ChannelEstimate& channel,
    const LegacySignalResult& lsig,
    const DataDecodeResult& data,
    const ParsedBeacon& beacon
) const {
    FeatureFrame frame;

    /*
     * Si tu enum utiliza otro nombre, por ejemplo
     * WifiNonHT, cambia únicamente esta línea.
     */
    frame.waveform_type =
        WaveformType::WifiNonHtBeacon;

    frame.profile_id =
        config_.profile_id;

    frame.transmitter_id =
        beacon.transmitter_id.value_or(0U);

    frame.experiment_id =
        beacon.experiment_id.value_or(0U);

    frame.packet_counter =
        beacon.packet_counter.value_or(0U);

    frame.sample_offset =
        global_packet_start;

    frame.sample_rate_hz =
        block.sample_rate_hz;

    frame.center_frequency_hz =
        block.center_frequency_hz;

    if (block.timestamp_ns != 0U) {
        const std::uint64_t relative_samples =
            global_packet_start
            - block.first_sample_index;

        const auto relative_time_ns =
            static_cast<std::uint64_t>(
                (
                    static_cast<double>(
                        relative_samples
                    )
                    / block.sample_rate_hz
                )
                * 1.0e9
            );

        frame.rx_timestamp_ns =
            block.timestamp_ns
            + relative_time_ns;
    } else {
        frame.rx_timestamp_ns =
            static_cast<std::uint64_t>(
                (
                    static_cast<double>(
                        global_packet_start
                    )
                    / block.sample_rate_hz
                )
                * 1.0e9
            );
    }

    frame.snr_db =
        channel.snr_db;

    frame.cfo_hz =
        detection.coarse_cfo_hz
        + channel.fine_cfo_hz;

    frame.power_dbfs =
        10.0F
        * std::log10(
            std::max(
                detection.power,
                1.0e-20F
            )
        );

    frame.numeric_metadata[
        "sequence_number"
    ] = static_cast<double>(
        beacon.sequence_number
    );

    frame.numeric_metadata[
        "fragment_number"
    ] = static_cast<double>(
        beacon.fragment_number
    );

    frame.numeric_metadata[
        "data_rate_mbps"
    ] = static_cast<double>(
        lsig.data_rate_mbps
    );

    frame.numeric_metadata[
        "psdu_length_bytes"
    ] = static_cast<double>(
        lsig.length_bytes
    );

    frame.numeric_metadata[
        "number_of_data_symbols"
    ] = static_cast<double>(
        lsig.number_of_data_symbols
    );

    frame.numeric_metadata[
        "stf_metric"
    ] = static_cast<double>(
        detection.metric
    );

    frame.numeric_metadata[
        "preamble_metric"
    ] = static_cast<double>(
        sync.preamble_metric
    );

    frame.numeric_metadata[
        "coarse_cfo_hz"
    ] = static_cast<double>(
        detection.coarse_cfo_hz
    );

    frame.numeric_metadata[
        "fine_cfo_hz"
    ] = static_cast<double>(
        channel.fine_cfo_hz
    );

    frame.numeric_metadata[
        "beacon_interval_tu"
    ] = static_cast<double>(
        beacon.beacon_interval_tu
    );

    frame.numeric_metadata[
        "vendor_type"
    ] = static_cast<double>(
        beacon.vendor_type
    );

    frame.numeric_metadata[
        "vendor_version"
    ] = static_cast<double>(
        beacon.vendor_version
    );

    frame.text_metadata["ssid"] =
        beacon.ssid;

    frame.text_metadata["bssid"] =
        beacon.bssid_string;

    frame.text_metadata["vendor_magic"] =
        beacon.vendor_magic;

    frame.complex_features.assign(
        channel.used_subcarrier_csi.begin(),
        channel.used_subcarrier_csi.end()
    );

    if (config_.include_psdu) {
        frame.payload =
            data.psdu_bytes;
    }

    return frame;
}

}  // namespace sensing::wifi_nonht
