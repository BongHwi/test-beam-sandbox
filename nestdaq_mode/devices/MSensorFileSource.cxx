// SPDX-License-Identifier: LGPL-3.0-or-later
#include <algorithm>
#include <chrono>
#include <cstdint>
#include <cstring>
#include <fcntl.h>
#include <memory>
#include <poll.h>
#include <sstream>
#include <stdexcept>
#include <string>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <thread>
#include <unistd.h>
#include <vector>
#include <boost/crc.hpp>
#include <boost/property_tree/json_parser.hpp>
#include <fairmq/Device.h>
#include <fairmq/runFairMQDevice.h>
#include "drain_client.h"
#include "msraw_framer.h"
namespace bpo = boost::program_options;
using Clock = std::chrono::steady_clock;
using Bytes = std::vector<unsigned char>;
static void put(Bytes& b, uint64_t n, int width) { for(int i=0;i<width;++i) b.push_back(static_cast<unsigned char>(n>>(i*8))); }
void addCustomOptions(bpo::options_description& o) {
    o.add_options()("out-chan-name",bpo::value<std::string>()->default_value("out"),"Output channel")
      ("runner-control",bpo::value<std::string>()->required(),"Runner control socket")
      ("runner-data",bpo::value<std::string>()->required(),"Runner data path")
      ("fem-id",bpo::value<unsigned>()->required(),"Plane")
      ("drain-redis-uri",bpo::value<std::string>()->default_value("tcp://127.0.0.1:6379/0"),"Redis URI");
}
class MSensorFileSource : public FairMQDevice {
    std::string channel, control, pattern, member, state="idle", reason;
    unsigned fem=0; long long run=0, last=-1; uint64_t frames=0, bytes=0, readpos=0;
    bool ended=false; int sock=-1, file=-1; sandbox::MsrawFramer framer;
    std::unique_ptr<sandbox::DrainClient> drain; std::unique_ptr<sw::redis::Redis> redis;
    uint64_t dqm_dropped=0;
    Clock::time_point next_status{};
    void close_socket() { if(sock>=0) ::close(sock); sock=-1; }
    void close_file() { if(file>=0) ::close(file); file=-1; }
    void publish() {
        try {
            if (!redis) return;
            // property_tree quotes scalar values, so construct typed JSON explicitly.
            auto quote=[](const std::string& s){ std::ostringstream o; o << '"'; for(unsigned char c:s) { if(c=='"'||c=='\\') o << '\\' << c; else if(c<32) o << ' '; else o << c; } o << '"'; return o.str(); };
            std::ostringstream out; out << "{\"member\":" << quote(member) << ",\"fem_id\":" << fem << ",\"input\":\"file\",\"run\":" << run << ",\"state\":" << quote(state) << ",\"reason\":" << quote(reason) << ",\"frames_sent\":" << frames << ",\"bytes_sent\":" << bytes << ",\"last_frame\":" << last << ",\"end_sent\":" << (ended?"true":"false") << ",\"dqm_dropped\":" << dqm_dropped << "}";
            redis->set("sandbox:source:"+member,out.str());
        } catch(const std::exception& e) { LOG(error) << "status: " << e.what(); }
        next_status=Clock::now()+std::chrono::milliseconds(200);
    }
    void setstate(const std::string& s,const std::string& why="") { state=s; reason=why; publish(); }
    bool connect_runner() {
        if(sock>=0) return true;
        auto until=Clock::now()+std::chrono::seconds(5);
        do {
            int fd=::socket(AF_UNIX,SOCK_STREAM,0); if(fd<0) throw std::runtime_error("runner unavailable");
            sockaddr_un addr{}; addr.sun_family=AF_UNIX;
            if(control.size()>=sizeof(addr.sun_path)) { ::close(fd); return false; }
            std::strcpy(addr.sun_path,control.c_str());
            if(::connect(fd,reinterpret_cast<sockaddr*>(&addr),sizeof(addr))==0) { sock=fd; return true; }
            ::close(fd); std::this_thread::sleep_for(std::chrono::milliseconds(100));
        } while(Clock::now()<until);
        return false;
    }
    bool lost() { if(sock<0) return true; pollfd p{sock,POLLIN|POLLHUP|POLLERR,0}; if(::poll(&p,1,0)>0) { char c; return ::recv(sock,&c,1,MSG_PEEK|MSG_DONTWAIT)<=0; } return false; }
    boost::property_tree::ptree request(const std::string& s,int seconds,const std::string& timeout) {
        std::string wire=s+"\n"; size_t pos=0;
        while(pos<wire.size()) { auto n=::send(sock,wire.data()+pos,wire.size()-pos,MSG_NOSIGNAL); if(n<=0) { close_socket(); throw std::runtime_error("runner lost"); } pos+=n; }
        auto deadline=Clock::now()+std::chrono::seconds(seconds); std::string line;
        while(true) {
            auto ms=std::chrono::duration_cast<std::chrono::milliseconds>(deadline-Clock::now()).count();
            if(ms<=0) { close_socket(); throw std::runtime_error(timeout); }
            pollfd p{sock,POLLIN,0}; int rc=::poll(&p,1,int(ms));
            if(rc==0) { close_socket(); throw std::runtime_error(timeout); }
            char c; if(rc<0 || ::recv(sock,&c,1,0)!=1) { close_socket(); throw std::runtime_error("runner lost"); }
            if(c=='\n') break; line+=c; if(line.size()>1048576) { close_socket(); throw std::runtime_error("runner lost"); }
        }
        boost::property_tree::ptree result; std::istringstream in(line); boost::property_tree::read_json(in,result); return result;
    }
    bool send_payload(const Bytes& data,uint16_t type,uint32_t id) {
        Bytes head; put(head,0x00454d4954425553ULL,8); put(head,48+data.size(),4); put(head,48,2); put(head,type,2); put(head,id,4); put(head,0x4d53,4); put(head,fem,4); put(head,1,4); put(head,0,8); put(head,0,8);
        auto msg=NewMessage(head.size()+data.size()); std::memcpy(msg->GetData(),head.data(),head.size()); std::memcpy(static_cast<unsigned char*>(msg->GetData())+head.size(),data.data(),data.size());
        fair::mq::MessagePtr copy;
        if(fChannels.count("dqm")) { copy=NewMessage(head.size()+data.size()); std::memcpy(copy->GetData(),msg->GetData(),head.size()+data.size()); }
        if(Send(msg,channel)<0) return false;
        if(fChannels.count("dqm") && Send(copy,"dqm",0,0)<0) ++dqm_dropped;
        bytes+=data.size(); return true;
    }
    bool read_file() {
        struct stat st{}; if(fstat(file,&st)<0) throw std::runtime_error("file missing");
        if(uint64_t(st.st_size)<readpos) throw std::runtime_error("file truncated");
        bool work=false; unsigned char chunk[65536];
        while(readpos<uint64_t(st.st_size)) { auto n=pread(file,chunk,std::min<uint64_t>(sizeof(chunk),uint64_t(st.st_size)-readpos),readpos); if(n<=0) break; framer.feed(chunk,n); readpos+=n; work=true; }
        sandbox::MsrawUnit unit;
        while(!ended && framer.next(unit)) {
            if(!send_payload(unit.data,unit.type==1?1:0,unit.type==0?uint32_t(unit.number):unit.type==2?uint32_t(frames):0)) throw std::runtime_error("send failed");
            if(unit.type==0) { ++frames; last=static_cast<long long>(unit.number); } else if(unit.type==2) ended=true;
        }
        return work;
    }
    void InitTask() override { try { channel=fConfig->GetProperty<std::string>("out-chan-name"); control=fConfig->GetProperty<std::string>("runner-control"); pattern=fConfig->GetProperty<std::string>("runner-data"); fem=fConfig->GetProperty<unsigned>("fem-id"); member=fConfig->GetProperty<std::string>("service-name")+":"+fConfig->GetProperty<std::string>("id"); auto uri=fConfig->GetProperty<std::string>("drain-redis-uri"); redis=std::make_unique<sw::redis::Redis>(uri); drain=std::make_unique<sandbox::DrainClient>(uri,member); drain->Register(); connect_runner(); setstate("idle"); } catch(const std::exception& e) { state="failed"; reason=e.what(); LOG(error) << reason; publish(); } }
    void PreRun() override { dqm_dropped=0; try { if(!drain) throw std::runtime_error("drain unavailable"); setstate("starting"); close_file(); run=0; last=-1; frames=bytes=readpos=0; ended=false; framer.reset(); drain->Arm(); try { run=std::stoll(fConfig->GetProperty<std::string>("run_number")); } catch(...) { throw std::runtime_error("run number unavailable"); } if(run<=0) throw std::runtime_error("run number unavailable"); if(sock>=0 && lost()) close_socket(); if(!connect_runner()) throw std::runtime_error("runner unavailable"); auto r=request("{\"op\":\"start\",\"run\":"+std::to_string(run)+"}",20,"start timeout"); if(!r.get<bool>("ok",false)) throw std::runtime_error(r.get<std::string>("error","start failed")); auto path=pattern; auto at=path.find("{run}"); if(at!=std::string::npos) path.replace(at,5,std::to_string(run)); auto end=Clock::now()+std::chrono::seconds(2); do { file=::open(path.c_str(),O_RDONLY); if(file>=0) break; std::this_thread::sleep_for(std::chrono::milliseconds(20)); } while(Clock::now()<end); if(file<0) throw std::runtime_error("file missing"); setstate("running"); } catch(const std::exception& e) { setstate("failed",e.what()); } }
    bool ConditionalRun() override { try { if(state=="failed") return false; if(state=="drained") { std::this_thread::sleep_for(std::chrono::milliseconds(10)); return true; } if(lost()) { close_socket(); throw std::runtime_error("runner lost"); } bool work=read_file(); if(auto id=drain->Poll()) { try { auto r=request("{\"op\":\"drain\"}",20,"drain timeout"); if(!r.get<bool>("ok",false)) throw std::runtime_error(r.get<std::string>("error","drain failed")); auto target=r.get<uint64_t>("end_bytes"); auto expected=r.get<uint64_t>("frames"); auto end=Clock::now()+std::chrono::seconds(5); while((readpos<target || !ended) && Clock::now()<end) { read_file(); std::this_thread::sleep_for(std::chrono::milliseconds(5)); } if(readpos<target || !ended) throw std::runtime_error("file incomplete"); if(frames!=expected) throw std::runtime_error("frame count"); drain->Done(*id); setstate("drained"); } catch(const std::exception& e) { throw std::runtime_error(std::string("drain failed: ")+e.what()); } } if(Clock::now()>=next_status) publish(); if(!work) std::this_thread::sleep_for(std::chrono::milliseconds(5)); return true; } catch(const std::exception& e) { setstate("failed",e.what()); return false; } }
    void PostRun() override { try { if(state!="drained") { if(sock>=0 && !lost()) try { request("{\"op\":\"abort\"}",5,"abort timeout"); } catch(...) {} if(state!="failed") setstate("stopped","stopped without drain"); } publish(); close_file(); } catch(const std::exception& e) { setstate("failed",e.what()); } }
    void ResetTask() override { close_socket(); close_file(); if(drain) drain->Unregister(); }
};
FairMQDevicePtr getDevice(const fair::mq::ProgOptions&) { return new MSensorFileSource(); }
