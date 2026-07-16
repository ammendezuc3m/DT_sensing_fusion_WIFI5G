#include "waveforms/wifi_nonht/beacon_parser.hpp"

#include "waveforms/wifi_nonht/wifi_fcs.hpp"

#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <iomanip>
#include <sstream>

namespace sensing::wifi_nonht {

namespace {

std::uint16_t read_u16_le(
    const std::span<const std::uint8_t> bytes,
    const std::size_t offset
) {
    return static_cast<std::uint16_t>(
        static_cast<std::uint16_t>(
            bytes[offset]
        )
        | (
            static_cast<std::uint16_t>(
                bytes[offset + 1U]
            ) << 8U
        )
    );
}

std::uint16_t read_u16_be(
    const std::span<const std::uint8_t> bytes,
    const std::size_t offset
) {
    return static_cast<std::uint16_t>(
        (
            static_cast<std::uint16_t>(
                bytes[offset]
            ) << 8U
        )
        | static_cast<std::uint16_t>(
            bytes[offset + 1U]
        )
    );
}

std::uint32_t read_u32_be(
    const std::span<const std::uint8_t> bytes,
    const std::size_t offset
) {
    return
        (
            static_cast<std::uint32_t>(
                bytes[offset]
            ) << 24U
        )
        | (
            static_cast<std::uint32_t>(
                bytes[offset + 1U]
            ) << 16U
        )
        | (
            static_cast<std::uint32_t>(
                bytes[offset + 2U]
            ) << 8U
        )
        | static_cast<std::uint32_t>(
            bytes[offset + 3U]
        );
}

std::uint32_t read_u32_le(
    const std::span<const std::uint8_t> bytes,
    const std::size_t offset
) {
    return
        static_cast<std::uint32_t>(
            bytes[offset]
        )
        | (
            static_cast<std::uint32_t>(
                bytes[offset + 1U]
            ) << 8U
        )
        | (
            static_cast<std::uint32_t>(
                bytes[offset + 2U]
            ) << 16U
        )
        | (
            static_cast<std::uint32_t>(
                bytes[offset + 3U]
            ) << 24U
        );
}

std::uint64_t read_u64_le(
    const std::span<const std::uint8_t> bytes,
    const std::size_t offset
) {
    std::uint64_t value = 0U;

    for (std::size_t index = 0;
         index < 8U;
         ++index) {

        value |= (
            static_cast<std::uint64_t>(
                bytes[offset + index]
            ) << (8U * index)
        );
    }

    return value;
}

}  // namespace

ParsedBeacon BeaconParser::parse(
    const std::span<const std::uint8_t> psdu
) const {
    ParsedBeacon result;

    /*
     * Beacon mínimo:
     *
     * MAC header:     24 bytes
     * Fixed fields:   12 bytes
     * FCS:             4 bytes
     */
    if (psdu.size() < 40U) {
        return result;
    }

    result.fcs_valid = validate_wifi_fcs(psdu);

    result.frame_control =
        read_u16_le(psdu,0U);

    result.duration =
        read_u16_le(psdu,2U);

    const std::uint8_t type =
        static_cast<std::uint8_t>(
            (result.frame_control >> 2U) & 0x3U
        );

    const std::uint8_t subtype =
        static_cast<std::uint8_t>(
            (result.frame_control >> 4U) & 0xFU
        );

    result.is_management_frame =
        type == 0U;

    result.is_beacon =
        result.is_management_frame
        && subtype == 8U;

    std::copy_n(
        psdu.begin() + 4,
        6,
        result.destination.begin()
    );

    std::copy_n(
        psdu.begin() + 10,
        6,
        result.source.begin()
    );

    std::copy_n(
        psdu.begin() + 16,
        6,
        result.bssid.begin()
    );

    result.bssid_string =
        format_mac(result.bssid);

    result.sequence_control =
        read_u16_le(psdu,22U);

    result.fragment_number =
        static_cast<std::uint8_t>(
            result.sequence_control & 0xFU
        );

    result.sequence_number =
        static_cast<std::uint16_t>(
            result.sequence_control >> 4U
        );

    result.timestamp_us =
        read_u64_le(psdu,24U);

    result.beacon_interval_tu =
        read_u16_le(psdu,32U);

    result.capability_information =
        read_u16_le(psdu,34U);

    /*
     * Information Elements empiezan en byte 36
     * y terminan antes de los cuatro bytes FCS.
     */
    std::size_t offset = 36U;
    const std::size_t ie_end =
        psdu.size() - 4U;

    while (offset + 2U <= ie_end) {
        const std::uint8_t id =
            psdu[offset];

        const std::size_t length =
            psdu[offset + 1U];

        offset += 2U;

        if (offset + length > ie_end) {
            return result;
        }

        InformationElement element;
        element.id = id;

        element.data.assign(
            psdu.begin()
                + static_cast<std::ptrdiff_t>(offset),
            psdu.begin()
                + static_cast<std::ptrdiff_t>(
                    offset + length
                )
        );

        if (id == 0U) {
            result.ssid.assign(
                element.data.begin(),
                element.data.end()
            );
        }

        /*
         * Element ID 221 = Vendor Specific IE.
         *
         * Buscamos ALBSENS en cualquier posición de los
         * datos para mantener compatibilidad con el
         * formato experimental actual.
         */
        /*
         * Formato Vendor IE ALBSENS v1:
         *
         *  0..2   OUI
         *  3      vendor_type
         *  4..11  magic ASCII, rellenado con cero
         * 12      version
         * 13..14  transmitter_id, big-endian
         * 15..16  experiment_id, big-endian
         * 17..20  packet_counter, big-endian
         */
        if (id == 221U && element.data.size() >= 21U) {
            const auto vendor_data =
                std::span<const std::uint8_t>{
                    element.data
                };

            std::copy_n(
                vendor_data.begin(),
                3,
                result.vendor_oui.begin()
            );

            result.vendor_type =
                vendor_data[3U];

            constexpr std::size_t magic_offset = 4U;
            constexpr std::size_t magic_size = 8U;

            std::size_t actual_magic_size = 0U;

            while (
                actual_magic_size < magic_size
                && vendor_data[
                    magic_offset + actual_magic_size
                ] != 0U
            ) {
                ++actual_magic_size;
            }

            result.vendor_magic.assign(
                reinterpret_cast<const char*>(
                    vendor_data.data() + magic_offset
                ),
                actual_magic_size
            );

            result.vendor_version =
                vendor_data[12U];

            result.transmitter_id =
                read_u16_be(vendor_data,13U);

            result.experiment_id =
                read_u16_be(vendor_data,15U);

            result.packet_counter =
                read_u32_be(vendor_data,17U);

            result.has_vendor_magic =
                result.vendor_magic == "ALBSENS";

            result.vendor_valid =
                result.has_vendor_magic;
        }

        result.information_elements.push_back(
            std::move(element)
        );

        offset += length;
    }

    result.valid =
        result.is_beacon
        && offset == ie_end;

    return result;
}

std::string BeaconParser::format_mac(
    const std::array<std::uint8_t,6>& mac
) {
    std::ostringstream stream;

    stream
        << std::hex
        << std::setfill('0');

    for (std::size_t index = 0;
         index < mac.size();
         ++index) {

        if (index > 0U) {
            stream << ':';
        }

        stream
            << std::setw(2)
            << static_cast<unsigned int>(
                mac[index]
            );
    }

    return stream.str();
}

bool BeaconParser::contains_ascii(
    const std::span<const std::uint8_t> data,
    const std::string& text
) {
    if (text.empty()
        || data.size() < text.size()) {

        return false;
    }

    return std::search(
        data.begin(),
        data.end(),
        text.begin(),
        text.end(),
        [](
            const std::uint8_t byte,
            const char character
        ) {
            return byte
                == static_cast<std::uint8_t>(
                    character
                );
        }
    ) != data.end();
}

}  // namespace sensing::wifi_nonht
