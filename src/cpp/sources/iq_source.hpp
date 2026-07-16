#pragma once

#include <complex>
#include <cstddef>
#include <cstdint>
#include <span>
#include <string>

namespace sensing {

struct IqReadResult {
    std::size_t samples_received{0};

    /*
     * Timestamp de la primera muestra del bloque.
     */
    std::uint64_t timestamp_ns{0};

    bool timeout{false};
    bool overflow{false};
    bool discontinuity{false};
};

class IIqSource {
public:
    virtual ~IIqSource() = default;

    [[nodiscard]]
    virtual std::string name() const = 0;

    virtual void start() = 0;
    virtual void stop() = 0;

    virtual IqReadResult read(
        std::span<std::complex<float>> output
    ) = 0;

    [[nodiscard]]
    virtual double sample_rate_hz() const = 0;

    [[nodiscard]]
    virtual double center_frequency_hz() const = 0;
};

}  // namespace sensing
