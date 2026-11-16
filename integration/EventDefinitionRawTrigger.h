// SPDX-License-Identifier: LGPL-3.0-or-later
#ifndef EVENT_DEFINITION_RAW_TRIGGER_H
#define EVENT_DEFINITION_RAW_TRIGGER_H
#include <memory>
#include <vector>
#include "core/module/Module.hpp"
#include "eudaq/FileReader.hh"
namespace corryvreckan {
class EventDefinitionRawTrigger : public Module {
public:
    EventDefinitionRawTrigger(Configuration&, std::vector<std::shared_ptr<Detector>>);
    void initialize() override;
    StatusCode run(const std::shared_ptr<Clipboard>&) override;
private:
    eudaq::FileReaderUP reader_;
};
}
#endif
