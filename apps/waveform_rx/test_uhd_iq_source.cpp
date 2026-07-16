#include "sources/uhd_iq_source.hpp"

#include <algorithm>
#include <atomic>
#include <chrono>
#include <cmath>
#include <complex>
#include <csignal>
#include <cstddef>
#include <cstdint>
#include <iomanip>
#include <iostream>
#include <span>
#include <vector>

namespace {

std::atomic<bool> running{true};

void handle_signal(int) {
    running.store(false);
}

}  // namespace

int main() {
    try {
        std::signal(SIGINT, handle_signal);
        std::signal(SIGTERM, handle_signal);

        sensing::UhdIqSourceConfig config;

        config.device_args =
            "serial=34B73C3";

        config.channel = 0U;
        config.sample_rate_hz = 20.0e6;
        config.center_frequency_hz = 2.462e9;
        config.gain_db = 35.0;
        config.bandwidth_hz = 20.0e6;
        config.antenna = "RX2";
        config.clock_source = "internal";
        config.time_source = "internal";

        sensing::UhdIqSource source{
            config
        };

        constexpr std::size_t block_samples =
            200'000U;

        std::vector<std::complex<float>> buffer(
            block_samples
        );

        std::uint64_t total_samples = 0U;
        std::uint64_t blocks = 0U;
        std::uint64_t overflows = 0U;
        std::uint64_t timeouts = 0U;

        const auto start =
            std::chrono::steady_clock::now();

        source.start();

        std::cout
            << "USRP RX activo. Ctrl+C para terminar.\n"
            << "Sample rate: "
            << source.sample_rate_hz() / 1.0e6
            << " Msps\n"
            << "Frecuencia: "
            << source.center_frequency_hz() / 1.0e6
            << " MHz\n";

        while (running.load()) {
            const auto result =
                source.read(buffer);

            if (result.timeout) {
                ++timeouts;
            }

            if (result.overflow) {
                ++overflows;
            }

            if (result.samples_received == 0U) {
                continue;
            }

            ++blocks;

            total_samples +=
                result.samples_received;

            if ((blocks % 100U) == 0U) {
                double power = 0.0;

                for (
                    std::size_t index = 0U;
                    index < result.samples_received;
                    ++index
                ) {
                    power += std::norm(
                        buffer[index]
                    );
                }

                power /= static_cast<double>(
                    result.samples_received
                );

                const double rms =
                    std::sqrt(power);

                const auto now =
                    std::chrono::steady_clock::now();

                const double elapsed_seconds =
                    std::chrono::duration<double>(
                        now - start
                    ).count();

                const double effective_rate =
                    static_cast<double>(
                        total_samples
                    )
                    / elapsed_seconds;

                std::cout
                    << "blocks=" << blocks
                    << " samples=" << total_samples
                    << " rate="
                    << std::fixed
                    << std::setprecision(3)
                    << effective_rate / 1.0e6
                    << " Msps"
                    << " rms=" << rms
                    << " overflows=" << overflows
                    << " timeouts=" << timeouts
                    << " timestamp_ns="
                    << result.timestamp_ns
                    << '\n';
            }
        }

        source.stop();

        std::cout
            << "\nRecepción finalizada\n"
            << "Muestras : " << total_samples << '\n'
            << "Bloques  : " << blocks << '\n'
            << "Overflows: " << overflows << '\n'
            << "Timeouts : " << timeouts << '\n';

        return 0;

    } catch (const std::exception& error) {
        std::cerr
            << "ERROR: "
            << error.what()
            << '\n';

        return 1;
    }
}
