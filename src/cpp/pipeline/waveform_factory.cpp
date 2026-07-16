#include "pipeline/waveform_factory.hpp"

#include "io/cf32_reader.hpp"
#include "waveforms/wifi_nonht/wifi_nonht_processor.hpp"

#include <array>
#include <cstdint>
#include <cstdio>
#include <memory>
#include <stdexcept>
#include <string>
#include <utility>

namespace sensing {

namespace {

std::array<std::uint8_t,3> parse_oui(
    const std::string& text
) {
    std::array<std::uint8_t,3> oui{};

    unsigned int first = 0U;
    unsigned int second = 0U;
    unsigned int third = 0U;

    const int matched = std::sscanf(
        text.c_str(),
        "%x:%x:%x",
        &first,
        &second,
        &third
    );

    if (
        matched != 3
        || first > 0xFFU
        || second > 0xFFU
        || third > 0xFFU
    ) {
        throw std::invalid_argument(
            "OUI inválido: " + text
            + ". Formato esperado: 02:11:22"
        );
    }

    oui[0] = static_cast<std::uint8_t>(first);
    oui[1] = static_cast<std::uint8_t>(second);
    oui[2] = static_cast<std::uint8_t>(third);

    return oui;
}

}  // namespace

std::unique_ptr<IWaveformProcessor>
create_waveform_processor(
    const std::string& waveform_name,
    const nlohmann::json& config,
    const double sample_rate_hz
) {
    if (waveform_name == "wifi_nonht") {
        wifi_nonht::WifiNonHtProcessorConfig
            wifi_config;

        wifi_config.profile_id =
            config.value(
                "profile_id",
                static_cast<std::uint16_t>(1U)
            );

        /*
         * Detector.
         */
        wifi_config.detector.sample_rate_hz =
            sample_rate_hz;

        if (config.contains("detector")) {
            const auto& detector =
                config.at("detector");

            wifi_config.detector.lag_samples =
                detector.value(
                    "lag_samples",
                    std::size_t{16U}
                );

            wifi_config.detector.correlation_window =
                detector.value(
                    "correlation_window",
                    std::size_t{64U}
                );

            wifi_config.detector.metric_threshold =
                detector.value(
                    "metric_threshold",
                    0.70F
                );

            wifi_config.detector.minimum_plateau =
                detector.value(
                    "minimum_plateau",
                    std::size_t{32U}
                );

            wifi_config.detector.minimum_power =
                detector.value(
                    "minimum_power",
                    1.0e-7F
                );

            wifi_config.detector.minimum_packet_spacing =
                detector.value(
                    "minimum_packet_spacing",
                    std::size_t{2000U}
                );
        }

        /*
         * Sincronizador.
         */
        wifi_config.synchronizer.sample_rate_hz =
            sample_rate_hz;

        if (config.contains("synchronizer")) {
            const auto& synchronizer =
                config.at("synchronizer");

            wifi_config.synchronizer.search_before =
                synchronizer.value(
                    "search_before",
                    std::size_t{256U}
                );

            wifi_config.synchronizer.search_after =
                synchronizer.value(
                    "search_after",
                    std::size_t{640U}
                );

            wifi_config.synchronizer
                .minimum_preamble_metric =
                synchronizer.value(
                    "minimum_preamble_metric",
                    0.55F
                );

            wifi_config.synchronizer
                .maximum_absolute_coarse_cfo_hz =
                synchronizer.value(
                    "maximum_absolute_cfo_hz",
                    200000.0F
                );
        }

        /*
         * Referencias PHY.
         */
        wifi_config.preamble_reference =
            io::read_cf32_file(
                config.at("preamble_reference")
                    .get<std::string>()
            );

        wifi_config.lltf_frequency_reference =
            io::read_cf32_file(
                config.at("lltf_frequency_reference")
                    .get<std::string>()
            );

        /*
         * Decoder DATA.
         */
        if (config.contains("data_decoder")) {
            const auto& decoder =
                config.at("data_decoder");

            const unsigned int seed =
                decoder.value(
                    "scrambler_seed",
                    0x5DU
                );

            if (seed == 0U || seed > 0x7FU) {
                throw std::invalid_argument(
                    "scrambler_seed debe estar "
                    "entre 1 y 127"
                );
            }

            wifi_config.data_decoder.scrambler_seed =
                static_cast<std::uint8_t>(seed);

            wifi_config.data_decoder
                .require_zero_service =
                decoder.value(
                    "require_zero_service",
                    true
                );

            wifi_config.data_decoder
                .require_zero_encoded_tail =
                decoder.value(
                    "require_zero_encoded_tail",
                    true
                );
        }

        wifi_config.duplicate_tolerance_samples =
            config.value(
                "duplicate_tolerance_samples",
                std::uint64_t{512U}
            );

        wifi_config.include_psdu =
            config.value(
                "include_psdu",
                false
            );

        /*
         * Filtros WiFi. Todos son configurables.
         * Un campo vacío o ausente desactiva ese filtro.
         */
        if (config.contains("filters")) {
            const auto& filters =
                config.at("filters");

            wifi_config.filters.require_valid_fcs =
                filters.value(
                    "require_valid_fcs",
                    true
                );

            wifi_config.filters.require_beacon =
                filters.value(
                    "require_beacon",
                    true
                );

            wifi_config.filters.ssid =
                filters.value(
                    "ssid",
                    std::string{}
                );

            wifi_config.filters.bssid =
                filters.value(
                    "bssid",
                    std::string{}
                );

            const std::string oui_text =
                filters.value(
                    "vendor_oui",
                    std::string{}
                );

            if (!oui_text.empty()) {
                wifi_config.filters
                    .require_vendor_oui = true;

                wifi_config.filters.vendor_oui =
                    parse_oui(oui_text);
            }

            if (filters.contains("vendor_type")) {
                const unsigned int value =
                    filters.at("vendor_type")
                        .get<unsigned int>();

                if (value > 0xFFU) {
                    throw std::invalid_argument(
                        "vendor_type fuera de rango"
                    );
                }

                wifi_config.filters
                    .require_vendor_type = true;

                wifi_config.filters.vendor_type =
                    static_cast<std::uint8_t>(
                        value
                    );
            }

            wifi_config.filters.vendor_magic =
                filters.value(
                    "vendor_magic",
                    std::string{}
                );

            if (filters.contains("vendor_version")) {
                const unsigned int value =
                    filters.at("vendor_version")
                        .get<unsigned int>();

                if (value > 0xFFU) {
                    throw std::invalid_argument(
                        "vendor_version fuera de rango"
                    );
                }

                wifi_config.filters
                    .require_vendor_version = true;

                wifi_config.filters.vendor_version =
                    static_cast<std::uint8_t>(
                        value
                    );
            }

            if (
                filters.contains("transmitter_id")
            ) {
                const unsigned int value =
                    filters.at("transmitter_id")
                        .get<unsigned int>();

                if (value > 0xFFFFU) {
                    throw std::invalid_argument(
                        "transmitter_id fuera de rango"
                    );
                }

                wifi_config.filters
                    .require_transmitter_id = true;

                wifi_config.filters.transmitter_id =
                    static_cast<std::uint16_t>(
                        value
                    );
            }

            if (
                filters.contains("experiment_id")
            ) {
                const unsigned int value =
                    filters.at("experiment_id")
                        .get<unsigned int>();

                if (value > 0xFFFFU) {
                    throw std::invalid_argument(
                        "experiment_id fuera de rango"
                    );
                }

                wifi_config.filters
                    .require_experiment_id = true;

                wifi_config.filters.experiment_id =
                    static_cast<std::uint16_t>(
                        value
                    );
            }
        }

        return std::make_unique<
            wifi_nonht::WifiNonHtProcessor
        >(
            std::move(wifi_config),
            sample_rate_hz
        );
    }

    if (
        waveform_name == "beamforming"
        || waveform_name
            == "beamforming_training"
    ) {
        throw std::runtime_error(
            "El procesador beamforming todavía "
            "no está implementado"
        );
    }

    if (waveform_name == "wifi_ndp") {
        throw std::runtime_error(
            "El procesador WiFi NDP todavía "
            "no está implementado"
        );
    }

    if (waveform_name == "custom_ofdm") {
        throw std::runtime_error(
            "El procesador Custom OFDM todavía "
            "no está implementado"
        );
    }

    if (waveform_name == "nr_ssb") {
        throw std::runtime_error(
            "El procesador NR SSB todavía "
            "no está implementado"
        );
    }

    if (waveform_name == "raw_reference") {
        throw std::runtime_error(
            "El procesador Raw Reference todavía "
            "no está implementado"
        );
    }

    throw std::invalid_argument(
        "Waveform desconocida: "
        + waveform_name
    );
}

}  // namespace sensing
