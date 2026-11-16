// SPDX-License-Identifier: LGPL-3.0-or-later
// Test-only: read public native events and run the installed StdEventConverter.
#include "eudaq/FileReader.hh"
#include "eudaq/StdEventConverter.hh"
#include <dlfcn.h>
#include <iostream>
int main(int argc,char** argv) {
  if(argc<2 || argc>3) return 2;
  if(!dlopen("/opt/eudaq/lib/libeudaq_module_msensor.so", RTLD_NOW|RTLD_GLOBAL)) { std::cerr<<dlerror(); return 3; }
  if(!dlopen("/opt/eudaq/lib/libeudaq_module_ITS3.so", RTLD_NOW|RTLD_GLOBAL)) { std::cerr<<dlerror(); return 3; }
  auto reader=eudaq::FileReader::Make("native",argv[1]); size_t events=0, hits=0, rejected=0;
  auto config=std::make_shared<eudaq::Configuration>(); if(argc==3) config->Set("msensor_window_offset",std::stoi(argv[2]));
  while(auto ev=reader->GetNextEvent()) {
    if(ev->GetDescription()!="msensor" && ev->GetDescription()!="ITS3global") continue;
    auto standard=eudaq::StandardEvent::MakeShared();
    if(!eudaq::StdEventConverter::Convert(ev,standard,config) || standard->NumPlanes()!=1) { if(argc==2) return 4; ++rejected; continue; }
    ++events; hits+=standard->GetPlane(0).HitPixels();
  }
  std::cout<<events<<" "<<hits; if(argc==3) std::cout<<" "<<rejected; std::cout<<"\n";
}
