// ESP32 firmware — bring-up skeleton (laptop <-> ESP32 communication only).
//
// Implements docs/COMMUNICATION_PROTOCOL.md (protocol version 1) over USB
// serial at 115200 8N1:
//   * EVT_BOOT once at startup, then HEARTBEAT (500 ms) + TELE (20 Hz)
//   * command parsing/validation, RESP_OK / RESP_ERR, NOT_READY gating
//   * READY after the (currently trivial) startup checks
//   * communication-loss safe stop (1000 ms) with latched NOT_READY and
//     CMD_RESET scope=all re-arm, duplicate-command suppression
//
// NO MOTORS / SENSORS ARE ATTACHED YET.  All hardware access goes through
// DeviceHardware below, which answers "not implemented" for anything that
// would actuate and reports safe placeholder telemetry (encoder 0, ultrasonic
// 9999 = no echo, IMU zeros).  Nothing here pretends hardware exists; the
// TODO(P5) markers show exactly where the real drivers plug in later.
//
// Build / upload / monitor (from firmware/):
//   pio run -t upload && pio device monitor
// Host unit tests for the protocol engine:
//   pio test -e native

#include <Arduino.h>
#include <esp_system.h>

#include <string>
#include <vector>

#include "protocol.h"

using ballrecovery::proto::ActResult;
using ballrecovery::proto::HardwarePort;
using ballrecovery::proto::ProtocolEngine;
using ballrecovery::proto::TelemetrySnapshot;
using ballrecovery::proto::kMaxLineBytes;

namespace {

constexpr char kFwLabel[] = "0.1.0-skeleton";

// ===========================================================================
// NOT YET IMPLEMENTED — HARDWARE REQUIRED  (safety-relevant)
// ---------------------------------------------------------------------------
// The protocol/state-machine behaviour in lib/protocol is implemented and
// unit-tested in SOFTWARE, but the PHYSICAL safety mechanisms below are NOT
// ACTIVE until the corresponding hardware is attached, integrated and
// tested.  Until then this firmware must never be treated as protecting the
// robot: it reports placeholder telemetry and refuses actuation with
// "not implemented" (RESP_ERR INTERNAL).
//
//   1. Physical emergency-stop wiring          — protocol §7 rule 1
//                                              (switch type/pin: OPEN, OD-14)
//   2. Local obstacle hard-stop at the forward
//      ultrasonic sensor(s)                    — protocol §7 rule 4
//                                              (threshold + sensor layout:
//                                              OPEN, OD-08; hard-stop code 3
//                                              not produced by software yet)
//   3. Ultrasonic / IMU sensor-fault detection — protocol §7 rule 5
//                                              (fault codes 4/5: not raised
//                                              because no sensors are wired)
//   4. Motor / encoder fault detection and
//      closed-loop drive control               — protocol §7 rule 6
//                                              (fault code 6: not raised yet)
//
// TODO(P5): integrate the real drivers, then delete/replace this banner with
// the actual per-subsystem wiring and self-test documentation.
// ===========================================================================

// ---------------------------------------------------------------------------
// Hardware port — bring-up stub.
//
// Everything hardware-dependent is marked TODO(P5): replace the stub methods
// with real drivers as motors/encoders/sensors are wired.  Until then the
// ESP32 is honest: actuation returns "not implemented" (surfaced as RESP_ERR
// INTERNAL to the laptop) and telemetry carries no-fault placeholder values.
// ---------------------------------------------------------------------------
class DeviceHardware : public HardwarePort {
 public:
  ActResult SetDriveVelocity(float lin, float ang) override {
    (void)lin;
    (void)ang;
    // TODO(P5): differential drive speed controller + motor drivers.  Until
    // motors exist this must NOT claim success.
    return ActResult::kNotImplemented;
  }

