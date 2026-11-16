// SPDX-License-Identifier: LGPL-3.0-or-later
#include "eudaq/FileReader.hh"
#include "eudaq/StdEventConverter.hh"
#include <dlfcn.h>
#include <iostream>
int main(int argc,char**argv){
 if(argc!=2 || !dlopen("/opt/eudaq/lib/libeudaq_module_ITS3.so",RTLD_NOW|RTLD_GLOBAL)) return 2;
 auto reader=eudaq::FileReader::Make("native",argv[1]);
 while(auto ev=reader->GetNextEvent()){
  for(auto sub:ev->GetSubEvents()){
   if(sub->GetDescription().find("ALPIDE_plane_")!=0)continue;
   auto out=eudaq::StandardEvent::MakeShared();
   if(!eudaq::StdEventConverter::Convert(sub,out,nullptr) || out->NumPlanes()!=1)return 3;
   auto &p=out->GetPlane(0);std::cout<<sub->GetDescription()<<" "<<sub->GetTriggerN()<<" "<<sub->GetTimestampBegin()<<" "<<p.HitPixels();
   for(unsigned i=0;i<p.HitPixels();i++)std::cout<<" "<<unsigned(p.GetX(i))<<","<<unsigned(p.GetY(i))<<","<<p.GetTimestamp(i);
   std::cout<<"\n";
  }
 }
}
