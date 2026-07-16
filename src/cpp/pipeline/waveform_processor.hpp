#pragma once

#include "common/feature_frame.hpp"
#include "common/iq_block.hpp"

#include <string>
#include <vector>

namespace sensing {

struct ProcessingStats {
    std::uint64_t candidates{0};
    std::uint64_t synchronized{0};
    std::uint64_t decoded{0};
    std::uint64_t rejected{0};
    std::uint64_t published{0};
};

class IWaveformProcessor {
public:
    virtual ~IWaveformProcessor() = default;

    [[nodiscard]]
    virtual std::string name() const = 0;

    virtual std::vector<FeatureFrame> process(
        const IqBlock& block,
        ProcessingStats& stats
    ) = 0;

    virtual void reset() = 0;
};

}  // namespace sensing
