#pragma once

#include <array>
#include <cstdint>
#include <optional>
#include <span>
#include <string>
#include <vector>

namespace sensing::wifi_nonht {

struct InformationElement {
    std::uint8_t id{0};
    std::vector<std::uint8_t> data;
};

struct ParsedBeacon {
    bool valid{false};
    bool is_management_frame{false};
    bool is_beacon{false};
    bool fcs_valid{false};

    std::uint16_t frame_control{0};
    std::uint16_t duration{0};
    std::uint16_t sequence_control{0};
    std::uint16_t sequence_number{0};
    std::uint8_t fragment_number{0};

    std::array<std::uint8_t,6> destination{};
    std::array<std::uint8_t,6> source{};
    std::array<std::uint8_t,6> bssid{};

    std::uint64_t timestamp_us{0};
    std::uint16_t beacon_interval_tu{0};
    std::uint16_t capability_information{0};

    std::string ssid;
    std::string bssid_string;

    std::vector<InformationElement>
        information_elements;

    bool has_vendor_magic{false};
    bool vendor_valid{false};

    std::array<std::uint8_t,3> vendor_oui{};
    std::uint8_t vendor_type{0};
    std::string vendor_magic;
    std::uint8_t vendor_version{0};

    std::optional<std::uint16_t> transmitter_id;
    std::optional<std::uint16_t> experiment_id;
    std::optional<std::uint32_t> packet_counter;
};

class BeaconParser {
public:
    [[nodiscard]]
    ParsedBeacon parse(
        std::span<const std::uint8_t> psdu
    ) const;

private:
    [[nodiscard]]
    static std::string format_mac(
        const std::array<std::uint8_t,6>& mac
    );

    [[nodiscard]]
    static bool contains_ascii(
        std::span<const std::uint8_t> data,
        const std::string& text
    );
};

}  // namespace sensing::wifi_nonht
