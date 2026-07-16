#include "io/psdu_writer.hpp"

#include <iomanip>
#include <sstream>
#include <stdexcept>

namespace sensing::io {

PsduWriter::PsduWriter(
    const std::filesystem::path& directory
)
    : directory_(directory) {

    std::filesystem::create_directories(
        directory_
    );
}

void PsduWriter::write(
    const std::uint64_t packet_index,
    const std::span<const std::uint8_t> psdu
) const {
    std::ostringstream filename;

    filename
        << "packet_"
        << std::setw(4)
        << std::setfill('0')
        << packet_index
        << ".bin";

    const auto path =
        directory_ / filename.str();

    std::ofstream output(
        path,
        std::ios::binary
    );

    if (!output) {
        throw std::runtime_error(
            "No se pudo crear PSDU: "
            + path.string()
        );
    }

    output.write(
        reinterpret_cast<const char*>(
            psdu.data()
        ),
        static_cast<std::streamsize>(
            psdu.size()
        )
    );

    if (!output) {
        throw std::runtime_error(
            "Error escribiendo PSDU: "
            + path.string()
        );
    }
}

}  // namespace sensing::io
