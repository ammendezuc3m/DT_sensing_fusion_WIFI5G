#include "io/constellation_csv_writer.hpp"

#include <stdexcept>

namespace sensing::io {

ConstellationCsvWriter::ConstellationCsvWriter(
    const std::filesystem::path& path
)
    : output_(path) {

    if (!output_) {
        throw std::runtime_error(
            "No se pudo crear CSV de constelación: "
            + path.string()
        );
    }

    output_
        << "packet_index"
        << ",ofdm_symbol"
        << ",data_subcarrier_index"
        << ",real"
        << ",imag\n";
}

void ConstellationCsvWriter::write(
    const std::uint64_t packet_index,
    const wifi_nonht::DataSymbolsResult& symbols
) {
    constexpr std::size_t subcarriers_per_symbol = 48;

    for (std::size_t index = 0;
         index < symbols.equalized_data_subcarriers.size();
         ++index) {

        const auto value =
            symbols.equalized_data_subcarriers[index];

        output_
            << packet_index
            << ',' << index / subcarriers_per_symbol
            << ',' << index % subcarriers_per_symbol
            << ',' << value.real()
            << ',' << value.imag()
            << '\n';
    }
}

}  // namespace sensing::io
