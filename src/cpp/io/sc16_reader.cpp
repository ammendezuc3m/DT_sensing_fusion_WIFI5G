#include "io/sc16_reader.hpp"

#include <algorithm>
#include <stdexcept>

namespace sensing::io {

namespace {

constexpr float kSc16Scale = 1.0F / 32768.0F;

}  // namespace

Sc16Reader::Sc16Reader(
    const std::filesystem::path& path,
    const double sample_rate_hz
)
    : path_(path),
      input_(path, std::ios::binary),
      sample_rate_hz_(sample_rate_hz) {

    if (sample_rate_hz_ <= 0.0) {
        throw std::invalid_argument(
            "sample_rate_hz debe ser mayor que cero"
        );
    }

    if (!input_) {
        throw std::runtime_error(
            "No se pudo abrir la captura: " + path_.string()
        );
    }

    info_.file_size_bytes =
        std::filesystem::file_size(path_);

    // Cada muestra compleja sc16 ocupa:
    // int16 I + int16 Q = 4 bytes.
    info_.complex_samples =
        static_cast<std::uint64_t>(
            info_.file_size_bytes / 4U
        );

    info_.duration_seconds =
        static_cast<double>(info_.complex_samples)
        / sample_rate_hz_;
}

const Sc16FileInfo& Sc16Reader::info() const noexcept {
    return info_;
}

bool Sc16Reader::eof() const noexcept {
    return input_.eof()
        || samples_read_ >= info_.complex_samples;
}

std::uint64_t Sc16Reader::samples_read() const noexcept {
    return samples_read_;
}

std::size_t Sc16Reader::read(
    const std::span<std::complex<float>> output
) {
    if (output.empty() || eof()) {
        return 0;
    }

    const std::uint64_t remaining =
        info_.complex_samples - samples_read_;

    const std::size_t requested =
        static_cast<std::size_t>(
            std::min<std::uint64_t>(
                output.size(),
                remaining
            )
        );

    raw_buffer_.resize(requested * 2U);

    input_.read(
        reinterpret_cast<char*>(raw_buffer_.data()),
        static_cast<std::streamsize>(
            raw_buffer_.size()
            * sizeof(std::int16_t)
        )
    );

    const std::streamsize bytes_read = input_.gcount();

    if (bytes_read <= 0) {
        return 0;
    }

    const std::size_t int16_values =
        static_cast<std::size_t>(bytes_read)
        / sizeof(std::int16_t);

    const std::size_t complete_samples =
        int16_values / 2U;

    for (std::size_t index = 0;
         index < complete_samples;
         ++index) {

        const float in_phase =
            static_cast<float>(
                raw_buffer_[2U * index]
            ) * kSc16Scale;

        const float quadrature =
            static_cast<float>(
                raw_buffer_[2U * index + 1U]
            ) * kSc16Scale;

        output[index] = {
            in_phase,
            quadrature
        };
    }

    samples_read_ += complete_samples;

    return complete_samples;
}

}  // namespace sensing::io
