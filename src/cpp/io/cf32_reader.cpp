#include "io/cf32_reader.hpp"

#include <cstdint>
#include <fstream>
#include <stdexcept>
#include <vector>

namespace sensing::io {

std::vector<std::complex<float>> read_cf32_file(
    const std::filesystem::path& path
) {
    std::ifstream input(path, std::ios::binary);

    if (!input) {
        throw std::runtime_error(
            "No se pudo abrir la referencia: "
            + path.string()
        );
    }

    input.seekg(0, std::ios::end);
    const auto bytes = input.tellg();
    input.seekg(0, std::ios::beg);

    if (bytes <= 0
        || bytes % static_cast<std::streamoff>(
            2 * sizeof(float)
        ) != 0) {
        throw std::runtime_error(
            "Formato cf32 inválido: " + path.string()
        );
    }

    const std::size_t complex_samples =
        static_cast<std::size_t>(bytes)
        / (2U * sizeof(float));

    std::vector<float> raw(complex_samples * 2U);

    input.read(
        reinterpret_cast<char*>(raw.data()),
        static_cast<std::streamsize>(
            raw.size() * sizeof(float)
        )
    );

    if (!input) {
        throw std::runtime_error(
            "Error leyendo la referencia: "
            + path.string()
        );
    }

    std::vector<std::complex<float>> result(
        complex_samples
    );

    for (std::size_t i = 0; i < complex_samples; ++i) {
        result[i] = {
            raw[2U * i],
            raw[2U * i + 1U]
        };
    }

    return result;
}

}  // namespace sensing::io
