#pragma once

#include <complex>
#include <cstddef>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <span>
#include <string>
#include <vector>

namespace sensing::io {

struct Sc16FileInfo {
    std::uintmax_t file_size_bytes{0};
    std::uint64_t complex_samples{0};
    double duration_seconds{0.0};
};

class Sc16Reader {
public:
    Sc16Reader(
        const std::filesystem::path& path,
        double sample_rate_hz
    );

    Sc16Reader(const Sc16Reader&) = delete;
    Sc16Reader& operator=(const Sc16Reader&) = delete;

    [[nodiscard]]
    const Sc16FileInfo& info() const noexcept;

    [[nodiscard]]
    bool eof() const noexcept;

    [[nodiscard]]
    std::uint64_t samples_read() const noexcept;

    std::size_t read(
        std::span<std::complex<float>> output
    );

private:
    std::filesystem::path path_;
    std::ifstream input_;
    double sample_rate_hz_{0.0};
    Sc16FileInfo info_{};
    std::uint64_t samples_read_{0};

    std::vector<std::int16_t> raw_buffer_;
};

}  // namespace sensing::io
