#include "io/sc16_reader.hpp"
#include "io/cf32_reader.hpp"
#include "io/csi_csv_writer.hpp"
#include "io/constellation_csv_writer.hpp"
#include "io/psdu_writer.hpp"
#include "waveforms/wifi_nonht/packet_detector.hpp"
#include "waveforms/wifi_nonht/synchronizer.hpp"
#include "waveforms/wifi_nonht/channel_estimator.hpp"
#include "waveforms/wifi_nonht/legacy_signal_decoder.hpp"
#include "waveforms/wifi_nonht/data_symbol_extractor.hpp"
#include "waveforms/wifi_nonht/data_decoder.hpp"
#include "waveforms/wifi_nonht/beacon_parser.hpp"

#include <algorithm>
#include <array>
#include <cmath>
#include <complex>
#include <cctype>
#include <cstddef>
#include <cstdint>
#include <exception>
#include <filesystem>
#include <iomanip>
#include <iostream>
#include <limits>
#include <span>
#include <string>
#include <vector>

namespace {

struct RunningStats {
    double sum_power{0.0};
    double peak_magnitude{0.0};
    std::uint64_t sample_count{0};

    void update(
        const std::span<const std::complex<float>> samples
    ) {
        for (const auto sample : samples) {
            const double power =
                static_cast<double>(std::norm(sample));

            const double magnitude =
                std::sqrt(power);

            sum_power += power;
            peak_magnitude =
                std::max(peak_magnitude, magnitude);

            ++sample_count;
        }
    }

    [[nodiscard]]
    double mean_power() const {
        if (sample_count == 0U) {
            return 0.0;
        }

        return sum_power
            / static_cast<double>(sample_count);
    }

    [[nodiscard]]
    double rms() const {
        return std::sqrt(mean_power());
    }
};

std::string sanitize_text(
    const std::string& input
) {
    std::string output;
    output.reserve(input.size());

    constexpr char hex[] = "0123456789ABCDEF";

    for (const unsigned char value : input) {
        if (
            value >= 32U
            && value <= 126U
            && value != '\\'
        ) {
            output.push_back(
                static_cast<char>(value)
            );
            continue;
        }

        output += "\\x";
        output.push_back(hex[(value >> 4U) & 0x0FU]);
        output.push_back(hex[value & 0x0FU]);
    }

    return output;
}

void print_usage(const char* executable) {
    std::cerr
        << "Uso:\n  "
        << executable
        << " <captura_sc16.dat>"
        << " <preambulo_cf32.dat>"
        << " <lltf_frequency_cf32.dat>"
        << " <salida_csi.csv>"
        << " <salida_constelacion.csv>"
        << " <directorio_psdu>\n";
}

}  // namespace

