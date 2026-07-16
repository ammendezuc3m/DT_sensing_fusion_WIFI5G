#pragma once

#include "waveforms/wifi_nonht/channel_estimator.hpp"
#include "waveforms/wifi_nonht/packet_detector.hpp"
#include "waveforms/wifi_nonht/synchronizer.hpp"

#include <cstdint>
#include <filesystem>
#include <fstream>

namespace sensing::io {

class CsiCsvWriter {
public:
    explicit CsiCsvWriter(
        const std::filesystem::path& path
    );

    CsiCsvWriter(const CsiCsvWriter&) = delete;
    CsiCsvWriter& operator=(const CsiCsvWriter&) = delete;

    void write(
        std::uint64_t packet_index,
        std::uint64_t global_sample_offset,
        double sample_rate_hz,
        const wifi_nonht::Detection& detection,
        const wifi_nonht::SyncResult& sync,
        const wifi_nonht::ChannelEstimate& channel
    );

private:
    std::ofstream output_;

    void write_header();
};

}  // namespace sensing::io
