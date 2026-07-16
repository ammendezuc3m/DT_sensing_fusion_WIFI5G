#pragma once

#include "common/feature_frame.hpp"

#include <cstddef>
#include <filesystem>
#include <fstream>

namespace sensing::io {

class CsiRawWriter {
public:
    explicit CsiRawWriter(
        const std::filesystem::path& path
    );

    void write(const FeatureFrame& frame);

    [[nodiscard]]
    std::size_t frames_written() const noexcept;

private:
    std::ofstream output_;
    std::size_t frames_written_{0};
};

}  // namespace sensing::io
