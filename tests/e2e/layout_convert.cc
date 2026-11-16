// SPDX-License-Identifier: LGPL-3.0-or-later
#include "eudaq/FileReader.hh"
#include "eudaq/StdEventConverter.hh"
#include <dlfcn.h>
#include <iostream>
int main(int argc, char**argv) {
 if(argc!=2 || !dlopen("/opt/eudaq/lib/libeudaq_module_ITS3.so",RTLD_NOW|RTLD_GLOBAL) || !dlopen("/opt/eudaq/lib/libeudaq_module_msensor.so",RTLD_NOW|RTLD_GLOBAL)) return 2;
 auto reader=eudaq::FileReader::Make("native",argv[1]);
 while(auto ev=reader->GetNextEvent()) if(ev->GetDescription()=="ITS3global") {
  for(auto sub:ev->GetSubEvents()) {
   auto out=eudaq::StandardEvent::MakeShared();
   if(!eudaq::StdEventConverter::Convert(sub,out,nullptr) || out->NumPlanes()!=1) return 3;
   auto &p=out->GetPlane(0); if(p.ID()!=sub->GetDeviceN()) return 4;
   double sum=0; int lo=-1, hi=-1; for(unsigned i=0;i<p.HitPixels();i++) { int col=p.GetX(i); sum+=col; if(lo<0 || col<lo) lo=col; if(col>hi) hi=col; }
   std::cout<<sub->GetDescription()<<" "<<sub->GetDeviceN()<<" "<<sub->GetTriggerN()<<" "<<p.HitPixels()<<" "<<sum<<" "<<lo<<" "<<hi<<"\n";
  }
 }
}
