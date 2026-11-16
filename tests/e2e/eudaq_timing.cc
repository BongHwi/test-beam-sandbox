// SPDX-License-Identifier: LGPL-3.0-or-later
// Test-only monotonic observations around exported public EUDAQ methods.
#include <dlfcn.h>
#include <fcntl.h>
#include <time.h>
#include <unistd.h>
#include <cstdio>
#include <cstdlib>
#include <string>

namespace {
void record(const char *event) {
    const char *path = std::getenv("EUDAQ_TIMING_TRACE");
    if (!path || !*path) return;
    timespec ts{};
    if (clock_gettime(CLOCK_MONOTONIC, &ts)) return;
    int fd = open(path, O_WRONLY | O_CREAT | O_APPEND | O_CLOEXEC, 0600);
    if (fd < 0) return;
    char buf[256];
    int n = std::snprintf(buf, sizeof(buf), "{\"event\":\"%s\",\"pid\":%ld,\"monotonic_ns\":%lld}\n", event,
                          static_cast<long>(getpid()), static_cast<long long>(ts.tv_sec)*1000000000LL+ts.tv_nsec);
    if (n > 0 && static_cast<size_t>(n) < sizeof(buf)) (void)write(fd, buf, static_cast<size_t>(n));
    close(fd);
}
template<typename F> F next(const char *name) {
    void *symbol = dlsym(RTLD_NEXT, name);
    // Python can load the extension and its dependencies in a local namespace.
    if (!symbol) {
        static void *core = dlopen("libeudaq_core.so.2.8", RTLD_LAZY | RTLD_NOLOAD);
        if (core) symbol = dlsym(core, name);
    }
    if (!symbol) { record("interposition_lookup_failed"); std::abort(); }
    return reinterpret_cast<F>(symbol);
}
}
extern "C" void _ZN5eudaq10RunControl8StartRunEv(void *self) {
    using Fn=void(*)(void*); static Fn fn=next<Fn>("_ZN5eudaq10RunControl8StartRunEv");
    record("runcontrol_start_begin"); try { fn(self); } catch (...) { record("runcontrol_start_exception"); throw; } record("runcontrol_start_end");
}
extern "C" void _ZN5eudaq13DataCollector10OnStartRunEv(void *self) {
    using Fn=void(*)(void*); static Fn fn=next<Fn>("_ZN5eudaq13DataCollector10OnStartRunEv");
    record("collector_start_begin"); try { fn(self); } catch (...) { record("collector_start_exception"); throw; } record("collector_start_end");
}
extern "C" void _ZN5eudaq8Producer10OnStartRunEv(void *self) {
    using Fn=void(*)(void*); static Fn fn=next<Fn>("_ZN5eudaq8Producer10OnStartRunEv");
    record("producer_start_begin"); try { fn(self); } catch (...) { record("producer_start_exception"); throw; } record("producer_start_end");
}
extern "C" void _ZN5eudaq10DataSender7ConnectERKNSt7__cxx1112basic_stringIcSt11char_traitsIcESaIcEEE(void *self,const std::string &address) {
    using Fn=void(*)(void*,const std::string&); static Fn fn=next<Fn>("_ZN5eudaq10DataSender7ConnectERKNSt7__cxx1112basic_stringIcSt11char_traitsIcESaIcEEE");
    record("sender_connect_begin"); try { fn(self,address); } catch (...) { record("sender_connect_exception"); throw; } record("sender_connect_end");
}
