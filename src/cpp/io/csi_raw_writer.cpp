#include "io/csi_raw_writer.hpp"

#include <complex>
#include <cstdint>
#include <filesystem>
#include <stdexcept>
#include <vector>

namespace sensing::io {

namespace {

struct ComplexFloat32 {
    float real;
    float imag;
};

static_assert(
    sizeof(ComplexFloat32) == 2 * sizeof(float)
);

}  // namespace

CsiRawWriter::CsiRawWriter(
    const std::filesystem::path& path
) {
    if (
        path.has_parent_path()
        && !path.parent_path().empty()
    ) {
        std::filesystem::create_directories(
            path.parent_path()
        );
    }

    output_.open(
        path,
        std::ios::binary | std::ios::trunc
    );

    if (!output_) {
        throw std::runtime_error(
            "No se pudo crear el fichero CSI raw: "
            + path.string()
        );
    }
}

void CsiRawWriter::write(
    const FeatureFrame& frame
) {
    if (frame.complex_features.empty()) {
        return;
    }

    std::vector<ComplexFloat32> values;
    values.reserve(frame.complex_features.size());

    for (const auto& value :
         frame.complex_features) {
        values.push_back({
            value.real(),
            value.imag()
        });
    }

    output_.write(
        reinterpret_cast<const char*>(
            values.data()
        ),
        static_cast<std::streamsize>(
            values.size()
            * sizeof(ComplexFloat32)
        )
    );

    output_.flush();

    if (!output_) {
        throw std::runtime_error(
            "Error escribiendo CSI raw"
        );
    }

    ++frames_written_;
}

std::size_t CsiRawWriter::frames_written()
    const noexcept {
    return frames_written_;
}

}  // namespace sensing::io
