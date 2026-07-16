#include "common/bounded_queue.hpp"
#include "common/iq_block.hpp"
#include "io/feature_jsonl_writer.hpp"
#include "io/csi_raw_writer.hpp"
#include "pipeline/waveform_factory.hpp"
#include "pipeline/waveform_processor.hpp"
#include "sources/uhd_iq_source.hpp"
#include "publishers/feature_publisher.hpp"
#include "publishers/zeromq_feature_publisher.hpp"

#include <nlohmann/json.hpp>

#include <algorithm>
#include <atomic>
#include <chrono>
#include <cmath>
#include <complex>
#include <csignal>
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
#include <thread>
#include <utility>
#include <vector>

namespace {

std::atomic<bool> running{true};

void handle_signal(int) {
    running.store(false);
}

nlohmann::json load_json(
    const std::filesystem::path& path
) {
    std::ifstream input{path};

    if (!input) {
        throw std::runtime_error(
            "No se pudo abrir: " + path.string()
        );
    }

    nlohmann::json config;
    input >> config;
    return config;
}

std::uint64_t steady_now_ns() {
    return static_cast<std::uint64_t>(
        std::chrono::duration_cast<
            std::chrono::nanoseconds
        >(
            std::chrono::steady_clock::now()
                .time_since_epoch()
        ).count()
    );
}

struct OnlineIqBlock {
    sensing::IqBlock block;

    bool overflow{false};
    bool discontinuity{false};

    /*
     * Tiempo del ordenador inmediatamente después
     * de recibir el bloque desde UHD.
     */
    std::uint64_t host_received_ns{0};
};

struct OnlineStats {
    std::atomic<std::uint64_t> rx_blocks{0};
    std::atomic<std::uint64_t> rx_samples{0};
    std::atomic<std::uint64_t> overflows{0};
    std::atomic<std::uint64_t> timeouts{0};
    std::atomic<std::uint64_t> discontinuities{0};
    std::atomic<std::uint64_t> frames{0};
    std::atomic<std::uint64_t> local_written{0};
    std::atomic<std::uint64_t> transport_published{0};
    std::atomic<std::uint64_t> transport_dropped{0};

    std::atomic<std::uint64_t> processed_blocks{0};

    /*
     * Tiempos acumulados en microsegundos para evitar
     * atomic<double>.
     */
    std::atomic<std::uint64_t>
        total_processing_time_us{0};

    std::atomic<std::uint64_t>
        maximum_processing_time_us{0};

    std::atomic<std::uint64_t>
        slow_blocks_over_10ms{0};

