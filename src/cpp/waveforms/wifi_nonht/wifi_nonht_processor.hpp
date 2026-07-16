#pragma once

#include "pipeline/waveform_processor.hpp"

#include "waveforms/wifi_nonht/beacon_parser.hpp"
#include "waveforms/wifi_nonht/channel_estimator.hpp"
#include "waveforms/wifi_nonht/data_decoder.hpp"
#include "waveforms/wifi_nonht/data_symbol_extractor.hpp"
#include "waveforms/wifi_nonht/legacy_signal_decoder.hpp"
#include "waveforms/wifi_nonht/packet_detector.hpp"
#include "waveforms/wifi_nonht/synchronizer.hpp"

#include <array>
#include <complex>
#include <cstddef>
#include <cstdint>
#include <string>
#include <vector>

namespace sensing::wifi_nonht {

struct WifiNonHtFilters {
    bool require_valid_fcs{true};
    bool require_beacon{true};

    std::string ssid;
    std::string bssid;

    bool require_vendor_oui{false};
    std::array<std::uint8_t,3> vendor_oui{};

    bool require_vendor_type{false};
    std::uint8_t vendor_type{0};

    std::string vendor_magic;

    bool require_vendor_version{false};
    std::uint8_t vendor_version{0};

    bool require_transmitter_id{false};
    std::uint16_t transmitter_id{0};

    bool require_experiment_id{false};
    std::uint16_t experiment_id{0};
};

struct WifiNonHtProcessorConfig {
    std::uint16_t profile_id{0};

    DetectorConfig detector;
    SynchronizerConfig synchronizer;
    DataDecoderConfig data_decoder;

    std::vector<std::complex<float>>
        preamble_reference;

    std::vector<std::complex<float>>
        lltf_frequency_reference;

    WifiNonHtFilters filters;

    /*
     * Dos detecciones separadas menos que esta cantidad
     * de muestras se consideran el mismo paquete.
     */
    std::uint64_t duplicate_tolerance_samples{512U};

    /*
     * Incluir PSDU en FeatureFrame aumenta el tamaño de
     * salida. El CSI se incluye siempre.
     */
    bool include_psdu{false};
};

class WifiNonHtProcessor final
    : public IWaveformProcessor {

public:
    WifiNonHtProcessor(
        WifiNonHtProcessorConfig config,
        double sample_rate_hz
    );

    [[nodiscard]]
    std::string name() const override;

    std::vector<FeatureFrame> process(
        const IqBlock& block,
        ProcessingStats& stats
    ) override;

    void reset() override;

private:
    [[nodiscard]]
    bool is_duplicate(
        std::uint64_t global_detection_offset
    );

    [[nodiscard]]
    bool passes_filters(
        const ParsedBeacon& beacon
    ) const;

    [[nodiscard]]
    FeatureFrame make_feature_frame(
        const IqBlock& block,
        std::uint64_t global_packet_start,
        const Detection& detection,
        const SyncResult& sync,
        const ChannelEstimate& channel,
        const LegacySignalResult& lsig,
        const DataDecodeResult& data,
        const ParsedBeacon& beacon
    ) const;

    WifiNonHtProcessorConfig config_;

    PacketDetector detector_;
    Synchronizer synchronizer_;
    ChannelEstimator channel_estimator_;
    LegacySignalDecoder legacy_signal_decoder_;
    DataSymbolExtractor data_symbol_extractor_;
    DataDecoder data_decoder_;
    BeaconParser beacon_parser_;

    bool has_last_detection_{false};
    std::uint64_t last_global_detection_{0U};
};

}  // namespace sensing::wifi_nonht
