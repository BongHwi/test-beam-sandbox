// SPDX-License-Identifier: LGPL-3.0-or-later
// MSENSOR status events are metadata, not pixel data. Register their names so
// EUDAQ's standard converter skips them without reporting unknown event types.
#include "eudaq/StdEventConverter.hh"
#include "eudaq/Utils.hh"
#include <string>
class MSensorStatusConverter final : public eudaq::StdEventConverter {
public:
    bool Converting(eudaq::EventSPC, eudaq::StdEventSP, eudaq::ConfigSPC) const override { return false; }
};
namespace {
struct RegisterStatuses {
    RegisterStatuses() {
        for (int n = 0; n < 64; ++n)
            eudaq::Factory<eudaq::StdEventConverter>::Register<MSensorStatusConverter>(
                eudaq::str2hash("MSENSOR_" + std::to_string(n) + "_status"));
    }
} registration;
}