    std::atomic<std::uint64_t>
        slow_blocks_over_20ms{0};
};

void print_usage(const char* executable) {
    std::cerr
        << "Uso:\n  "
        << executable
        << " --config <pipeline.json>\n";
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

        std::signal(SIGINT, handle_signal);
        std::signal(SIGTERM, handle_signal);

        const std::filesystem::path config_path{
            argv[2]
        };

        const auto root =
            load_json(config_path);

        const auto& pipeline_config =
            root.at("pipeline");

        const auto& input_config =
            root.at("input");

        const auto& waveform_config =
            root.at("waveform_config");

        const auto& output_config =
            root.at("output");

        if (
            pipeline_config.at("mode")
                .get<std::string>()
            != "online"
        ) {
            throw std::invalid_argument(
                "pipeline.mode debe ser online"
            );
        }

        if (
            input_config.at("driver")
                .get<std::string>()
            != "uhd"
        ) {
            throw std::invalid_argument(
                "Esta versión requiere input.driver=uhd"
            );
        }

        const std::string waveform_name =
            pipeline_config.at("waveform")
                .get<std::string>();

        const std::size_t block_samples =
            pipeline_config.value(
                "block_samples",
                std::size_t{400000U}
            );

        const std::size_t overlap_samples =
            pipeline_config.value(
                "overlap_samples",
                std::size_t{20000U}
            );

        const std::size_t queue_capacity =
            pipeline_config.value(
                "iq_queue_capacity",
                std::size_t{16U}
            );

        if (block_samples == 0U) {
            throw std::invalid_argument(
                "block_samples debe ser > 0"
            );
        }

        if (overlap_samples >= block_samples) {
            throw std::invalid_argument(
                "overlap_samples debe ser menor "
                "que block_samples"
            );
        }

        sensing::UhdIqSourceConfig source_config;

        source_config.device_args =
            input_config.value(
                "device_args",
                std::string{"type=b200"}
            );

        source_config.channel =
            input_config.value(
                "channel",
                std::size_t{0U}
            );

        source_config.sample_rate_hz =
            input_config.at("sample_rate_hz")
                .get<double>();

        source_config.center_frequency_hz =
            input_config.at(
                "center_frequency_hz"
            ).get<double>();

        source_config.gain_db =
            input_config.value(
                "gain_db",
                35.0
            );

        source_config.bandwidth_hz =
            input_config.value(
                "bandwidth_hz",
                source_config.sample_rate_hz
            );

        source_config.antenna =
            input_config.value(
                "antenna",
                std::string{"RX2"}
            );

        source_config.clock_source =
            input_config.value(
                "clock_source",
                std::string{"internal"}
            );

        source_config.time_source =
            input_config.value(
                "time_source",
                std::string{"internal"}
            );

        source_config.cpu_format =
            input_config.value(
                "cpu_format",
                std::string{"fc32"}
            );

        source_config.wire_format =
            input_config.value(
                "wire_format",
                std::string{"sc16"}
            );

        source_config.receive_timeout_seconds =
            input_config.value(
                "receive_timeout_seconds",
                1.0
            );

        sensing::UhdIqSource source{
            source_config
        };

        auto processor =
            sensing::create_waveform_processor(
                waveform_name,
                waveform_config,
                source.sample_rate_hz()
            );

        std::unique_ptr<sensing::io::FeatureJsonlWriter>
            feature_writer;

        std::filesystem::path feature_path;

        const bool write_features =
            output_config.value(
                "write_features",
                false
            );

        if (write_features) {
            feature_path =
                output_config.value(
                    "feature_path",
                    std::string{
                        "results/csi/live/latest.jsonl"
                    }
                );

            feature_writer =
                std::make_unique<
                    sensing::io::FeatureJsonlWriter
                >(
                    feature_path
                );
        }

        std::unique_ptr<sensing::io::CsiRawWriter>
            csi_raw_writer;

        std::filesystem::path csi_raw_path;

        const bool write_csi_raw =
            output_config.value(
                "write_csi_raw",
                false
            );

        if (write_csi_raw) {
            csi_raw_path =
                output_config.value(
                    "csi_raw_path",
                    std::string{
                        "results/csi/live/latest_csi.cf32"
                    }
                );

            csi_raw_writer =
                std::make_unique<
                    sensing::io::CsiRawWriter
                >(
                    csi_raw_path
                );
        }

        std::unique_ptr<sensing::IFeaturePublisher>
            publisher;

        const std::string publisher_type =
            output_config.value(
                "publisher",
                std::string{"none"}
            );

        if (publisher_type == "zeromq_push") {
            sensing::ZeroMqPublisherConfig
                publisher_config;

            publisher_config.endpoint =
                output_config.value(
                    "endpoint",
                    std::string{
                        "tcp://127.0.0.1:5555"
                    }
                );

            publisher_config.send_high_water_mark =
                output_config.value(
                    "send_high_water_mark",
                    1000
                );

            publisher_config.linger_ms =
                output_config.value(
                    "linger_ms",
                    0
                );

            publisher_config.non_blocking =
                output_config.value(
                    "non_blocking",
                    true
                );

            publisher =
                std::make_unique<
                    sensing::ZeroMqFeaturePublisher
                >(
                    std::move(publisher_config)
                );

        } else if (publisher_type != "none") {
            throw std::invalid_argument(
                "Publicador no soportado: "
                + publisher_type
            );
        }

        sensing::BoundedQueue<OnlineIqBlock>
            iq_queue{queue_capacity};

        OnlineStats online_stats;
        sensing::ProcessingStats processing_stats;

        std::cout
            << "========================================\n"
            << "Online waveform pipeline\n"
            << "========================================\n"
            << "Fuente            : "
            << source.name() << '\n'
            << "Procesador        : "
            << processor->name() << '\n'
            << "Publicador        : "
            << (
                publisher
                ? publisher->name()
                : std::string{"none"}
            )
            << '\n'
            << "Salida JSONL      : "
            << (
                feature_writer
                ? feature_path.string()
                : std::string{"disabled"}
            )
            << '\n'
            << "Salida CSI raw    : "
            << (
                csi_raw_writer
                ? csi_raw_path.string()
                : std::string{"disabled"}
            )
            << '\n'
            << "Sample rate       : "
            << source.sample_rate_hz() / 1.0e6
            << " Msps\n"
            << "Frecuencia        : "
            << source.center_frequency_hz() / 1.0e6
            << " MHz\n"
            << "Ganancia          : "
            << source_config.gain_db << " dB\n"
            << "Bloque            : "
            << block_samples << " muestras\n"
            << "Solapamiento      : "
            << overlap_samples << " muestras\n"
            << "Cola IQ           : "
            << queue_capacity << " bloques\n"
            << "Ctrl+C para terminar\n\n";

        const auto application_start =
            std::chrono::steady_clock::now();

        source.start();
        processor->reset();

        std::jthread rx_thread{
            [&] {
                try {
                    std::vector<std::complex<float>>
                        receive_buffer(block_samples);

                    std::vector<std::complex<float>> tail;
                    tail.reserve(overlap_samples);

                    std::uint64_t global_new_samples = 0U;

                    while (running.load()) {
                        const auto read_result =
                            source.read(receive_buffer);

                        if (read_result.timeout) {
                            ++online_stats.timeouts;
                        }

                        if (read_result.overflow) {
                            ++online_stats.overflows;
                        }

                        if (read_result.discontinuity) {
                            ++online_stats.discontinuities;

                            /*
                             * No conservar muestras anteriores
                             * tras una discontinuidad.
                             */
                            tail.clear();
                        }

                        if (
                            read_result.samples_received
                            == 0U
                        ) {
                            continue;
                        }

                        OnlineIqBlock online_block;

                        online_block.overflow =
                            read_result.overflow;

                        online_block.discontinuity =
                            read_result.discontinuity;

                        online_block.host_received_ns =
                            steady_now_ns();

                        online_block.block.first_sample_index =
                            global_new_samples
                            - static_cast<std::uint64_t>(
                                tail.size()
                            );

                        online_block.block.timestamp_ns =
                            read_result.timestamp_ns;

                        online_block.block.sample_rate_hz =
                            source.sample_rate_hz();

                        online_block.block
                            .center_frequency_hz =
                            source.center_frequency_hz();

                        online_block.block.samples.reserve(
                            tail.size()
                            + read_result.samples_received
                        );

                        online_block.block.samples.insert(
                            online_block.block.samples.end(),
                            tail.begin(),
                            tail.end()
                        );

                        online_block.block.samples.insert(
                            online_block.block.samples.end(),
                            receive_buffer.begin(),
                            receive_buffer.begin()
                                + static_cast<
                                    std::ptrdiff_t
                                >(
                                    read_result
                                        .samples_received
                                )
                        );

                        global_new_samples +=
                            static_cast<std::uint64_t>(
                                read_result.samples_received
                            );

                        const std::size_t keep =
                            std::min(
                                overlap_samples,
                                online_block
                                    .block.samples.size()
                            );

                        tail.assign(
                            online_block.block.samples.end()
                                - static_cast<
                                    std::ptrdiff_t
                                >(keep),
                            online_block.block.samples.end()
                        );

                        ++online_stats.rx_blocks;

                        online_stats.rx_samples +=
                            read_result.samples_received;

                        if (
                            !iq_queue.push(
                                std::move(online_block)
                            )
                        ) {
                            break;
                        }
                    }
                } catch (const std::exception& error) {
                    std::cerr
                        << "ERROR hilo UHD: "
                        << error.what()
                        << '\n';

                    running.store(false);
                }

                iq_queue.close();
            }
        };

        std::jthread processing_thread{
            [&] {
                try {
                    while (running.load()) {
                        auto item = iq_queue.pop();

                        if (!item.has_value()) {
                            break;
                        }

                        /*
                         * Un bloque con discontinuidad puede
                         * contener datos válidos posteriores al
                         * overflow, pero nunca debe combinarse
                         * con el tail anterior.
                         */
                        const std::uint64_t
                            candidates_before =
                                processing_stats.candidates;

                        const std::uint64_t
                            synchronized_before =
                                processing_stats.synchronized;

                        const std::uint64_t
                            decoded_before =
                                processing_stats.decoded;

                        const std::uint64_t
                            processing_started_ns =
                                steady_now_ns();

                        const auto frames =
                            processor->process(
                                item->block,
                                processing_stats
                            );

                        const std::uint64_t processed_ns =
                            steady_now_ns();

                        const double queue_wait_ms =
                            static_cast<double>(
                                processing_started_ns
                                - item->host_received_ns
                            )
                            / 1.0e6;

                        const double processing_time_ms =
                            static_cast<double>(
                                processed_ns
                                - processing_started_ns
                            )
                            / 1.0e6;

                        const std::uint64_t processing_time_us =
                            (
                                processed_ns
                                - processing_started_ns
                            )
                            / 1000U;

                        const std::uint64_t
                            block_candidates =
                                processing_stats.candidates
                                - candidates_before;

                        const std::uint64_t
                            block_synchronized =
                                processing_stats.synchronized
                                - synchronized_before;

                        const std::uint64_t
                            block_decoded =
                                processing_stats.decoded
                                - decoded_before;

                        if (processing_time_us >= 10000U) {
                            std::cerr
                                << "SLOW_BLOCK"
                                << " | first_sample="
                                << item->block.first_sample_index
                                << " | samples="
                                << item->block.samples.size()
                                << " | candidates="
                                << block_candidates
                                << " | synchronized="
                                << block_synchronized
                                << " | decoded="
                                << block_decoded
                                << " | frames="
                                << frames.size()
                                << " | processing="
                                << processing_time_ms
                                << " ms"
                                << " | queue_wait="
                                << queue_wait_ms
                                << " ms"
                                << " | queue="
                                << iq_queue.size()
                                << '\n';
                        }

                        ++online_stats.processed_blocks;

                        online_stats
                            .total_processing_time_us
                            .fetch_add(
                                processing_time_us
                            );

                        std::uint64_t previous_max =
                            online_stats
                                .maximum_processing_time_us
                                .load();

                        while (
                            processing_time_us > previous_max
                            && !online_stats
                                .maximum_processing_time_us
                                .compare_exchange_weak(
                                    previous_max,
                                    processing_time_us
                                )
                        ) {
                        }

                        if (processing_time_us >= 10000U) {
                            ++online_stats
                                .slow_blocks_over_10ms;
                        }

                        if (processing_time_us >= 20000U) {
                            ++online_stats
                                .slow_blocks_over_20ms;
                        }

                        for (const auto& frame : frames) {
                            ++online_stats.frames;

                            if (feature_writer) {
                                feature_writer->write(frame);

                                ++online_stats.local_written;
                            }

                            if (csi_raw_writer) {
                                if (
                                    frame.complex_features.size()
                                    != 52U
                                ) {
                                    throw std::runtime_error(
                                        "FeatureFrame WiFi con CSI "
                                        "distinto de 52"
                                    );
                                }

                                csi_raw_writer->write(frame);
                            }

                            if (publisher) {
                                const bool published =
                                    publisher->publish(frame);

                                if (published) {
                                    ++online_stats
                                        .transport_published;
                                } else {
                                    ++online_stats
                                        .transport_dropped;

                                    std::cerr
                                        << "TRANSPORT_DROP"
                                        << " | counter="
                                        << frame.packet_counter
                                        << '\n';
                                }
                            }

                            const double queue_latency_ms =
                                static_cast<double>(
                                    processed_ns
                                    - item->host_received_ns
                                )
                                / 1.0e6;

                            const auto interval_it =
                                frame.numeric_metadata.find(
                                    "beacon_interval_tu"
                                );

                            double interval_ms = 0.0;

                            if (
                                interval_it
                                != frame.numeric_metadata.end()
                            ) {
                                interval_ms =
                                    interval_it->second
                                    * 1.024;
                            }

                            std::cout
                                << "CSI"
                                << " | counter="
                                << frame.packet_counter
                                << " | tx="
                                << frame.transmitter_id
                                << " | exp="
                                << frame.experiment_id
                                << " | CSI="
                                << frame.complex_features.size()
                                << " | SNR="
                                << std::fixed
                                << std::setprecision(2)
                                << frame.snr_db
                                << " dB"
                                << " | CFO="
                                << frame.cfo_hz
                                << " Hz"
                                << " | queue_wait="
                                << queue_wait_ms
                                << " ms"
                                << " | processing="
                                << processing_time_ms
                                << " ms"
                                << " | latency="
                                << queue_latency_ms
                                << " ms"
                                << " | budget="
                                << interval_ms
                                << " ms"
                                << " | queue="
                                << iq_queue.size()
                                << '\n';

                            if (
                                interval_ms > 0.0
                                && queue_latency_ms
                                    >= interval_ms
                            ) {
                                std::cout
                                    << "WARNING: latencia "
                                    << "superior al intervalo "
                                    << "del beacon\n";
                            }
                        }
                    }
                } catch (const std::exception& error) {
                    std::cerr
                        << "ERROR hilo decoder: "
                        << error.what()
                        << '\n';

                    running.store(false);
                }

                iq_queue.close();
            }
        };

        std::jthread status_thread{
            [&] {
                while (running.load()) {
                    std::this_thread::sleep_for(
                        std::chrono::seconds{1}
                    );

                    const double elapsed =
                        std::chrono::duration<double>(
                            std::chrono::steady_clock::now()
                            - application_start
                        ).count();

                    const double rate =
                        elapsed > 0.0
                        ? static_cast<double>(
                            online_stats.rx_samples.load()
                        ) / elapsed
                        : 0.0;

                    const std::uint64_t processed_blocks =
                        online_stats.processed_blocks.load();

                    const double average_processing_ms =
                        processed_blocks > 0U
                        ? (
                            static_cast<double>(
                                online_stats
                                    .total_processing_time_us
                                    .load()
                            )
                            / static_cast<double>(
                                processed_blocks
                            )
                            / 1000.0
                        )
                        : 0.0;

                    const double maximum_processing_ms =
                        static_cast<double>(
                            online_stats
                                .maximum_processing_time_us
                                .load()
                        )
                        / 1000.0;

                    std::cout
                        << "STATUS"
                        << " | rate="
                        << std::fixed
                        << std::setprecision(3)
                        << rate / 1.0e6
                        << " Msps"
                        << " | queue="
                        << iq_queue.size()
                        << "/" << queue_capacity
                        << " | queue_max="
                        << iq_queue.high_water_mark()
                        << " | overflows="
                        << online_stats.overflows.load()
                        << " | timeouts="
                        << online_stats.timeouts.load()
                        << " | frames="
                        << online_stats.frames.load()
                        << " | local_written="
                        << online_stats.local_written.load()
                        << " | tx_ok="
                        << online_stats
                            .transport_published
                            .load()
                        << " | tx_drop="
                        << online_stats
                            .transport_dropped
                            .load()
                        << " | proc_avg="
                        << average_processing_ms
                        << " ms"
                        << " | proc_max="
                        << maximum_processing_ms
                        << " ms"
                        << " | slow10="
                        << online_stats
                            .slow_blocks_over_10ms
                            .load()
                        << " | slow20="
                        << online_stats
                            .slow_blocks_over_20ms
                            .load()
                        << '\n';
                }
            }
        };

        rx_thread.join();

        running.store(false);
        iq_queue.close();

        processing_thread.join();
        status_thread.join();

        source.stop();

        std::cout
            << "\n========================================\n"
            << "RESULTADO ONLINE\n"
            << "========================================\n"
            << "Bloques RX       : "
            << online_stats.rx_blocks.load()
            << '\n'
            << "Muestras RX      : "
            << online_stats.rx_samples.load()
            << '\n'
            << "Overflows        : "
            << online_stats.overflows.load()
            << '\n'
            << "Timeouts         : "
            << online_stats.timeouts.load()
            << '\n'
            << "Discontinuidades : "
            << online_stats.discontinuities.load()
            << '\n'
            << "Cola máxima      : "
            << iq_queue.high_water_mark()
            << '\n'
            << "Candidatos       : "
            << processing_stats.candidates
            << '\n'
            << "Sincronizados    : "
            << processing_stats.synchronized
            << '\n'
            << "Decodificados    : "
            << processing_stats.decoded
            << '\n'
            << "Publicados       : "
            << processing_stats.published
            << '\n'
            << "Guardados JSONL  : "
            << online_stats.local_written.load()
            << '\n'
            << "Transportados    : "
            << online_stats
                .transport_published
                .load()
            << '\n'
            << "Descartados ZMQ  : "
            << online_stats
                .transport_dropped
                .load()
            << '\n';

        return 0;

    } catch (const std::exception& error) {
        std::cerr
            << "ERROR: "
            << error.what()
            << '\n';

        return 1;
    }
}
