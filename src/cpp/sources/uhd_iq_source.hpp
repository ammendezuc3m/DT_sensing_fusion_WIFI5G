#pragma once

#include "sources/iq_source.hpp"

#include <uhd/stream.hpp>
#include <uhd/usrp/multi_usrp.hpp>

#include <cstddef>
#include <memory>
#include <string>

namespace sensing {

struct UhdIqSourceConfig {
    std::string device_args{"type=b200"};

    std::size_t channel{0};

    double sample_rate_hz{20.0e6};
    double center_frequency_hz{2.462e9};
    double gain_db{35.0};
    double bandwidth_hz{20.0e6};

    std::string antenna{"RX2"};
    std::string clock_source{"internal"};
    std::string time_source{"internal"};

    std::string cpu_format{"fc32"};
    std::string wire_format{"sc16"};

    double receive_timeout_seconds{1.0};
};

class UhdIqSource final : public IIqSource {
public:
    explicit UhdIqSource(
        UhdIqSourceConfig config
    );

    ~UhdIqSource() override;

    [[nodiscard]]
    std::string name() const override;

    void start() override;
    void stop() override;

    IqReadResult read(
        std::span<std::complex<float>> output
    ) override;

    [[nodiscard]]
    double sample_rate_hz() const override;

    [[nodiscard]]
    double center_frequency_hz() const override;

private:
    UhdIqSourceConfig config_;

    uhd::usrp::multi_usrp::sptr usrp_;
    uhd::rx_streamer::sptr rx_stream_;

    bool running_{false};
};

}  // namespace sensing
