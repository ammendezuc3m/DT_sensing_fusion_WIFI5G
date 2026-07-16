#pragma once

#include "publishers/feature_publisher.hpp"

#include <cstddef>
#include <memory>
#include <string>

namespace sensing {

struct ZeroMqPublisherConfig {
    std::string endpoint{
        "tcp://127.0.0.1:5555"
    };

    int send_high_water_mark{1000};

    /*
     * Con cero, el cierre no espera a entregar
     * mensajes pendientes.
     */
    int linger_ms{0};

    /*
     * Si es true, publish() no bloquea.
     * Cuando la cola ZeroMQ está llena, el frame
     * se considera descartado.
     */
    bool non_blocking{true};
};

class ZeroMqFeaturePublisher final
    : public IFeaturePublisher {
public:
    explicit ZeroMqFeaturePublisher(
        ZeroMqPublisherConfig config
    );

    ~ZeroMqFeaturePublisher() override;

    ZeroMqFeaturePublisher(
        const ZeroMqFeaturePublisher&
    ) = delete;

    ZeroMqFeaturePublisher& operator=(
        const ZeroMqFeaturePublisher&
    ) = delete;

    ZeroMqFeaturePublisher(
        ZeroMqFeaturePublisher&&
    ) noexcept;

    ZeroMqFeaturePublisher& operator=(
        ZeroMqFeaturePublisher&&
    ) noexcept;

    [[nodiscard]]
    std::string name() const override;

    bool publish(
        const FeatureFrame& frame
    ) override;

private:
    class Impl;
    std::unique_ptr<Impl> impl_;
};

}  // namespace sensing
