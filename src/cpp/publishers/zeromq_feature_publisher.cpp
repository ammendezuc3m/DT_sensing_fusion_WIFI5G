#include "publishers/zeromq_feature_publisher.hpp"

#include "publishers/feature_json_serializer.hpp"

#include <zmq.hpp>

#include <stdexcept>
#include <string>
#include <utility>

namespace sensing {

class ZeroMqFeaturePublisher::Impl {
public:
    explicit Impl(
        ZeroMqPublisherConfig input_config
    )
        : config{std::move(input_config)},
          context{1},
          socket{
              context,
              zmq::socket_type::push
          } {
        if (config.endpoint.empty()) {
            throw std::invalid_argument(
                "El endpoint ZeroMQ está vacío"
            );
        }

        socket.set(
            zmq::sockopt::sndhwm,
            config.send_high_water_mark
        );

        socket.set(
            zmq::sockopt::linger,
            config.linger_ms
        );

        /*
         * PUSH conecta y el receptor PULL realiza bind.
         * Esto permite arrancar primero cualquiera de
         * los dos procesos.
         */
        socket.connect(config.endpoint);
    }

    ZeroMqPublisherConfig config;
    zmq::context_t context;
    zmq::socket_t socket;
};

ZeroMqFeaturePublisher::
ZeroMqFeaturePublisher(
    ZeroMqPublisherConfig config
)
    : impl_{
          std::make_unique<Impl>(
              std::move(config)
          )
      } {
}

ZeroMqFeaturePublisher::
~ZeroMqFeaturePublisher() = default;

ZeroMqFeaturePublisher::
ZeroMqFeaturePublisher(
    ZeroMqFeaturePublisher&&
) noexcept = default;

ZeroMqFeaturePublisher&
ZeroMqFeaturePublisher::operator=(
    ZeroMqFeaturePublisher&&
) noexcept = default;

std::string
ZeroMqFeaturePublisher::name() const {
    return
        "zeromq_push:"
        + impl_->config.endpoint;
}

bool ZeroMqFeaturePublisher::publish(
    const FeatureFrame& frame
) {
    const std::string message =
        feature_frame_to_json_string(frame);

    const auto flags =
        impl_->config.non_blocking
        ? zmq::send_flags::dontwait
        : zmq::send_flags::none;

    try {
        const auto result =
            impl_->socket.send(
                zmq::buffer(message),
                flags
            );

        return result.has_value();
    } catch (
        const zmq::error_t& error
    ) {
        if (
            error.num() == EAGAIN
            && impl_->config.non_blocking
        ) {
            return false;
        }

        throw;
    }
}

}  // namespace sensing
