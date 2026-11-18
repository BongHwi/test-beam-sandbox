// SPDX-License-Identifier: LGPL-3.0-or-later
#include <algorithm>
#include <atomic>
#include <chrono>
#include <cstdint>
#include <cstring>
#include <deque>
#include <map>
#include <limits>
#include <memory>
#include <mutex>
#include <sstream>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>
#include <boost/property_tree/json_parser.hpp>
#include <fairmq/Device.h>
#include <fairmq/runFairMQDevice.h>
#include <sw/redis++/redis++.h>
#include <TCanvas.h>
#include <TGraph.h>
#include <TH1.h>
#include <TH2.h>
#include <THttpServer.h>
#include <TLine.h>
#include <TList.h>
#include <TROOT.h>
#include <TPad.h>

namespace bpo = boost::program_options;
void addCustomOptions(bpo::options_description& o) {
    o.add_options()("metrics-redis-uri",bpo::value<std::string>()->default_value("tcp://127.0.0.1:6379/1"),"Redis TimeSeries URI")("reference-fem",bpo::value<int>()->default_value(-1),"Reference fem ID");
}
namespace {
uint64_t num(const unsigned char* p, int n) { uint64_t x=0; for(int i=0;i<n;++i) x|=uint64_t(p[i])<<(8*i); return x; }
struct DqmData {
    std::string name; unsigned cols, rows, tx, ty, tc, tr;
    std::unique_ptr<TH2I> hitmap, tiles;
    std::unique_ptr<TH1I> hits, triggers;
    std::unique_ptr<TCanvas> canvas;
    std::vector<std::unique_ptr<TLine>> lines;
    uint64_t frame_hits=0, frame_triggers=0, total=0; long long timestamp=0;
};
struct Hit { unsigned col, row; };
struct Trigger { uint64_t n, time; std::vector<Hit> hits; };
struct Pair {
    std::deque<std::shared_ptr<Trigger>> ref_q, dev_q;
    bool locked=false; int64_t last_dt=0; uint64_t last_t=0;
    uint64_t since_ref=0, since_dev=0;
    uint64_t matched=0, only_ref=0, only_dev=0, skipped=0;
    uint64_t n=0; double x0=0, sx=0, sy=0, sxx=0, sxy=0;
    std::unique_ptr<TH2I> col, row;
    std::unique_ptr<TH1I> dcol, drow, match, lost;
    std::unique_ptr<TGraph> dt, dn;
};
}
class MSensorDqm : public FairMQDevice {
    std::mutex mutex; std::atomic<bool> alive{true}; std::thread http;
    THttpServer* server=nullptr; std::map<unsigned,DqmData> devices;
    std::unique_ptr<sw::redis::Redis> redis; uint64_t received=0, discarded=0;
    int reference=-1; std::map<unsigned,Pair> pairs;
    std::unique_ptr<TH1D> summary_dcol, summary_drow, summary_drift, summary_offset;
    std::unique_ptr<TH2I> summary_counts;
    std::unique_ptr<TCanvas> summary;
    std::chrono::steady_clock::time_point last_summary{};
    static double peak(const TH1I& h) {
        int k=1; for(int i=2;i<=h.GetNbinsX();++i) if(h.GetBinContent(i)>h.GetBinContent(k)) k=i;
        double sum=0, weighted=0;
        for(int i=std::max(1,k-3);i<=std::min(h.GetNbinsX(),k+3);++i) { double v=h.GetBinContent(i); sum+=v; weighted+=v*h.GetBinCenter(i); }
        return sum ? weighted/sum : 0;
    }
    void update_summary() {
        int count=pairs.size(); if(!count) return;
        if(!summary) {
            const auto& ref=devices.at(reference).name;
            auto make=[&](const char* name,const std::string& title) { return std::make_unique<TH1D>(name,title.c_str(),count,0,count); };
            summary_dcol=make("summary_dcol","column offset of each msensor from "+ref+" (pixels)");
            summary_drow=make("summary_drow","row offset of each msensor from "+ref+" (pixels)");
            summary_drift=make("summary_drift","trigger clock drift of each msensor against "+ref+" (ppm)");
            summary_offset=make("summary_offset","trigger time offset of each msensor from "+ref+" (us)");
            summary_counts=std::make_unique<TH2I>("summary_counts",("trigger pairs of each msensor with "+ref).c_str(),count,0,count,4,0,4);
            for(auto* h:{summary_dcol.get(),summary_drow.get(),summary_drift.get(),summary_offset.get()}) { h->SetStats(kFALSE); server->Register("/correlation",h); }
            summary_counts->SetStats(kFALSE); server->Register("/correlation",summary_counts.get());
            summary=std::make_unique<TCanvas>("summary",("summary of all msensors against "+ref).c_str(),1200,800);
            gROOT->GetListOfCanvases()->Remove(summary.get()); summary->Divide(2,3);
            summary->cd(1); gPad->SetLeftMargin(0.08); gPad->SetRightMargin(0.03); summary_dcol->Draw("HIST TEXT");
            summary->cd(2); gPad->SetLeftMargin(0.08); gPad->SetRightMargin(0.03); summary_drow->Draw("HIST TEXT");
            summary->cd(3); gPad->SetLeftMargin(0.30); gPad->SetRightMargin(0.12); summary_counts->GetYaxis()->SetLabelSize(0.05); summary_counts->Draw("TEXT COLZ");
            summary->cd(4); gPad->SetLeftMargin(0.08); gPad->SetRightMargin(0.03); summary_drift->Draw("HIST TEXT");
            summary->cd(5); gPad->SetLeftMargin(0.08); gPad->SetRightMargin(0.03); summary_offset->Draw("HIST TEXT");
            server->Register("/correlation",summary.get());
        }
        if(summary_dcol->GetNbinsX()!=count) {
            for(auto* h:{summary_dcol.get(),summary_drow.get(),summary_drift.get(),summary_offset.get()}) h->SetBins(count,0,count);
            summary_counts->SetBins(count,0,count,4,0,4);
        }
        const auto& ref=devices.at(reference).name;
        summary_counts->GetYaxis()->SetBinLabel(1,"matched"); summary_counts->GetYaxis()->SetBinLabel(2,("only in "+ref).c_str());
        summary_counts->GetYaxis()->SetBinLabel(3,"only in the other msensor"); summary_counts->GetYaxis()->SetBinLabel(4,"skipped");
        int i=0; for(auto& [id,p]:pairs) {
            ++i; const auto& name=devices.at(id).name;
            for(auto* h:{summary_dcol.get(),summary_drow.get(),summary_drift.get(),summary_offset.get()}) h->GetXaxis()->SetBinLabel(i,name.c_str());
            summary_counts->GetXaxis()->SetBinLabel(i,name.c_str());
            double den=p.n*p.sxx-p.sx*p.sx; double slope=p.n>=2 && den!=0 ? (p.n*p.sxy-p.sx*p.sy)/den : 0;
            double offset=p.n ? p.sy/p.n-slope*(p.sx/p.n+p.x0) : 0;
            summary_dcol->SetBinContent(i,peak(*p.dcol)); summary_drow->SetBinContent(i,peak(*p.drow));
            summary_drift->SetBinContent(i,slope); summary_offset->SetBinContent(i,offset);
            summary_counts->SetBinContent(i,1,p.matched); summary_counts->SetBinContent(i,2,p.only_ref); summary_counts->SetBinContent(i,3,p.only_dev); summary_counts->SetBinContent(i,4,p.skipped);
        }
        for(auto* h:{summary_dcol.get(),summary_drow.get(),summary_drift.get(),summary_offset.get()}) {
            double lo=std::min(0.,h->GetBinContent(1)), hi=std::max(0.,h->GetBinContent(1));
            for(int b=2;b<=count;++b) { lo=std::min(lo,h->GetBinContent(b)); hi=std::max(hi,h->GetBinContent(b)); }
            double span=std::max(hi-lo,1e-9); h->SetMaximum(hi+span); h->SetMinimum(lo<0 ? lo-0.1*span : 0);
        }
    }
    void make_pairs() {
        if(reference<0 || !devices.count(reference)) return;
        auto& r=devices.at(reference);
        for(auto& [id,d]:devices) {
            if(id==unsigned(reference) || pairs.count(id)) continue;
            Pair p;
            auto make2=[&](const std::string& name,const std::string& title,unsigned nx,unsigned ny) {
                return std::make_unique<TH2I>(name.c_str(),title.c_str(),nx,0,nx,ny,0,ny);
            };
            p.col=make2("col_"+d.name,d.name+" column vs "+r.name+" column",r.cols,d.cols);
            p.row=make2("row_"+d.name,d.name+" row vs "+r.name+" row",r.rows,d.rows);
            auto name="dcol_"+d.name; auto title=d.name+" column minus "+r.name+" column";
            p.dcol=std::make_unique<TH1I>(name.c_str(),title.c_str(),r.cols+d.cols,-double(r.cols)-.5,double(d.cols)-.5);
            name="drow_"+d.name; title=d.name+" row minus "+r.name+" row";
            p.drow=std::make_unique<TH1I>(name.c_str(),title.c_str(),r.rows+d.rows,-double(r.rows)-.5,double(d.rows)-.5);
            p.dt=std::make_unique<TGraph>(); name="trigger_dt_"+d.name; title=d.name+" trigger time minus "+r.name+" (us) vs "+r.name+" trigger time (s)"; p.dt->SetName(name.c_str()); p.dt->SetTitle(title.c_str());
            p.dn=std::make_unique<TGraph>(); name="trigger_dn_"+d.name; title=d.name+" trigger number minus "+r.name+" trigger number vs "+r.name+" trigger time (s)"; p.dn->SetName(name.c_str()); p.dn->SetTitle(title.c_str());
            name="lost_"+d.name; title=d.name+" triggers seen by only one of "+r.name+" and "+d.name;
            p.lost=std::make_unique<TH1I>(name.c_str(),title.c_str(),2,.5,2.5);
            p.lost->GetXaxis()->SetBinLabel(1,("only in "+r.name).c_str()); p.lost->GetXaxis()->SetBinLabel(2,("only in "+d.name).c_str());
            name="match_"+d.name; title=d.name+" triggers paired with "+r.name;
            p.match=std::make_unique<TH1I>(name.c_str(),title.c_str(),3,.5,3.5);
            p.match->GetXaxis()->SetBinLabel(1,"matched"); p.match->GetXaxis()->SetBinLabel(2,"unmatched"); p.match->GetXaxis()->SetBinLabel(3,"skipped");
            server->Register("/correlation",p.col.get()); server->Register("/correlation",p.row.get()); server->Register("/correlation",p.dcol.get()); server->Register("/correlation",p.drow.get()); server->Register("/correlation",p.dt.get()); server->Register("/correlation",p.match.get()); server->Register("/correlation",p.dn.get()); server->Register("/correlation",p.lost.get());
            pairs.emplace(id,std::move(p));
        }
    }
    void only(Pair& p,bool ref) {
        if(ref) { ++p.only_ref; ++p.since_ref; p.lost->Fill(1); }
        else { ++p.only_dev; ++p.since_dev; p.lost->Fill(2); }
        p.match->Fill(2);
    }
    void fill_pair(Pair& p,const Trigger& r,const Trigger& d) {
        ++p.matched;
        double delta=d.time>=r.time ? double(d.time-r.time) : -double(r.time-d.time);
        double t=double(r.time)/1e9, y=delta/1e3; if(!p.n) p.x0=t;
        ++p.n; double x=t-p.x0; p.sx+=x; p.sy+=y; p.sxx+=x*x; p.sxy+=x*y;
        p.match->Fill(1);
        if(p.dt->GetN()<100000 || r.n%100==0) {
            p.dt->SetPoint(p.dt->GetN(),double(r.time)/1e9,delta/1e3);
        }
        if(p.dn->GetN()<100000 || r.n%100==0)
            p.dn->SetPoint(p.dn->GetN(),double(r.time)/1e9,double(int64_t(d.n)-int64_t(r.n)));
        p.since_ref=p.since_dev=0;
        if(!r.hits.empty() && d.hits.size()>10000/r.hits.size()) { ++p.skipped; p.match->Fill(3); return; }
        for(const auto& a:r.hits) for(const auto& b:d.hits) {
            p.col->Fill(a.col,b.col); p.row->Fill(a.row,b.row);
            p.dcol->Fill(int64_t(b.col)-int64_t(a.col)); p.drow->Fill(int64_t(b.row)-int64_t(a.row));
        }
    }
    static int64_t difference(const Trigger& r,const Trigger& d) { return int64_t(d.time)-int64_t(r.time); }
    static int64_t tolerance(uint64_t t,uint64_t last) {
        int64_t delta=int64_t(t)-int64_t(last);
        return 1000+std::max<int64_t>(delta,0)/400;
    }
    bool trial(const Pair& p,size_t a,size_t b) {
        size_t i=a,j=b, count=0;
        int64_t dt=difference(*p.ref_q[a],*p.dev_q[b]); uint64_t last=p.ref_q[a]->time;
        while(i<a+8 && j<p.dev_q.size()) {
            const auto& r=*p.ref_q[i]; const auto& d=*p.dev_q[j];
            int64_t e=difference(r,d)-dt, tol=tolerance(r.time,last);
            if(e>=-tol && e<=tol) { ++count; dt=difference(r,d); last=r.time; ++i; ++j; }
            else if(e < -tol) ++j;
            else ++i;
        }
        return count>=6;
    }
    void track(Pair& p) {
        for(;;) {
            if(!p.locked) {
                if(p.ref_q.size()<8 || p.dev_q.empty()) return;
                size_t a=0,b=0; bool found=false;
                for(;a+8<=p.ref_q.size();++a) if(trial(p,a,0)) { found=true; break; }
                if(!found) { a=0; for(b=1;b<p.dev_q.size();++b) if(trial(p,0,b)) { found=true; break; } }
                if(!found) {
                    if(p.ref_q.size()<264 || p.dev_q.size()<264) return;
                    only(p,true); p.ref_q.pop_front(); only(p,false); p.dev_q.pop_front();
                    continue;
                }
                while(a--) { only(p,true); p.ref_q.pop_front(); }
                while(b--) { only(p,false); p.dev_q.pop_front(); }
                p.last_dt=difference(*p.ref_q.front(),*p.dev_q.front()); p.last_t=p.ref_q.front()->time;
                p.locked=true; p.since_ref=p.since_dev=0;
            }
            while(!p.ref_q.empty() && !p.dev_q.empty()) {
                const auto& r=*p.ref_q.front(); const auto& d=*p.dev_q.front();
                int64_t e=difference(r,d)-p.last_dt, tol=tolerance(r.time,p.last_t);
                if(e>=-tol && e<=tol) {
                    fill_pair(p,r,d); p.last_dt=difference(r,d); p.last_t=r.time;
                    p.ref_q.pop_front(); p.dev_q.pop_front();
                } else if(e < -tol) { only(p,false); p.dev_q.pop_front(); }
                else { only(p,true); p.ref_q.pop_front(); }
                if(p.since_ref>=8 && p.since_dev>=8) { p.locked=false; break; }
            }
            if(p.locked) return;
        }
    }
    void pair_message(Pair& p,bool is_ref,const std::vector<std::shared_ptr<Trigger>>& items) {
        auto& q=is_ref?p.ref_q:p.dev_q;
        for(const auto& t:items) {
            q.push_back(t);
            if(q.size()>100000) { only(p,is_ref); q.pop_front(); }
        }
        track(p);
    }
    void metrics(DqmData& d) {
        auto now=std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::system_clock::now().time_since_epoch()).count();
        d.timestamp=std::max<long long>(now,d.timestamp+1);
        try {
            if(!redis) return;
            redis->command<long long>("TS.ADD", "ts:msensor:"+d.name+":hits_per_frame", d.timestamp, d.frame_hits);
            redis->command<long long>("TS.ADD", "ts:msensor:"+d.name+":triggers_per_frame", d.timestamp, d.frame_triggers);
        } catch(const std::exception& e) { LOG(error) << "metrics: " << e.what(); }
    }
    void metadata(unsigned fem,const unsigned char* p,size_t n) {
        if(n<64 || std::memcmp(p,"MSRAWV01",8) || num(p+8,4)!=64 || num(p+16,4)!=n-64) throw std::runtime_error("bad metadata header");
        boost::property_tree::ptree m; std::istringstream in(std::string(reinterpret_cast<const char*>(p+64),n-64)); boost::property_tree::read_json(in,m);
        DqmData d; d.name=m.get<std::string>("device"); d.cols=m.get<unsigned>("columns"); d.rows=m.get<unsigned>("rows"); d.tx=m.get<unsigned>("tiles_x"); d.ty=m.get<unsigned>("tiles_y"); d.tc=m.get<unsigned>("tile_columns"); d.tr=m.get<unsigned>("tile_rows");
        if(d.name.empty() || !d.cols || !d.rows || !d.tx || !d.ty || !d.tc || !d.tr || uint64_t(d.tx)*d.tc!=d.cols || uint64_t(d.ty)*d.tr!=d.rows) throw std::runtime_error("bad geometry");
        auto found=devices.find(fem);
        if(found!=devices.end()) { auto& a=found->second; if(a.cols!=d.cols || a.rows!=d.rows || a.tx!=d.tx || a.ty!=d.ty || a.tc!=d.tc || a.tr!=d.tr) throw std::runtime_error("geometry changed"); return; }
        std::string off; for(auto& v:m.get_child("tiles_off")) { if(!off.empty()) off+=", "; off+=v.second.get_value<std::string>(); } if(off.empty()) off="none";
        d.hitmap=std::make_unique<TH2I>("hitmap",(d.name+" hit map").c_str(),d.cols,0,d.cols,d.rows,0,d.rows);
        d.tiles=std::make_unique<TH2I>("tile_hits",(d.name+" hits per tile (off: "+off+")").c_str(),d.tx,0,d.tx,d.ty,0,d.ty);
        d.hits=std::make_unique<TH1I>("hits_per_frame","hits per frame",2048,-.5,2047.5);
        d.triggers=std::make_unique<TH1I>("triggers_per_frame","triggers per frame",2048,-.5,2047.5);
        d.canvas=std::make_unique<TCanvas>("hitmap_tiles","hitmap tiles",800,600); gROOT->GetListOfCanvases()->Remove(d.canvas.get());
        d.canvas->cd(); d.hitmap->Draw("COLZ");
        for(unsigned x=1;x<d.tx;++x) { d.lines.emplace_back(std::make_unique<TLine>(x*d.tc,0,x*d.tc,d.rows)); d.lines.back()->Draw(); }
        for(unsigned y=1;y<d.ty;++y) { d.lines.emplace_back(std::make_unique<TLine>(0,y*d.tr,d.cols,y*d.tr)); d.lines.back()->Draw(); }
        auto folder="/"+d.name; server->Register(folder.c_str(),d.hitmap.get()); server->Register(folder.c_str(),d.tiles.get()); server->Register(folder.c_str(),d.hits.get()); server->Register(folder.c_str(),d.triggers.get()); server->Register(folder.c_str(),d.canvas.get());
        devices.emplace(fem,std::move(d));
        make_pairs();
    }
    void process(const void* data,size_t size) noexcept {
        ++received;
        try {
            auto p=static_cast<const unsigned char*>(data);
            if(size<48 || num(p,8)!=0x00454d4954425553ULL || num(p+8,4)!=size || num(p+12,2)!=48 || num(p+20,4)!=0x4d53 || num(p+28,4)!=1) throw std::runtime_error("bad subtimeframe");
            auto type=num(p+14,2), fem=num(p+24,4); p+=48; size-=48;
            std::lock_guard<std::mutex> lock(mutex);
            if(type==1) { metadata(fem,p,size); return; }
            if(type!=0 || !devices.count(fem)) throw std::runtime_error("unknown type or device");
            auto& d=devices.at(fem);
            // Validate every record before applying any of the message.
            size_t pos=0;
            while(pos<size) {
                if(size-pos<9) throw std::runtime_error("short record");
                auto kind=p[pos]; auto len=num(p+pos+1,4); if(kind<1 || kind>5 || len>size-pos-9) throw std::runtime_error("bad record length");
                auto b=p+pos+5;
                if(kind==1) {
                    if(len<20 || num(b+16,4)>(len-20)/4 || len!=20+4*num(b+16,4) || num(b,2)>=uint64_t(d.tx)*d.ty) throw std::runtime_error("bad tile");
                    for(size_t k=20;k<len;k+=4) if(num(b+k,2)>=d.tc || num(b+k+2,2)>=d.tr) throw std::runtime_error("bad pixel");
                } else if((kind==2 && len!=28) || (kind==3 && len!=40) || (kind==4 && (len!=16 || num(b,2)>=uint64_t(d.tx)*d.ty)) || (kind==5 && len!=96)) throw std::runtime_error("bad record body");
                pos+=len+9;
            }
            std::map<uint64_t,std::vector<Hit>> windows, gates;
            pos=0;
            while(pos<size) {
                auto kind=p[pos]; auto len=num(p+pos+1,4); auto b=p+pos+5;
                if(kind==1) {
                    unsigned tile=num(b,2); auto& hits=(num(b+2,2)&2) ? gates[num(b+8,8)] : windows[num(b+4,2)];
                    for(size_t k=20;k<len;k+=4) hits.push_back({unsigned((tile%d.tx)*d.tc+num(b+k,2)),unsigned((tile/d.tx)*d.tr+num(b+k+2,2))});
                }
                pos+=len+9;
            }
            std::vector<std::shared_ptr<Trigger>> triggers;
            pos=0;
            while(pos<size) {
                auto kind=p[pos]; auto len=num(p+pos+1,4); auto b=p+pos+5;
                if(kind==2) {
                    auto n=num(b,8), window=num(b+16,8);
                    auto& hits=window==std::numeric_limits<uint64_t>::max()?gates[n]:windows[window&0xffff];
                    triggers.push_back(std::make_shared<Trigger>(Trigger{n,num(b+8,8),hits}));
                }
                if(kind==1) { unsigned tile=num(b,2), x=tile%d.tx, y=tile/d.tx; auto count=num(b+16,4); d.tiles->Fill(x,y,count); d.frame_hits+=count; d.total+=count; for(size_t k=20;k<len;k+=4) d.hitmap->Fill(x*d.tc+num(b+k,2),y*d.tr+num(b+k+2,2)); }
                else if(kind==2) ++d.frame_triggers;
                else if(kind==3) { d.hits->Fill(d.frame_hits); d.triggers->Fill(d.frame_triggers); metrics(d); d.frame_hits=d.frame_triggers=0; }
                pos+=len+9;
            }
            if(fem==unsigned(reference)) { for(auto& [id,pair]:pairs) pair_message(pair,true,triggers); }
            else if(pairs.count(fem)) pair_message(pairs.at(fem),false,triggers);
        } catch(const std::exception& e) { ++discarded; LOG(error) << "DQM message: " << e.what(); }
        catch(...) { ++discarded; LOG(error) << "DQM message: unknown exception"; }
    }
    bool drain_once() {
        bool got=false;
        for(size_t i=0;i<fChannels.at("in").size();++i) { auto msg=NewMessage(); if(Receive(msg,"in",i,0)>=0) { got=true; process(msg->GetData(),msg->GetSize()); } }
        return got;
    }
