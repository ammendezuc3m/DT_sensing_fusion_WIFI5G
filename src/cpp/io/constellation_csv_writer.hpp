#pragma once

#include "waveforms/wifi_nonht/data_symbol_extractor.hpp"

#include <cstdint>
#include <filesystem>
#include <fstream>

namespace sensing::io {

class ConstellationCsvWriter {
public:
    explicit ConstellationCsvWriter(
        const std::filesystem::path& path
    );

    void write(
        std::uint64_t packet_index,
        const wifi_nonht::DataSymbolsResult& symbols
    );

private:
    std::ofstream output_;
};

}  // namespace sensing::io
