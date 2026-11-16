// SPDX-License-Identifier: LGPL-3.0-or-later
// Define Corryvreckan events from the actual public ITS3 global raw trigger.
// This reads no source configuration or truth; detector data remain with the
// public EventLoaderEUDAQ2 and the original EUDAQ converters.
#include "EventDefinitionRawTrigger.h"
#include "objects/Event.hpp"
#include "eudaq/Factory.hh"
#include "eudaq/Utils.hh"
#include "eudaq/RawEvent.hh"
#include <stdexcept>
#include <utility>
using namespace corryvreckan;
EventDefinitionRawTrigger::EventDefinitionRawTrigger(Configuration& c, std::vector<std::shared_ptr<Detector>> d)
    : Module(c, std::move(d)) {}
void EventDefinitionRawTrigger::initialize() {
    reader_ = eudaq::Factory<eudaq::FileReader>::MakeUnique(eudaq::str2hash("native"),config_.getPath("file_name",true).string());
    if(!reader_) throw std::runtime_error("cannot open EUDAQ raw");
}
StatusCode EventDefinitionRawTrigger::run(const std::shared_ptr<Clipboard>& clipboard) {
    while(auto event=reader_->GetNextEvent()) {
        if(event->GetDescription()!="ITS3global") continue;
        auto subevents=event->GetSubEvents();
        if(subevents.empty()) throw std::runtime_error("empty global event");
        auto trigger=subevents.front()->GetTriggerN();
        bool found=false; double when=0.;
        for(auto const& sub:subevents) {
            if(sub->GetTriggerN()!=trigger) throw std::runtime_error("unaligned trigger");
            if(sub->GetDescription()=="msensor" && !found) {
                found=true;when=static_cast<double>(sub->GetTimestampBegin())/1000.;
            }
        }
        if(!found) {
            // Setups without an msensor: use the frame time of the first ALPIDE
            // sub-event, read as the public ALPIDE converter reads it (80 MHz ticks).
            for(auto const& sub:subevents) {
                auto raw=std::dynamic_pointer_cast<const eudaq::RawEvent>(sub);
                if(!raw || raw->NumBlocks()==0) continue;
                auto data=raw->GetBlock(0);
                if(data.size()<16 || data[0]!=0xAA || data[1]!=0xAA || data[2]!=0xAA || data[3]!=0xAA) continue;
                uint64_t ticks=0;
                for(int j=0;j<8;++j) ticks|=static_cast<uint64_t>(data[8+j])<<(j*8);
                found=true;when=static_cast<double>(ticks*12500)/1000.;
                break;
            }
        }
        if(!found) throw std::runtime_error("missing msensor or ALPIDE timestamp");
        auto frame=std::make_shared<Event>(when-0.1,when+0.1);
        frame->addTrigger(trigger,when);clipboard->putEvent(frame);
        return StatusCode::Success;
    }
    return StatusCode::EndRun;
}
