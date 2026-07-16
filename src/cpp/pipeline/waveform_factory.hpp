#pragma once

#include "pipeline/waveform_processor.hpp"

#include <memory>
#include <string>

#include <nlohmann/json.hpp>

namespace sensing {

std::unique_ptr<IWaveformProcessor>
create_waveform_processor(
    const std::string& waveform_name,
    const nlohmann::json& waveform_config,
    double sample_rate_hz
);

}  // namespace sensing
