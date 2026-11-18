// SPDX-License-Identifier: LGPL-3.0-or-later
#pragma once
#include <boost/crc.hpp>
#include <cstdint>
#include <cstring>
#include <stdexcept>
#include <string>
#include <vector>
namespace sandbox {
struct MsrawUnit { int type; uint64_t number; std::vector<unsigned char> data; };
class MsrawFramer {
    std::vector<unsigned char> buf, group;
    size_t pos=0; uint64_t base=0; bool meta=false, end=false;
    static uint32_t u32(const unsigned char* p) { uint32_t n=0; for(int i=0;i<4;++i) n|=uint32_t(p[i])<<(8*i); return n; }
    static uint64_t u64(const unsigned char* p) { uint64_t n=0; for(int i=0;i<8;++i) n|=uint64_t(p[i])<<(8*i); return n; }
    void compact() { if(pos && (pos>1048576 || pos==buf.size())) { buf.erase(buf.begin(),buf.begin()+pos); base+=pos; pos=0; } }
    [[noreturn]] void corrupt() { throw std::runtime_error("corrupt data at "+std::to_string(base+pos)); }
public:
    void feed(const unsigned char* p,size_t n) { buf.insert(buf.end(),p,p+n); }
    void reset() { buf.clear(); group.clear(); pos=0; base=0; meta=end=false; }
    bool next(MsrawUnit& out) {
        compact();
        if(end) return false;
        if(!meta) {
            if(buf.size()-pos<64) return false;
            if(std::memcmp(buf.data()+pos,"MSRAWV01",8)!=0) corrupt();
            uint64_t n=64ULL+u32(buf.data()+pos+16); if(buf.size()-pos<n) return false;
            out={1,0,std::vector<unsigned char>(buf.begin()+pos,buf.begin()+pos+n)}; pos+=n; meta=true; return true;
        }
        while(true) {
            compact(); if(buf.size()-pos<5) return false;
            auto kind=buf[pos]; auto len=u32(buf.data()+pos+1);
            if(kind<1 || kind>5 || len>16777216) corrupt();
            size_t n=size_t(len)+9; if(buf.size()-pos<n) return false;
            boost::crc_32_type crc; crc.process_bytes(buf.data()+pos,n-4);
            if(crc.checksum()!=u32(buf.data()+pos+n-4) || (kind==3 && len<8) || (kind==5 && len<56)) corrupt();
            uint64_t number=kind==3?u64(buf.data()+pos+5):kind==5?u64(buf.data()+pos+5+48):0;
            group.insert(group.end(),buf.begin()+pos,buf.begin()+pos+n); pos+=n;
            if(kind==3 || kind==5) { out={kind==3?0:2,number,std::move(group)}; group.clear(); if(kind==5) end=true; return true; }
        }
    }
};
}