public:
    MSensorDqm() {
        ROOT::EnableThreadSafety(); gROOT->SetBatch(kTRUE); TH1::AddDirectory(kFALSE);
        http=std::thread([this] { THttpServer s("http:127.0.0.1:8090"); s.SetTimer(0); { std::lock_guard<std::mutex> lock(mutex); server=&s; } while(alive) { { std::lock_guard<std::mutex> lock(mutex); s.ProcessRequests(); } std::this_thread::sleep_for(std::chrono::milliseconds(10)); } });
    }
    ~MSensorDqm() override { alive=false; if(http.joinable()) http.join(); }
    void InitTask() override { reference=fConfig->GetProperty<int>("reference-fem"); try { redis=std::make_unique<sw::redis::Redis>(fConfig->GetProperty<std::string>("metrics-redis-uri")); } catch(const std::exception& e) { LOG(error) << e.what(); } }
    void PreRun() override { std::lock_guard<std::mutex> lock(mutex); received=discarded=0; for(auto& [id,d]:devices) { d.hitmap->Reset(); d.tiles->Reset(); d.hits->Reset(); d.triggers->Reset(); d.frame_hits=d.frame_triggers=d.total=0; } for(auto& [id,p]:pairs) { p.col->Reset(); p.row->Reset(); p.dcol->Reset(); p.drow->Reset(); p.dt->Set(0); p.dn->Set(0); p.match->Reset(); p.lost->Reset(); p.ref_q.clear(); p.dev_q.clear(); p.locked=false; p.last_dt=0; p.last_t=0; p.since_ref=p.since_dev=0; p.matched=p.only_ref=p.only_dev=p.skipped=0; p.n=0; p.x0=p.sx=p.sy=p.sxx=p.sxy=0; } if(summary) { summary_dcol->Reset(); summary_drow->Reset(); summary_drift->Reset(); summary_offset->Reset(); summary_counts->Reset(); } }
    bool ConditionalRun() override { auto now=std::chrono::steady_clock::now(); if(now-last_summary>=std::chrono::seconds(1)) { std::lock_guard<std::mutex> lock(mutex); update_summary(); last_summary=now; } bool got=drain_once(); if(!got) std::this_thread::sleep_for(std::chrono::milliseconds(1)); return true; }
    void PostRun() override { while(drain_once()) {} std::this_thread::sleep_for(std::chrono::milliseconds(100)); while(drain_once()) {} std::lock_guard<std::mutex> lock(mutex); std::ostringstream out; out << "received=" << received << " discarded=" << discarded; for(auto& [id,d]:devices) out << " " << d.name << "=" << d.total; for(auto& [id,p]:pairs) { while(!p.ref_q.empty()) { p.ref_q.pop_front(); only(p,true); } while(!p.dev_q.empty()) { p.dev_q.pop_front(); only(p,false); } out << " " << devices.at(id).name << ":matched=" << p.matched << ",unmatched=" << p.only_ref+p.only_dev << ",skipped=" << p.skipped << ",only_ref=" << p.only_ref << ",only_dev=" << p.only_dev; } update_summary(); LOG(info) << out.str(); }
};
FairMQDevicePtr getDevice(const fair::mq::ProgOptions&) { return new MSensorDqm(); }