  ActResult StopDrive(bool emergency) override {
    (void)emergency;
    // Safe motor state = motors stopped; on the skeleton they already are.
    // TODO(P5): ramp down / hard-stop the real drivers.
    return ActResult::kOk;
  }

  ActResult StartIntake() override {
    // TODO(P5): intake roller driver (front roller, Module 3).
    return ActResult::kNotImplemented;
  }

  ActResult StopIntake() override {
    // TODO(P5): stop the intake roller; on the skeleton it already is off.
    return ActResult::kOk;
  }

  ActResult OpenDispenseGate(int count) override {
    (void)count;
    // TODO(P5): servo gate; the result arrives later via
    // ProtocolEngine::OnDispenseResult() once the mechanism exists.
    return ActResult::kNotImplemented;
  }

  TelemetrySnapshot Snapshot() override {
    TelemetrySnapshot s;
    // Placeholders (protocol §4.2 TELE) — no sensors wired yet:
    //   enc       0/0          (no encoders)
    //   us[4]     9999 = no echo / not populated (no ultrasonics)
    //   imu       zeros        (no MPU6050)
    //   mot       pwm 0, spd 0 (nothing moving)
    //   collect   trip=false   (no verification sensor)
    s.enc_dl = 0;
    s.enc_dr = 0;
    s.us[0] = 9999;
    s.us[1] = 9999;
    s.us[2] = 9999;
    s.us[3] = 9999;
    s.ts_ms = millis();
    // TODO(P5): read encoders (deltas since previous call), 4x ultrasonic,
    // MPU6050, verification sensor, and the actual PWM/speed values.
    return s;
  }

  bool StartupChecksOk() override {
    // TODO(P5): real init + self-test of the motor drivers, ultrasonic
    // sensors and IMU before returning true (protocol §7 READY semantics).
    return true;  // skeleton: nothing to check yet
  }
};

DeviceHardware g_hardware;
ProtocolEngine g_engine(g_hardware);
std::string g_line;

const char* BootReason() {
  switch (esp_reset_reason()) {
    case ESP_RST_POWERON:
    case ESP_RST_UNKNOWN:
      return "poweron";
    case ESP_RST_WDT:
    case ESP_RST_TASK_WDT:
    case ESP_RST_INT_WDT:
      return "watchdog";
    default:
      return "reset";  // ESP_RST_SW, ESP_RST_DEEPSLEEP, brownout, ...
  }
}

void Flush(std::vector<std::string>& out) {
  for (size_t i = 0; i < out.size(); ++i) {
    Serial.println(out[i].c_str());
  }
  out.clear();
}

}  // namespace

void setup() {
  Serial.begin(115200);  // protocol §2: 115200 8N1
  // Small settle delay so the USB-UART bridge is ready before EVT_BOOT.
  delay(100);

  std::vector<std::string> out;
  g_engine.Begin(millis(), BootReason(), kFwLabel, out);  // -> EVT_BOOT
  Flush(out);

  // Startup checks (skeleton: instant).  READY only after this returns true.
  g_engine.CompleteStartupChecks(out);
  Flush(out);
}

void loop() {
  // Read one line at a time from the serial port (LF-terminated; a trailing
  // CR is ignored per protocol §2).
  while (Serial.available() > 0) {
    const char c = static_cast<char>(Serial.read());
    if (c == '\n') {
      if (!g_line.empty()) {
        std::vector<std::string> out;
        g_engine.HandleLine(g_line, millis(), out);
        Flush(out);
        g_line.clear();
      }
    } else if (c == '\r') {
      // ignore: CRLF is tolerated
    } else {
      if (g_line.size() < kMaxLineBytes) {
        g_line.push_back(c);
      } else {
        g_line.clear();  // overlong line: drop it (protocol §2)
      }
    }
  }

  // Periodic work: heartbeats, 20 Hz telemetry, comms-loss timeout.
  std::vector<std::string> out;
  g_engine.Tick(millis(), out);
  Flush(out);
}
