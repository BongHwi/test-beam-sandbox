// SPDX-License-Identifier: LGPL-3.0-or-later
#include <algorithm>
#include <chrono>
#include <cstdint>
#include <cstring>
#include <fcntl.h>
#include <arpa/inet.h>
#include <cerrno>
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
      ("listen-port",bpo::value<unsigned>()->required(),"TCP listen port")
      ("fem-id",bpo::value<unsigned>()->required(),"Plane")
      ("drain-redis-uri",bpo::value<std::string>()->default_value("tcp://127.0.0.1:6379/0"),"Redis URI");
}
class MSensorStreamServer : public FairMQDevice {
    std::string channel, control, member, state="idle", reason;
    unsigned fem=0, port=0; long long run=0, last=-1; uint64_t frames=0, bytes=0, missing=0, connections=0;
    bool ended=false, connected=false; int sock=-1, listener=-1, datafd=-1, delay=0; Bytes first_header; sandbox::MsrawFramer framer;
    std::unique_ptr<sandbox::DrainClient> drain; std::unique_ptr<sw::redis::Redis> redis;
    uint64_t dqm_dropped=0;
    Clock::time_point next_status{};
    void close_socket() { if(sock>=0) ::close(sock); sock=-1; }
    void close_data() { if(datafd>=0) ::close(datafd); datafd=-1; connected=false; }
    void close_listener() { close_data(); if(listener>=0) ::close(listener); listener=-1; }
    void publish() {
        try {
            if (!redis) return;
            // property_tree quotes scalar values, so construct typed JSON explicitly.
            auto quote=[](const std::string& s){ std::ostringstream o; o << '"'; for(unsigned char c:s) { if(c=='"'||c=='\\') o << '\\' << c; else if(c<32) o << ' '; else o << c; } o << '"'; return o.str(); };
            std::ostringstream out; out << "{\"member\":" << quote(member) << ",\"fem_id\":" << fem << ",\"input\":\"socket\",\"run\":" << run << ",\"state\":" << quote(state) << ",\"reason\":" << quote(reason) << ",\"frames_sent\":" << frames << ",\"bytes_sent\":" << bytes << ",\"last_frame\":" << last << ",\"end_sent\":" << (ended?"true":"false") << ",\"frames_missing\":" << missing << ",\"connections\":" << connections << ",\"connected\":" << (connected?"true":"false") << ",\"dqm_dropped\":" << dqm_dropped << "}";
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
    void open_listener() {
        listener=::socket(AF_INET,SOCK_STREAM,0); if(listener<0) throw std::runtime_error("listen failed");
        int one=1; setsockopt(listener,SOL_SOCKET,SO_REUSEADDR,&one,sizeof(one));
        if(delay) { int size=4096; setsockopt(listener,SOL_SOCKET,SO_RCVBUF,&size,sizeof(size)); }
        sockaddr_in addr{}; addr.sin_family=AF_INET; addr.sin_addr.s_addr=htonl(INADDR_LOOPBACK); addr.sin_port=htons(port);
        if(fcntl(listener,F_SETFL,O_NONBLOCK)<0 || ::bind(listener,reinterpret_cast<sockaddr*>(&addr),sizeof(addr))<0 || ::listen(listener,16)<0) throw std::runtime_error("listen failed");
    }
    bool receive(bool limited) {
        if(ended) return false;
        bool work=false;
        if(datafd<0) { datafd=::accept(listener,nullptr,nullptr); if(datafd>=0) { fcntl(datafd,F_SETFL,O_NONBLOCK); framer.reset(); ++connections; connected=true; work=true; } }
        if(datafd<0) return work;
        if(limited && delay) std::this_thread::sleep_for(std::chrono::milliseconds(delay));
        unsigned char chunk[65536];
        while(true) {
            auto n=::recv(datafd,chunk,limited && delay?4096:sizeof(chunk),0);
            if(n>0) { framer.feed(chunk,n); work=true; }
            else if(n==0 || (errno!=EAGAIN && errno!=EWOULDBLOCK && errno!=EINTR)) { close_data(); framer.reset(); return work; }
            else break;
            sandbox::MsrawUnit unit;
            while(framer.next(unit)) {
                if(unit.type==1) { if(first_header.empty()) { first_header=unit.data; if(!send_payload(unit.data,1,0)) throw std::runtime_error("send failed"); } else if(first_header!=unit.data) throw std::runtime_error("corrupt data: header changed"); }
                else if(unit.type==0) { if(last>=0 && unit.number<=uint64_t(last)) throw std::runtime_error("corrupt data: frame order"); missing+=unit.number-uint64_t(last+1); if(!send_payload(unit.data,0,uint32_t(unit.number))) throw std::runtime_error("send failed"); ++frames; last=static_cast<long long>(unit.number); }
                else { if(unit.number<uint64_t(last+1)) throw std::runtime_error("corrupt data: frame order"); missing+=unit.number-uint64_t(last+1); if(!send_payload(unit.data,0,uint32_t(frames))) throw std::runtime_error("send failed"); ended=true; return true; }
            }
            if(limited && delay) break;
        }
        return work;
    }
    void drain_run(const std::string& id) {
        try {
            std::string command="{\"op\":\"drain\"}\n"; size_t sent=0; std::string line; bool reply=false; uint64_t expected=0; auto deadline=Clock::now()+std::chrono::seconds(20);
            while(!reply && Clock::now()<deadline) {
                receive(false);
                pollfd p{sock,short(sent<command.size()?POLLOUT:POLLIN),0}; int rc=::poll(&p,1,10);
                if(rc<0 || (rc>0 && (p.revents&(POLLERR|POLLHUP|POLLNVAL)))) throw std::runtime_error("runner lost");
                if(rc>0 && sent<command.size() && (p.revents&POLLOUT)) { auto n=::send(sock,command.data()+sent,command.size()-sent,MSG_NOSIGNAL|MSG_DONTWAIT); if(n<=0) throw std::runtime_error("runner lost"); sent+=n; }
                else if(rc>0 && (p.revents&POLLIN)) { char c; auto n=::recv(sock,&c,1,MSG_DONTWAIT); if(n!=1) throw std::runtime_error("runner lost"); if(c=='\n') { boost::property_tree::ptree r; std::istringstream in(line); boost::property_tree::read_json(in,r); if(!r.get<bool>("ok",false)) throw std::runtime_error(r.get<std::string>("error","drain failed")); if(!r.get<bool>("end_sent",false)) throw std::runtime_error("end not received"); expected=r.get<uint64_t>("frames"); reply=true; } else { line+=c; if(line.size()>1048576) throw std::runtime_error("runner lost"); } }
            }
            if(!reply) throw std::runtime_error("drain timeout");
            auto end=Clock::now()+std::chrono::seconds(5); while(!ended && Clock::now()<end) { receive(false); std::this_thread::sleep_for(std::chrono::milliseconds(5)); }
            if(!ended) throw std::runtime_error("end not received"); if(frames+missing!=expected) throw std::runtime_error("frame count"); drain->Done(id); setstate("drained");
        } catch(const std::exception& e) { std::string error=e.what(); if(error=="drain timeout" || error=="runner lost") close_socket(); throw std::runtime_error(std::string("drain failed: ")+error); }
    }
    void InitTask() override { try { channel=fConfig->GetProperty<std::string>("out-chan-name"); control=fConfig->GetProperty<std::string>("runner-control"); port=fConfig->GetProperty<unsigned>("listen-port"); fem=fConfig->GetProperty<unsigned>("fem-id"); member=fConfig->GetProperty<std::string>("service-name")+":"+fConfig->GetProperty<std::string>("id"); auto uri=fConfig->GetProperty<std::string>("drain-redis-uri"); redis=std::make_unique<sw::redis::Redis>(uri); drain=std::make_unique<sandbox::DrainClient>(uri,member); drain->Register(); connect_runner(); setstate("idle"); } catch(const std::exception& e) { state="failed"; reason=e.what(); LOG(error) << reason; publish(); } }
    void PreRun() override { dqm_dropped=0; try { if(!drain) throw std::runtime_error("drain unavailable"); setstate("starting"); close_listener(); run=0; last=-1; frames=bytes=missing=connections=0; ended=connected=false; delay=0; first_header.clear(); framer.reset(); drain->Arm(); try { run=std::stoll(fConfig->GetProperty<std::string>("run_number")); } catch(...) { throw std::runtime_error("run number unavailable"); } if(run<=0) throw std::runtime_error("run number unavailable"); try { auto value=redis->get("sandbox:test:recv_delay_ms").value_or(""); size_t used=0; int n=std::stoi(value,&used); if(used==value.size() && n>=1 && n<=5000) delay=n; } catch(...) {} open_listener(); if(sock>=0 && lost()) close_socket(); if(!connect_runner()) throw std::runtime_error("runner unavailable"); auto r=request("{\"op\":\"start\",\"run\":"+std::to_string(run)+"}",20,"start timeout"); if(!r.get<bool>("ok",false)) throw std::runtime_error(r.get<std::string>("error","start failed")); setstate("running"); } catch(const std::exception& e) { setstate("failed",e.what()); } }
    bool ConditionalRun() override { try { if(state=="failed") return false; if(state=="drained") { std::this_thread::sleep_for(std::chrono::milliseconds(10)); return true; } if(lost()) { close_socket(); throw std::runtime_error("runner lost"); } if(auto id=drain->Poll()) drain_run(*id); else { bool work=receive(true); if(!work) std::this_thread::sleep_for(std::chrono::milliseconds(5)); } if(Clock::now()>=next_status) publish(); return true; } catch(const std::exception& e) { setstate("failed",e.what()); return false; } }
    void PostRun() override { try { if(state!="drained") { if(sock>=0 && !lost()) try { request("{\"op\":\"abort\"}",5,"abort timeout"); } catch(...) {} if(state!="failed") setstate("stopped","stopped without drain"); } close_listener(); publish(); } catch(const std::exception& e) { setstate("failed",e.what()); } }
    void ResetTask() override { close_socket(); close_listener(); if(drain) drain->Unregister(); }
};
FairMQDevicePtr getDevice(const fair::mq::ProgOptions&) { return new MSensorStreamServer(); }
