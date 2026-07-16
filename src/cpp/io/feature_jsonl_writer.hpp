#pragma once

#include "common/feature_frame.hpp"

#include <filesystem>
#include <fstream>

namespace sensing::io {

class FeatureJsonlWriter {
public:
    explicit FeatureJsonlWriter(
        const std::filesystem::path& path
    );

    void write(const FeatureFrame& frame);

private:
    std::ofstream output_;
};

}  // namespace sensing::io
