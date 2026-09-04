// Shared helpers for the protocol engine native unit tests (pio test -e
// native).  This header is compiled into the host test binary only.

#ifndef FIRMWARE_TEST_TEST_UTIL_H
#define FIRMWARE_TEST_TEST_UTIL_H

#include <ArduinoJson.h>

#include <cstring>
#include <string>
#include <vector>

#include "protocol.h"

namespace ballrecovery {
namespace proto {
namespace testutil {

// ---------------------------------------------------------------------------
// Fake hardware port: records every call and returns configurable results.
// Defaults behave like the real bring-up hardware would once wired: OK for
// every operation (the DeviceHardware stub in src/main.cpp answers
// kNotImplemented; tests override results where that matters).
// ---------------------------------------------------------------------------
struct FakeHardware : HardwarePort {
  using ActResult = ballrecovery::proto::ActResult;

  ActResult move_result = ActResult::kOk;
  ActResult intake_result = ActResult::kOk;
  ActResult dispense_result = ActResult::kOk;
  bool startup_ok = true;

  int move_calls = 0;
  int stop_calls = 0;
  int intake_start_calls = 0;
  int intake_stop_calls = 0;
  int dispense_calls = 0;
  int snapshots = 0;

  float last_lin = 0.0f;
  float last_ang = 0.0f;
  int last_dispense_count = 0;
  bool last_stop_emergency = false;

  TelemetrySnapshot snap;  // pre-filled by the test if needed

  ActResult SetDriveVelocity(float lin, float ang) override {
    ++move_calls;
    last_lin = lin;
    last_ang = ang;
    return move_result;
  }
  ActResult StopDrive(bool emergency) override {
    ++stop_calls;
    last_stop_emergency = emergency;
    return ActResult::kOk;
  }
  ActResult StartIntake() override {
    ++intake_start_calls;
    return intake_result;
  }
  ActResult StopIntake() override {
    ++intake_stop_calls;
    return ActResult::kOk;
  }
  ActResult OpenDispenseGate(int count) override {
    ++dispense_calls;
    last_dispense_count = count;
    return dispense_result;
  }
  TelemetrySnapshot Snapshot() override {
    ++snapshots;
    return snap;
  }
  bool StartupChecksOk() override { return startup_ok; }
};

// Per-test context: hardware + engine + outgoing buffer.
struct Ctx {
  FakeHardware hw;
  ProtocolEngine engine;
  std::vector<std::string> out;
  // capacity overrides the placeholder storage-bin size for capacity tests.
  Ctx(int capacity = kDefaultStorageCapacity) : engine(hw, capacity) {}
  void clear() { out.clear(); }
  size_t count(const char* type) const {
    const std::string needle = std::string("\"type\":\"") + type + "\"";
    size_t n = 0;
    for (size_t i = 0; i < out.size(); ++i) {
      if (out[i].find(needle) != std::string::npos) ++n;
    }
    return n;
  }
};

inline std::string FindLine(const std::vector<std::string>& out,
                            const char* type) {
  const std::string needle = std::string("\"type\":\"") + type + "\"";
  for (size_t i = 0; i < out.size(); ++i) {
    if (out[i].find(needle) != std::string::npos) return out[i];
  }
  return std::string();
}

inline DynamicJsonDocument Parse(const std::string& line) {
  DynamicJsonDocument doc(2048);
  deserializeJson(doc, line.c_str());
  return doc;
}

// True when `out` contains a RESP_ERR whose "code" equals `code`.
inline bool HasRespErr(const std::vector<std::string>& out,
                       const char* code) {
  const std::string line = FindLine(out, "RESP_ERR");
  if (line.empty()) return false;
  DynamicJsonDocument doc = Parse(line);
  return std::strcmp(doc["code"] | "", code) == 0;
}

}  // namespace testutil
}  // namespace proto
}  // namespace ballrecovery

#endif  // FIRMWARE_TEST_TEST_UTIL_H
