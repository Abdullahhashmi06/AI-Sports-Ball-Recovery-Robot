// Protocol engine for the laptop <-> ESP32 link (ESP32 side).
//
// Implements docs/COMMUNICATION_PROTOCOL.md (protocol version 1):
// newline-framed JSON lines over USB serial at 115200 8N1, command/status
// model, READY/NOT_READY gating, latched fail-safes (e-stop, comms-loss),
// CMD_RESET re-arm, duplicate-command suppression, 20 Hz telemetry and
// 500 ms heartbeats.
//
// The engine is deliberately free of Arduino/ESP32 dependencies (only
// <ArduinoJson.h> in protocol.cpp) so the exact same code is compiled into
// the firmware (env:esp32dev) and into host unit tests (env:native, pio
// test -e native).  All hardware access goes through HardwarePort, which
// the application provides (src/main.cpp supplies a "not implemented" stub
// until motors/sensors are attached).
//
// This file must stay C++11-compatible (Arduino toolchains).

#ifndef BALLRECOVERY_PROTO_PROTOCOL_H
#define BALLRECOVERY_PROTO_PROTOCOL_H

#include <cstdint>
#include <string>
#include <vector>

namespace ballrecovery {
namespace proto {

// --- Protocol constants (mirror docs/COMMUNICATION_PROTOCOL.md) -----------
constexpr int kProtocolVersion = 1;
constexpr size_t kMaxLineBytes = 512;
constexpr uint32_t kHeartbeatPeriodMs = 500u;
constexpr uint32_t kTelemetryPeriodMs = 50u;      // 20 Hz
constexpr uint32_t kCommsLossTimeoutMs = 1000u;   // ESP32 liveness timeout
constexpr uint32_t kMaxSeq = 65535u;

// Storage-bin capacity placeholder.  The bin is not built yet (Person 4);
// this constant is a firmware configuration to be revisited, NOT a protocol
// value.  Documented as an implementation detail in docs/DECISION_LOG.md
// style — see the skeleton notes.
constexpr int kDefaultStorageCapacity = 10;

// Fault codes (protocol section 7 / Appendix A).
enum FaultCode {
  kFaultEstop = 1,
  kFaultCommsLost = 2,
  kFaultHardStop = 3,
  kFaultUltrasonic = 4,
  kFaultImu = 5,
  kFaultMotor = 6,
  kFaultVersionMismatch = 8,
  kFaultDispense = 9,
};

// ---------------------------------------------------------------------------
// Hardware abstraction (provided by the application)
// ---------------------------------------------------------------------------

// One TELE frame's worth of hardware data (protocol section 4.2 TELE).
// Values are the raw, ESP32-side truth.  Encoder fields are deltas since the
// previous snapshot (the hardware layer computes them).
struct TelemetrySnapshot {
  int enc_dl = 0;                 // left encoder delta (raw counts)
  int enc_dr = 0;                 // right encoder delta (raw counts)
  // Ultrasonic distances [front_left, front_right, left, right] in mm.
  // 0..4000 valid, 9999 = no echo, -1 = sensor fault (protocol section 4.2).
  int us[4] = {9999, 9999, 9999, 9999};
  float ax = 0.0f, ay = 0.0f, az = 0.0f;   // IMU accel, g
  float gx = 0.0f, gy = 0.0f, gz = 0.0f;   // IMU gyro, deg/s
  int pwm_l = 0, pwm_r = 0;                 // commanded drive duty -100..100
  float spd_l = 0.0f, spd_r = 0.0f;         // measured speed, % of max
  bool collect_trip = false;                // collection sensor blocked
  bool collect_running = false;             // intake roller running
  uint32_t ts_ms = 0;                       // snapshot time (ms since boot)
};

// Result of a hardware operation.  kNotImplemented is the bring-up skeleton's
// honest answer for anything that needs motors/actuators that are not wired
// yet (never fabricate a successful actuation).
enum class ActResult { kOk, kNotImplemented, kBusy, kFailed };

class HardwarePort {
 public:
  virtual ~HardwarePort() = default;

  // Continuous differential velocity demand, normalized -1..1 per axis
  // (protocol section 5).  The ESP32-side speed controller owns PWM.
  virtual ActResult SetDriveVelocity(float lin, float ang) = 0;
  // Stop drive motors.  emergency=true for immediate hard stop (the engine
  // latches the e-stop condition itself before calling this).
  virtual ActResult StopDrive(bool emergency) = 0;

  virtual ActResult StartIntake() = 0;
  virtual ActResult StopIntake() = 0;
  virtual ActResult OpenDispenseGate(int count) = 0;

  virtual TelemetrySnapshot Snapshot() = 0;

  // Sensor / IMU / driver initialization.  Called once after boot; READY is
  // only entered when this returns true (protocol section 7).
  virtual bool StartupChecksOk() = 0;
};

// ---------------------------------------------------------------------------
// Engine
// ---------------------------------------------------------------------------

enum class EngineState { kStarting, kReady, kLatched };

class ProtocolEngine {
 public:
  explicit ProtocolEngine(HardwarePort& hw,
                          int storage_capacity = kDefaultStorageCapacity);

