// SPDX-License-Identifier: LGPL-3.0-or-later
#include "eudaq/StdEventConverter.hh"
#include "eudaq/RawEvent.hh"
#include <nlohmann/json.hpp>
#include <atomic>
#include <set>
#include <string>

class MSensorConverter : public eudaq::StdEventConverter {
public:
  bool Converting(eudaq::EventSPC in, eudaq::StdEventSP out, eudaq::ConfigSPC config) const override {
    try {
      auto raw = std::dynamic_pointer_cast<const eudaq::RawEvent>(in);
      if (!raw) return false;
      auto bytes = raw->GetBlock(0);
      const std::string magic("MSENSOR\1",8);
      if(bytes.size()<12 || std::string(bytes.begin(),bytes.begin()+8)!=magic) return false;
      uint32_t size=0; for(int i=8;i<12;i++) size=(size<<8)|bytes[i];
      if(size!=bytes.size()-12) return false;
      auto data=nlohmann::json::parse(bytes.begin()+12,bytes.end());
      const int offset=config?config->Get("msensor_window_offset",0):0;
      const int span=config?config->Get("msensor_window_span",1):1;
      if(span<=0) return false;
      std::set<std::pair<int,int>> hits;
      auto frames=data.at("frames");
      if(data.at("window_index").is_null()) {
        if(offset!=0 || span!=1 || frames.size()!=1) return false;
        for(auto hit: frames.at(0).at("hits")) hits.emplace(hit.at(0).get<int>(),hit.at(1).get<int>());
      } else {
        long base=data.at("window_index").get<long>();
        for(int i=0;i<span;i++) {
          long wanted=base+offset+i; bool found=false;
          for(auto frame: frames) if(frame.at("window_index").get<long>()==wanted) {
            found=true; for(auto hit: frame.at("hits")) hits.emplace(hit.at(0).get<int>(),hit.at(1).get<int>());
          }
          if(!found) { static std::atomic_flag warned=ATOMIC_FLAG_INIT; if(!warned.test_and_set()) EUDAQ_WARN("msensor requested window unavailable"); return false; }
        }
      }
      eudaq::StandardPlane plane(raw->GetDeviceN(),"ITS3DAQ","MSENSOR");
      plane.SetSizeZS(data.at("columns").get<unsigned>(),data.at("rows").get<unsigned>(),0,1);
      for(auto hit:hits) plane.PushPixel(hit.first,hit.second,1);
      out->SetTriggerN(data.at("trigger_number").get<uint32_t>()); out->AddPlane(plane); return true;
    } catch(const std::exception& e) { EUDAQ_WARN(std::string("msensor conversion failed: ")+e.what()); return false; }
  }
};
namespace { auto registered=eudaq::Factory<eudaq::StdEventConverter>::Register<MSensorConverter>(eudaq::cstr2hash("msensor")); }
