#pragma once

#include <complex>
#include <cstdint>
#include <vector>

namespace sensing {

struct IqBlock {
    std::uint64_t first_sample_index{0};
    std::uint64_t timestamp_ns{0};

    double sample_rate_hz{0.0};
    double center_frequency_hz{0.0};

    std::vector<std::complex<float>> samples;
};

}  // namespace sensing