  // Call once at boot (before the main loop).  Queues EVT_BOOT.  The engine
  // starts in kStarting / NOT_READY (safe motor state) and the application
  // calls CompleteStartupChecks() once its initialization ran.
  void Begin(uint32_t now_ms, const char* reason, const char* fw_label,
             std::vector<std::string>& out);

  // Run startup checks (via HardwarePort).  Returns true when READY.
  bool CompleteStartupChecks(std::vector<std::string>& out);

  // Feed one received line (no trailing newline).  Never throws, never
  // blocks; anything to transmit is appended to `out`.
  void HandleLine(const std::string& line, uint32_t now_ms,
                  std::vector<std::string>& out);

  // Call every main-loop iteration: schedules heartbeats/telemetry and
  // enforces the communication-loss timeout.
  void Tick(uint32_t now_ms, std::vector<std::string>& out);

  // -- State queries --------------------------------------------------------
  bool IsReady() const { return state_ == EngineState::kReady; }
  EngineState State() const { return state_; }
  const char* StateName() const;
  const std::vector<int>& Faults() const { return faults_; }
  bool HasFault(int code) const;
  uint32_t MalformedLines() const { return malformed_; }
  uint32_t LastRxMs() const { return last_rx_ms_; }

  // Stored-ball count is ESP32-authoritative (protocol section 10).
  int StorageCount() const { return balls_count_; }
  int StorageCapacity() const { return balls_capacity_; }
  bool StorageFull() const { return balls_count_ >= balls_capacity_; }

  // -- Entry points for the firmware/hardware layer (Person 5) --------------
  //
  // PROTOCOL §10 MACHINERY — implemented and unit-tested for early protocol
  // bring-up, but PHYSICALLY UNREACHABLE until Person 5 integrates the real
  // hardware: no intake roller, verification sensor, storage bin or servo
  // gate exists yet.  The hardware layer must call these once wired; until
  // then commands are answered RESP_ERR INTERNAL ("not implemented").
  //
  // Collection verification sensor tripped during an intake run: the ball
  // entered the storage path.
  void OnCollectionVerified(uint32_t now_ms, std::vector<std::string>& out);
  // Dispense finished (async, real gate later): status "ok"/"failed"/"empty".
  void OnDispenseResult(const char* status, uint32_t now_ms,
                        std::vector<std::string>& out);
  // Async fault from the hardware layer (sensor fault, motor fault, ...).
  void RaiseFault(int code, uint32_t now_ms, std::vector<std::string>& out,
                  const char* info = nullptr);
  // Local emergency stop (physical e-stop pin later; independent of the
  // laptop).  Latches the same NOT_READY state as a remote CMD_STOP.
  void TriggerEmergencyStop(uint32_t now_ms, std::vector<std::string>& out);

 private:
  // Outgoing message emission helpers (all append one JSON line to `out`).
  uint32_t NextSeq();
  void EmitBoot(const char* reason, const char* fw_label, uint32_t now_ms,
                std::vector<std::string>& out);
  void EmitHeartbeat(uint32_t now_ms, std::vector<std::string>& out);
  void EmitTelemetry(uint32_t now_ms, std::vector<std::string>& out);
  // The RESP emitters return the line they queued and remember it for the
  // duplicate-command replay (protocol section 9).
  std::string EmitRespOk(int ack, std::vector<std::string>& out);
  std::string EmitRespErr(int ack, const char* code, const char* msg,
                          std::vector<std::string>& out);
  void EmitFault(int code, uint32_t now_ms, const char* info,
                 std::vector<std::string>& out);
  void EmitCollectEvent(const char* status, uint32_t now_ms,
                        std::vector<std::string>& out);
  void EmitDispenseEvent(const char* status, uint32_t now_ms,
                         std::vector<std::string>& out);
  void StoreResponse(const std::string& type, int seq);

  // Handlers.
  bool CanExecuteCommand(const char* type, int seq,
                         std::vector<std::string>& out);
  void HandleCmdMove(float lin, float ang, int seq,
                     std::vector<std::string>& out);
  void HandleCmdStop(bool emergency, int seq, uint32_t now_ms,
                     std::vector<std::string>& out);
  void HandleCmdIntake(bool start, int seq, uint32_t now_ms,
                       std::vector<std::string>& out);
  void HandleCmdDispense(int count, int seq, uint32_t now_ms,
                         std::vector<std::string>& out);
  void ApplyEmergencyStop(uint32_t now_ms, std::vector<std::string>& out,
                          const char* info);
  void ClearFaultsExceptLatches();
  void AddFault(int code);

  HardwarePort& hw_;
  EngineState state_ = EngineState::kStarting;
  std::vector<int> faults_;

  uint32_t seq_ = 1u;
  uint32_t last_rx_ms_ = 0u;
  uint32_t last_hb_ms_ = 0u;
  uint32_t last_tele_ms_ = 0u;
  uint32_t malformed_ = 0u;

  // Duplicate-command suppression (protocol section 9).
  std::string last_cmd_type_;
  int last_cmd_seq_ = -1;
  std::string last_resp_line_;

  // Actuator run state (mutual exclusion, protocol section 7/10).
  bool intake_active_ = false;
  bool dispense_busy_ = false;
  int dispense_count_ = 0;

  int balls_count_ = 0;
  int balls_capacity_;
};

}  // namespace proto
}  // namespace ballrecovery

#endif  // BALLRECOVERY_PROTO_PROTOCOL_H
