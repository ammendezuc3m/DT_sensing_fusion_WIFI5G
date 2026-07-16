#include "common/iq_block.hpp"
#include "io/feature_jsonl_writer.hpp"
#include "io/sc16_reader.hpp"
#include "pipeline/waveform_factory.hpp"
#include "pipeline/waveform_processor.hpp"

#include <nlohmann/json.hpp>

#include <algorithm>
#include <complex>
#include <cstddef>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <memory>
#include <span>
#include <stdexcept>
#include <string>
#include <vector>

namespace {

nlohmann::json load_json(
    const std::filesystem::path& path
) {
    std::ifstream input(path);

    if (!input) {
        throw std::runtime_error(
            "No se pudo abrir la configuración: "
            + path.string()
        );
    }

    nlohmann::json config;
    input >> config;

    return config;
}

void print_usage(const char* executable) {
    std::cerr
        << "Uso:\n  "
        << executable
        << " --config <pipeline.json>\n";
}

std::filesystem::path resolve_output_path(
    const nlohmann::json& output_config
) {
    const std::filesystem::path directory =
        output_config.value(
            "directory",
            std::string{"results/runtime"}
        );

    const std::string filename =
        output_config.value(
            "features_filename",
            std::string{
                "feature_frames.jsonl"
            }
        );

    return directory / filename;
}

}  // namespace