int main(int argc, char** argv) {
    try {
        if (argc != 7) {
            print_usage(argv[0]);
            return 2;
        }

        constexpr double sample_rate_hz = 20.0e6;

        constexpr std::size_t chunk_samples =
            2'000'000U;

        constexpr std::size_t overlap_samples =
            200'000U;

        const std::filesystem::path capture_path{
            argv[1]
        };

        const std::filesystem::path reference_path{
            argv[2]
        };

        const std::filesystem::path lltf_reference_path{
            argv[3]
        };

        const std::filesystem::path output_csv_path{
            argv[4]
        };

        const std::filesystem::path constellation_csv_path{
            argv[5]
        };

        const std::filesystem::path psdu_directory_path{
            argv[6]
        };

        const auto preamble_reference =
            sensing::io::read_cf32_file(
                reference_path
            );

        const auto lltf_frequency_reference =
            sensing::io::read_cf32_file(
                lltf_reference_path
            );

        sensing::io::Sc16Reader reader(
            capture_path,
            sample_rate_hz
        );

        const auto& info = reader.info();

        std::cout
            << "========================================\n"
            << "Offline Wi-Fi Non-HT decoder - fase 4\n"
            << "========================================\n"
            << "Archivo          : "
            << capture_path << '\n'
            << "Bytes            : "
            << info.file_size_bytes << '\n'
            << "Muestras IQ      : "
            << info.complex_samples << '\n'
            << "Duracion         : "
            << std::fixed << std::setprecision(3)
            << info.duration_seconds << " s\n"
            << "Sample rate      : "
            << sample_rate_hz / 1.0e6
            << " Msps\n"
            << "Bloque           : "
            << chunk_samples << " muestras\n"
            << "Solapamiento     : "
            << overlap_samples << " muestras\n\n";

        sensing::wifi_nonht::DetectorConfig config;
        config.sample_rate_hz = sample_rate_hz;
        config.metric_threshold = 0.70F;
        config.minimum_plateau = 32U;
        config.minimum_power = 1.0e-7F;
        config.minimum_packet_spacing = 2000U;

        const sensing::wifi_nonht::PacketDetector detector{
            config
        };

        sensing::wifi_nonht::SynchronizerConfig sync_config;
        sync_config.sample_rate_hz = sample_rate_hz;
        sync_config.search_before = 256U;
        sync_config.search_after = 640U;
        sync_config.minimum_preamble_metric = 0.55F;
        sync_config.maximum_absolute_coarse_cfo_hz =
            200000.0F;

        const sensing::wifi_nonht::Synchronizer synchronizer{
            sync_config,
            preamble_reference
        };

        const sensing::wifi_nonht::ChannelEstimator
            channel_estimator{
                sample_rate_hz,
                lltf_frequency_reference
            };

        const sensing::wifi_nonht::LegacySignalDecoder
            legacy_signal_decoder;

        const sensing::wifi_nonht::DataSymbolExtractor
            data_symbol_extractor;

        sensing::wifi_nonht::DataDecoderConfig
            data_decoder_config;

        data_decoder_config.scrambler_seed = 0x5D;
        data_decoder_config.require_zero_service = true;
        data_decoder_config.require_zero_encoded_tail = true;

        const sensing::wifi_nonht::DataDecoder
            data_decoder{data_decoder_config};

        const sensing::wifi_nonht::BeaconParser
            beacon_parser;

        sensing::io::PsduWriter psdu_writer{
            psdu_directory_path
        };

        sensing::io::CsiCsvWriter csi_writer{
            output_csv_path
        };

        sensing::io::ConstellationCsvWriter
            constellation_writer{
                constellation_csv_path
            };

        std::vector<std::complex<float>> current(
            chunk_samples
        );

        std::vector<std::complex<float>> tail;
        tail.reserve(overlap_samples);

        std::vector<std::complex<float>> processing;
        processing.reserve(
            chunk_samples + overlap_samples
        );

        RunningStats stats;

        std::uint64_t global_new_samples = 0U;
        std::uint64_t total_candidates = 0U;
        std::uint64_t confirmed_packets = 0U;
        std::uint64_t valid_channel_estimates = 0U;
        std::uint64_t valid_lsig_packets = 0U;
        std::uint64_t valid_data_packets = 0U;
        std::uint64_t valid_fcs_packets = 0U;
        std::uint64_t parsed_beacons = 0U;
        std::uint64_t matching_ssid = 0U;
        std::uint64_t matching_bssid = 0U;
        std::uint64_t matching_vendor = 0U;
        std::uint64_t matching_vendor_fields = 0U;
        std::size_t chunk_index = 0U;

        std::uint64_t last_global_candidate =
            std::numeric_limits<std::uint64_t>::max();

        while (!reader.eof()) {
            const std::size_t received =
                reader.read(current);

            if (received == 0U) {
                break;
            }

            ++chunk_index;

            stats.update(
                std::span{
                    current.data(),
                    received
                }
            );

            const std::uint64_t processing_start =
                global_new_samples
                - static_cast<std::uint64_t>(
                    tail.size()
                );

            processing.clear();

            processing.insert(
                processing.end(),
                tail.begin(),
                tail.end()
            );

            processing.insert(
                processing.end(),
                current.begin(),
                current.begin()
                    + static_cast<std::ptrdiff_t>(
                        received
                    )
            );

            const auto detections =
                detector.detect(processing);

            std::size_t accepted_in_chunk = 0U;

            for (const auto& detection : detections) {
                const std::uint64_t global_offset =
                    processing_start
                    + detection.sample_offset;

                // Elimina candidatos repetidos debidos
                // al solapamiento entre bloques.
                const bool duplicated =
                    last_global_candidate
                        != std::numeric_limits<
                            std::uint64_t
                        >::max()
                    && global_offset
                        <= last_global_candidate + 512U;

                if (duplicated) {
                    continue;
                }

                last_global_candidate = global_offset;
                ++total_candidates;
                ++accepted_in_chunk;

                const auto sync =
                    synchronizer.synchronize(
                        processing,
                        detection
                    );

                if (!sync.valid) {
                    continue;
                }

                ++confirmed_packets;

                const auto channel =
                    channel_estimator.estimate(
                        processing,
                        sync
                    );

                if (!channel.valid) {
                    continue;
                }

                ++valid_channel_estimates;

                const auto lsig =
                    legacy_signal_decoder.decode(
                        processing,
                        sync,
                        channel
                    );

                if (!lsig.valid) {
                    if (confirmed_packets <= 10U) {
                        std::cout
                            << "L-SIG invalido"
                            << " | packet="
                            << confirmed_packets
                            << " | rate_field="
                            << static_cast<int>(
                                lsig.rate_field
                            )
                            << " | rate_valid="
                            << lsig.rate_valid
                            << " | rate_mbps="
                            << lsig.data_rate_mbps
                            << " | length="
                            << lsig.length_bytes
                            << " | length_valid="
                            << lsig.length_valid
                            << " | parity="
                            << lsig.parity_valid
                            << " | tail="
                            << lsig.tail_valid
                            << " | bits=";

                        for (const auto bit :
                             lsig.decoded_bits) {
                            std::cout
                                << static_cast<int>(bit);
                        }

                        std::cout << '\n';
                    }

                    continue;
                }

                ++valid_lsig_packets;

                const auto data_symbols =
                    data_symbol_extractor.extract(
                        processing,
                        sync,
                        channel,
                        lsig
                    );

                if (!data_symbols.valid) {
                    continue;
                }

                constellation_writer.write(
                    valid_lsig_packets,
                    data_symbols
                );

                const auto data =
                    data_decoder.decode(
                        data_symbols,
                        lsig
                    );

                if (!data.valid) {
                    if (valid_lsig_packets <= 10U) {
                        std::cout
                            << "DATA invalido"
                            << " | packet="
                            << valid_lsig_packets
                            << " | service="
                            << data.service_valid
                            << " | encoded_tail="
                            << data.encoded_tail_valid
                            << " | psdu_bytes="
                            << data.psdu_bytes.size()
                            << " | expected="
                            << lsig.length_bytes
                            << '\n';
                    }

                    continue;
                }

                ++valid_data_packets;

                const auto beacon =
                    beacon_parser.parse(
                        data.psdu_bytes
                    );

                if (beacon.fcs_valid) {
                    ++valid_fcs_packets;
                }

                if (beacon.valid) {
                    ++parsed_beacons;
                }

                if (beacon.ssid == "USRP_CHANNEL11") {
                    ++matching_ssid;
                }

                if (
                    beacon.bssid_string
                    == "02:11:22:33:44:55"
                ) {
                    ++matching_bssid;
                }

                if (beacon.has_vendor_magic) {
                    ++matching_vendor;
                }

                if (
                    beacon.vendor_valid
                    && beacon.vendor_oui
                        == std::array<std::uint8_t,3>{
                            0x02U,
                            0x11U,
                            0x22U
                        }
                    && beacon.vendor_type == 1U
                    && beacon.vendor_version == 1U
                    && beacon.transmitter_id.value_or(0U)
                        == 1U
                    && beacon.experiment_id.value_or(0U)
                        == 1U
                ) {
                    ++matching_vendor_fields;
                }

                /*
                 * En este banco de pruebas guardamos todas
                 * las PSDU DATA válidas, aunque fallen FCS,
                 * para poder inspeccionarlas.
                 */
                psdu_writer.write(
                    valid_data_packets,
                    data.psdu_bytes
                );

                std::cout
                    << "BEACON"
                    << " | data_packet="
                    << valid_data_packets
                    << " | fcs="
                    << beacon.fcs_valid
                    << " | valid="
                    << beacon.valid
                    << " | seq="
                    << beacon.sequence_number
                    << " | ssid="
                    << sanitize_text(beacon.ssid)
                    << " | bssid="
                    << beacon.bssid_string
                    << " | vendor="
                    << beacon.vendor_valid
                    << " | vendor_type="
                    << static_cast<unsigned int>(
                        beacon.vendor_type
                    )
                    << " | vendor_version="
                    << static_cast<unsigned int>(
                        beacon.vendor_version
                    )
                    << " | tx_id="
                    << beacon.transmitter_id.value_or(0U)
                    << " | experiment_id="
                    << beacon.experiment_id.value_or(0U)
                    << " | packet_counter="
                    << beacon.packet_counter.value_or(0U)
                    << '\n';

                const std::uint64_t global_packet_start =
                    processing_start + sync.packet_start;

                csi_writer.write(
                    valid_channel_estimates,
                    global_packet_start,
                    sample_rate_hz,
                    detection,
                    sync,
                    channel
                );

                const double time_seconds =
                    static_cast<double>(
                        global_offset
                    ) / sample_rate_hz;

                std::cout
                    << "Paquete confirmado "
                    << std::setw(5)
                    << total_candidates
                    << " | sample="
                    << global_packet_start
                    << " | t="
                    << std::fixed
                    << std::setprecision(6)
                    << time_seconds
                    << " s"
                    << " | STF="
                    << std::setprecision(3)
                    << detection.metric
                    << " | PREAMBLE="
                    << sync.preamble_metric
                    << " | RATE="
                    << lsig.data_rate_mbps
                    << " Mbps"
                    << " | LENGTH="
                    << lsig.length_bytes
                    << " B"
                    << " | NSYM="
                    << lsig.number_of_data_symbols
                    << " | SNR="
                    << std::fixed
                    << std::setprecision(2)
                    << channel.snr_db
                    << " dB"
                    << " | power="
                    << std::scientific
                    << detection.power
                    << " | CFO="
                    << std::fixed
                    << std::setprecision(1)
                    << detection.coarse_cfo_hz
                    << " Hz"
                    << " | fine_CFO="
                    << channel.fine_cfo_hz
                    << " Hz\n";
            }

            global_new_samples += received;

            const std::size_t keep =
                std::min(
                    overlap_samples,
                    processing.size()
                );

            tail.assign(
                processing.end()
                    - static_cast<std::ptrdiff_t>(
                        keep
                    ),
                processing.end()
            );

            const double progress =
                100.0
                * static_cast<double>(
                    global_new_samples
                )
                / static_cast<double>(
                    info.complex_samples
                );

            std::cout
                << "Bloque "
                << std::setw(3)
                << chunk_index
                << " | progreso="
                << std::fixed
                << std::setprecision(2)
                << progress
                << " %"
                << " | candidatos bloque="
                << accepted_in_chunk
                << " | total="
                << total_candidates
                << '\n';
        }

        std::cout
            << "\n========================================\n"
            << "RESULTADO FASE 4A/4B\n"
            << "========================================\n"
            << "Muestras procesadas : "
            << stats.sample_count << '\n'
            << "RMS                 : "
            << std::scientific
            << stats.rms() << '\n'
            << "Pico absoluto       : "
            << stats.peak_magnitude << '\n'
            << "Candidatos L-STF    : "
            << std::fixed
            << total_candidates << '\n'
            << "Paquetes confirmados: "
            << confirmed_packets << '\n'
            << "CSI validas          : "
            << valid_channel_estimates << '\n'
            << "L-SIG validos        : "
            << valid_lsig_packets << '\n'
            << "DATA validos         : "
            << valid_data_packets << '\n'
            << "FCS validos          : "
            << valid_fcs_packets << '\n'
            << "Beacons parseados    : "
            << parsed_beacons << '\n'
            << "SSID coincidente     : "
            << matching_ssid << '\n'
            << "BSSID coincidente    : "
            << matching_bssid << '\n'
            << "Vendor ALBSENS       : "
            << matching_vendor << '\n'
            << "Vendor campos validos: "
            << matching_vendor_fields << '\n';

        return 0;

    } catch (const std::exception& error) {
        std::cerr
            << "ERROR: "
            << error.what()
            << '\n';

        return 1;
    }
}
