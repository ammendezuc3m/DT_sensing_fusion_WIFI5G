#pragma once

#include <cstdint>
#include <filesystem>
#include <fstream>
#include <span>

namespace sensing::io {

class PsduWriter {
public:
    explicit PsduWriter(
        const std::filesystem::path& directory
    );

    void write(
        std::uint64_t packet_index,
        std::span<const std::uint8_t> psdu
    ) const;

private:
    std::filesystem::path directory_;
};

}  // namespace sensing::io
