#pragma once

#include <cstdint>

namespace sensing {

enum class WaveformType : std::uint8_t {
    Unknown            = 0,
    WifiNonHtBeacon    = 1,
    WifiNdp            = 2,
    CustomOfdm         = 3,
    BeamformingTraining = 4,
    NrSsb              = 5,
    RawReference       = 6
};

}  // namespace sensing
