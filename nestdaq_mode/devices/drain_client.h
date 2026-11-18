// SPDX-License-Identifier: LGPL-3.0-or-later
#pragma once
#include <chrono>
#include <optional>
#include <string>
#include <fairlogger/Logger.h>
#include <sw/redis++/redis++.h>

namespace sandbox {
class DrainClient {
    sw::redis::Redis redis;
    std::string member, last;
    std::chrono::steady_clock::time_point next{};
public:
    DrainClient(const std::string& uri, const std::string& id) : redis(uri), member(id) {}
    void Register() { try { redis.sadd("sandbox:drain:members", member); } catch (const std::exception& e) { LOG(error) << e.what(); } }
    void Unregister() { try { redis.srem("sandbox:drain:members", member); } catch (const std::exception& e) { LOG(error) << e.what(); } }
    void Arm() { try { last = redis.get("sandbox:drain:request").value_or(""); next = {}; } catch (const std::exception& e) { LOG(error) << e.what(); } }
    std::optional<std::string> Poll() {
        auto now = std::chrono::steady_clock::now();
        if (now < next) return std::nullopt;
        next = now + std::chrono::milliseconds(100);
        try { auto value = redis.get("sandbox:drain:request").value_or(""); if (!value.empty() && value != last) { last = value; return value; } }
        catch (const std::exception& e) { LOG(error) << e.what(); }
        return std::nullopt;
    }
    void Done(const std::string& id) { try { redis.set("sandbox:drain:done:" + member, id); } catch (const std::exception& e) { LOG(error) << e.what(); } }
};
}
