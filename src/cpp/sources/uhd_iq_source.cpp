#include "sources/uhd_iq_source.hpp"

#include <uhd/types/metadata.hpp>
#include <uhd/types/stream_cmd.hpp>
#include <uhd/types/tune_request.hpp>

#include <chrono>
#include <cmath>
#include <complex>
#include <cstddef>
#include <cstdint>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

namespace sensing {

namespace {

std::uint64_t seconds_to_nanoseconds(
    const double seconds
) {
    if (seconds <= 0.0) {
        return 0U;
    }

    return static_cast<std::uint64_t>(
        std::llround(seconds * 1.0e9)
    );
}

}  // namespace

UhdIqSource::UhdIqSource(
    UhdIqSourceConfig config
)
    : config_(std::move(config)) {

    if (config_.sample_rate_hz <= 0.0) {
        throw std::invalid_argument(
            "UHD: sample_rate_hz debe ser positivo"
        );
    }

    if (config_.center_frequency_hz <= 0.0) {
        throw std::invalid_argument(
            "UHD: center_frequency_hz debe ser positivo"
        );
    }

    usrp_ = uhd::usrp::multi_usrp::make(
        config_.device_args
    );

    usrp_->set_clock_source(
        config_.clock_source
    );

    usrp_->set_time_source(
        config_.time_source
    );

    usrp_->set_rx_rate(
        config_.sample_rate_hz,
        config_.channel
    );

    usrp_->set_rx_freq(
        uhd::tune_request_t{
            config_.center_frequency_hz
        },
        config_.channel
    );

    usrp_->set_rx_gain(
        config_.gain_db,
        config_.channel
    );

    if (config_.bandwidth_hz > 0.0) {
        usrp_->set_rx_bandwidth(
            config_.bandwidth_hz,
            config_.channel
        );
    }

    if (!config_.antenna.empty()) {
        usrp_->set_rx_antenna(
            config_.antenna,
            config_.channel
        );
    }

    uhd::stream_args_t stream_args{
        config_.cpu_format,
        config_.wire_format
    };

    stream_args.channels = {
        config_.channel
    };

    rx_stream_ = usrp_->get_rx_stream(
        stream_args
    );

    if (!rx_stream_) {
        throw std::runtime_error(
            "UHD: no se pudo crear rx_stream"
        );
    }
}

UhdIqSource::~UhdIqSource() {
    try {
        stop();
    } catch (...) {
    }
}

std::string UhdIqSource::name() const {
    return "uhd";
}

void UhdIqSource::start() {
    if (running_) {
        return;
    }

    /*
     * Reiniciar el tiempo del dispositivo. En el B210
     * interno nos permite obtener timestamps relativos
     * coherentes desde el inicio de la ejecución.
     */
    usrp_->set_time_now(
        uhd::time_spec_t{0.0}
    );

    uhd::stream_cmd_t command{
        uhd::stream_cmd_t::STREAM_MODE_START_CONTINUOUS
    };

    command.stream_now = true;

    rx_stream_->issue_stream_cmd(command);

    running_ = true;
}

void UhdIqSource::stop() {
    if (!running_ || !rx_stream_) {
        return;
    }

    uhd::stream_cmd_t command{
        uhd::stream_cmd_t::STREAM_MODE_STOP_CONTINUOUS
    };

    rx_stream_->issue_stream_cmd(command);

    running_ = false;
}

IqReadResult UhdIqSource::read(
    const std::span<std::complex<float>> output
) {
    if (!running_) {
        throw std::logic_error(
            "UHD: read() llamado antes de start()"
        );
    }

    IqReadResult result;

    if (output.empty()) {
        return result;
    }

    std::size_t total_received = 0U;
    bool first_metadata = true;

    while (total_received < output.size()) {
        uhd::rx_metadata_t metadata;

        const std::size_t remaining =
            output.size() - total_received;

        const std::size_t received =
            rx_stream_->recv(
                output.data() + total_received,
                remaining,
                metadata,
                config_.receive_timeout_seconds,
                false
            );

        if (
            metadata.error_code
            == uhd::rx_metadata_t::ERROR_CODE_TIMEOUT
        ) {
            result.timeout = true;
            break;
        }

        if (
            metadata.error_code
            == uhd::rx_metadata_t::ERROR_CODE_OVERFLOW
        ) {
            result.overflow = true;
            result.discontinuity = true;

            /*
             * UHD puede devolver cero muestras en un
             * overflow. Continuamos intentando llenar el
             * bloque para no detener la recepción.
             */
            if (received == 0U) {
                continue;
            }
        } else if (
            metadata.error_code
            != uhd::rx_metadata_t::ERROR_CODE_NONE
        ) {
            throw std::runtime_error(
                "UHD RX: "
                + metadata.strerror()
            );
        }

        if (
            first_metadata
            && metadata.has_time_spec
        ) {
            result.timestamp_ns =
                seconds_to_nanoseconds(
                    metadata.time_spec
                        .get_real_secs()
                );

            first_metadata = false;
        }

        total_received += received;
    }

    result.samples_received =
        total_received;

    return result;
}

double UhdIqSource::sample_rate_hz() const {
    return usrp_->get_rx_rate(
        config_.channel
    );
}

double UhdIqSource::center_frequency_hz() const {
    return usrp_->get_rx_freq(
        config_.channel
    );
}

}  // namespace sensing
