#include "io/csi_csv_writer.hpp"

#include <iomanip>
#include <stdexcept>

namespace sensing::io {

CsiCsvWriter::CsiCsvWriter(
    const std::filesystem::path& path
)
    : output_(path) {

    if (!output_) {
        throw std::runtime_error(
            "No se pudo crear CSV: " + path.string()
        );
    }

    write_header();
}

void CsiCsvWriter::write_header() {
    output_
        << "packet_index"
        << ",sample_offset"
        << ",time_seconds"
        << ",stf_metric"
        << ",preamble_metric"
        << ",coarse_cfo_hz"
        << ",fine_cfo_hz"
        << ",signal_power"
        << ",noise_power"
        << ",snr_db";

    for (std::size_t index = 0;
         index < wifi_nonht::kWifiUsedSubcarriers;
         ++index) {

        output_
            << ",csi_" << index << "_real"
            << ",csi_" << index << "_imag";
    }

    output_ << '\n';
}

void CsiCsvWriter::write(
    const std::uint64_t packet_index,
    const std::uint64_t global_sample_offset,
    const double sample_rate_hz,
    const wifi_nonht::Detection& detection,
    const wifi_nonht::SyncResult& sync,
    const wifi_nonht::ChannelEstimate& channel
) {
    const double time_seconds =
        static_cast<double>(global_sample_offset)
        / sample_rate_hz;

    output_
        << packet_index
        << ',' << global_sample_offset
        << ',' << std::fixed << std::setprecision(9)
        << time_seconds
        << ',' << detection.metric
        << ',' << sync.preamble_metric
        << ',' << detection.coarse_cfo_hz
        << ',' << channel.fine_cfo_hz
        << ',' << channel.signal_power
        << ',' << channel.noise_power
        << ',' << channel.snr_db;

    for (const auto value :
         channel.used_subcarrier_csi) {

        output_
            << ',' << value.real()
            << ',' << value.imag();
    }

    output_ << '\n';
}

}  // namespace sensing::io
