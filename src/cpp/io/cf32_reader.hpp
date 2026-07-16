#pragma once

#include <complex>
#include <filesystem>
#include <vector>

namespace sensing::io {

std::vector<std::complex<float>> read_cf32_file(
    const std::filesystem::path& path
);

}  // namespace sensing::io
