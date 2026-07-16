#pragma once

#include "common/feature_frame.hpp"

#include <string>

namespace sensing {

class IFeaturePublisher {
public:
    virtual ~IFeaturePublisher() = default;

    [[nodiscard]]
    virtual std::string name() const = 0;

    virtual bool publish(
        const FeatureFrame& frame
    ) = 0;
};

}  // namespace sensing