int main(int argc, char** argv) {
    try {
        if (
            argc != 3
            || std::string{argv[1]} != "--config"
        ) {
            print_usage(argv[0]);
            return 1;
        }

        const std::filesystem::path config_path{
            argv[2]
        };

        const auto root =
            load_json(config_path);

        const auto& pipeline_config =
            root.at("pipeline");

        const auto& input_config =
            root.at("input");

        const auto& output_config =
            root.at("output");

        const auto& waveform_config =
            root.at("waveform_config");

        const std::string mode =
            pipeline_config.value(
                "mode",
                std::string{}
            );

        if (mode != "offline") {
            throw std::invalid_argument(
                "Esta aplicación requiere "
                "pipeline.mode=offline"
            );
        }

        const std::string waveform_name =
            pipeline_config.at("waveform")
                .get<std::string>();

        const std::size_t block_samples =
            pipeline_config.value(
                "block_samples",
                std::size_t{2'000'000U}
            );

        const std::size_t overlap_samples =
            pipeline_config.value(
                "overlap_samples",
                std::size_t{200'000U}
            );

        if (block_samples == 0U) {
            throw std::invalid_argument(
                "block_samples debe ser mayor que cero"
            );
        }

        if (overlap_samples >= block_samples) {
            throw std::invalid_argument(
                "overlap_samples debe ser menor "
                "que block_samples"
            );
        }

        const std::string input_format =
            input_config.value(
                "format",
                std::string{}
            );

        if (input_format != "sc16_le") {
            throw std::invalid_argument(
                "Formato offline no soportado: "
                + input_format
                + ". Actualmente se admite sc16_le"
            );
        }

        const std::filesystem::path input_path =
            input_config.at("path")
                .get<std::string>();

        const double sample_rate_hz =
            input_config.at("sample_rate_hz")
                .get<double>();

        const double center_frequency_hz =
            input_config.value(
                "center_frequency_hz",
                0.0
            );

        if (sample_rate_hz <= 0.0) {
            throw std::invalid_argument(
                "sample_rate_hz debe ser positivo"
            );
        }

        sensing::io::Sc16Reader reader{
            input_path,
            sample_rate_hz
        };

        const auto& file_info =
            reader.info();

        auto processor =
            sensing::create_waveform_processor(
                waveform_name,
                waveform_config,
                sample_rate_hz
            );

        const auto output_path =
            resolve_output_path(
                output_config
            );

        sensing::io::FeatureJsonlWriter writer{
            output_path
        };

        sensing::ProcessingStats stats;

        std::vector<std::complex<float>> current(
            block_samples
        );

        std::vector<std::complex<float>> tail;
        tail.reserve(overlap_samples);

        sensing::IqBlock block;
        block.samples.reserve(
            block_samples + overlap_samples
        );

        std::uint64_t new_samples_processed = 0U;
        std::uint64_t frames_written = 0U;
        std::size_t block_index = 0U;

        std::cout
            << "========================================\n"
            << "Offline waveform pipeline\n"
            << "========================================\n"
            << "Configuracion     : "
            << config_path << '\n'
            << "Procesador        : "
            << processor->name() << '\n'
            << "Entrada           : "
            << input_path << '\n'
            << "Formato           : "
            << input_format << '\n'
            << "Muestras          : "
            << file_info.complex_samples << '\n'
            << "Duracion          : "
            << std::fixed
            << std::setprecision(3)
            << file_info.duration_seconds
            << " s\n"
            << "Sample rate       : "
            << sample_rate_hz / 1.0e6
            << " Msps\n"
            << "Frecuencia central: "
            << center_frequency_hz / 1.0e6
            << " MHz\n"
            << "Bloque nuevo      : "
            << block_samples
            << " muestras\n"
            << "Solapamiento      : "
            << overlap_samples
            << " muestras\n"
            << "Salida            : "
            << output_path
            << "\n\n";

        processor->reset();

        while (!reader.eof()) {
            const std::size_t received =
                reader.read(
                    std::span{
                        current.data(),
                        current.size()
                    }
                );

            if (received == 0U) {
                break;
            }

            ++block_index;

            /*
             * first_sample_index es el índice global de
             * la primera muestra incluida en block.samples.
             *
             * Al incorporar el tail anterior, el bloque
             * comienza antes que las muestras nuevas.
             */
            block.first_sample_index =
                new_samples_processed
                - static_cast<std::uint64_t>(
                    tail.size()
                );

            block.timestamp_ns = 0U;
            block.sample_rate_hz =
                sample_rate_hz;

            block.center_frequency_hz =
                center_frequency_hz;

            block.samples.clear();

            block.samples.insert(
                block.samples.end(),
                tail.begin(),
                tail.end()
            );

            block.samples.insert(
                block.samples.end(),
                current.begin(),
                current.begin()
                    + static_cast<std::ptrdiff_t>(
                        received
                    )
            );

            const auto frames =
                processor->process(
                    block,
                    stats
                );

            for (const auto& frame : frames) {
                writer.write(frame);
                ++frames_written;
            }

            new_samples_processed +=
                static_cast<std::uint64_t>(
                    received
                );

            const std::size_t keep =
                std::min(
                    overlap_samples,
                    block.samples.size()
                );

            tail.assign(
                block.samples.end()
                    - static_cast<std::ptrdiff_t>(
                        keep
                    ),
                block.samples.end()
            );

            const double progress =
                file_info.complex_samples > 0U
                ? (
                    100.0
                    * static_cast<double>(
                        new_samples_processed
                    )
                    / static_cast<double>(
                        file_info.complex_samples
                    )
                )
                : 100.0;

            std::cout
                << "Bloque "
                << std::setw(3)
                << block_index
                << " | progreso="
                << std::fixed
                << std::setprecision(2)
                << progress
                << " %"
                << " | frames bloque="
                << frames.size()
                << " | frames total="
                << frames_written
                << '\n';
        }

        std::cout
            << "\n========================================\n"
            << "RESULTADO PIPELINE GENERICO\n"
            << "========================================\n"
            << "Muestras procesadas : "
            << new_samples_processed << '\n'
            << "Candidatos          : "
            << stats.candidates << '\n'
            << "Sincronizados       : "
            << stats.synchronized << '\n'
            << "Decodificados       : "
            << stats.decoded << '\n'
            << "Rechazados          : "
            << stats.rejected << '\n'
            << "Publicados          : "
            << stats.published << '\n'
            << "Frames escritos     : "
            << frames_written << '\n'
            << "Salida              : "
            << output_path << '\n';

        return 0;

    } catch (const std::exception& error) {
        std::cerr
            << "ERROR: "
            << error.what()
            << '\n';

        return 1;
    }
}
