#pragma once

#include "waveforms/wifi_nonht/channel_estimator.hpp"
#include "waveforms/wifi_nonht/legacy_signal_decoder.hpp"
#include "waveforms/wifi_nonht/synchronizer.hpp"

#include <complex>
#include <cstddef>
#include <span>
#include <vector>

namespace sensing::wifi_nonht {

struct DataSymbolsResult {
    bool valid{false};

    std::size_t number_of_symbols{0};

    std::vector<std::complex<float>>
        equalized_data_subcarriers;
};

class DataSymbolExtractor {
public:
    [[nodiscard]]
    DataSymbolsResult extract(
        std::span<const std::complex<float>> samples,
        const SyncResult& sync,
        const ChannelEstimate& channel,
        const LegacySignalResult& lsig
    ) const;
};

}  // namespace sensing::wifi_nonht
