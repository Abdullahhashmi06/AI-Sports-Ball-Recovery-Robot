// Protocol engine implementation (see protocol.h for the contract).
//
// ESP32-side behaviour per docs/COMMUNICATION_PROTOCOL.md v1.  Only
// <ArduinoJson.h> is needed here, so this file also builds on the host for
// `pio test -e native`.

#include "protocol.h"

#include <ArduinoJson.h>

#include <cmath>
#include <cstring>

namespace ballrecovery {
namespace proto {

namespace {

constexpr char kMsgHeartbeat[] = "HEARTBEAT";
constexpr char kMsgCmdMove[] = "CMD_MOVE";
constexpr char kMsgCmdStop[] = "CMD_STOP";
constexpr char kMsgCmdIntake[] = "CMD_INTAKE";
constexpr char kMsgCmdDispense[] = "CMD_DISPENSE";
constexpr char kMsgCmdReset[] = "CMD_RESET";

// True if the variant holds any JSON number (ArduinoJson stores JSON ints and
// floats as separate C++ types; booleans and strings are not numbers).
bool IsNumber(const JsonVariant& v) {
  return v.is<int>() || v.is<float>() || v.is<double>();
}

// Serialize a document as one compact line, appended to `out`.
void PushLine(const JsonDocument& doc, std::vector<std::string>& out) {
  char buffer[kMaxLineBytes + 16];
  const size_t n = serializeJson(doc, buffer, sizeof(buffer));
  if (n > 0 && n < sizeof(buffer)) {
    out.emplace_back(buffer, n);
  }
}

const char* FaultName(int code) {
  switch (code) {
    case kFaultEstop: return "e-stop latched";
    case kFaultCommsLost: return "comms lost";
    case kFaultHardStop: return "obstacle hard stop";
    case kFaultUltrasonic: return "ultrasonic fault";
    case kFaultImu: return "IMU fault";
    case kFaultMotor: return "motor fault";
    case kFaultVersionMismatch: return "protocol version mismatch";
    case kFaultDispense: return "dispense mechanism";
    default: return "unknown fault";
  }
}

}  // namespace

// ---------------------------------------------------------------------------
ProtocolEngine::ProtocolEngine(HardwarePort& hw, int storage_capacity)
    : hw_(hw), balls_capacity_(storage_capacity > 0 ? storage_capacity
                                                    : kDefaultStorageCapacity) {}

const char* ProtocolEngine::StateName() const {
  switch (state_) {
    case EngineState::kStarting: return "STARTING";
    case EngineState::kReady: return "READY";
    case EngineState::kLatched: return "NOT_READY (latched)";
  }
  return "UNKNOWN";
}

bool ProtocolEngine::HasFault(int code) const {
  for (size_t i = 0; i < faults_.size(); ++i) {
    if (faults_[i] == code) return true;
  }
  return false;
}

uint32_t ProtocolEngine::NextSeq() {
  const uint32_t s = seq_;
  seq_ = (seq_ >= kMaxSeq) ? 0u : seq_ + 1u;
  return s;
}

void ProtocolEngine::AddFault(int code) {
  if (HasFault(code)) return;
  std::vector<int>::iterator it = faults_.begin();
  while (it != faults_.end() && *it < code) ++it;
  faults_.insert(it, code);
}

void ProtocolEngine::ClearFaultsExceptLatches() {
  // scope=faults clears non-latching faults; e-stop (1) and comms-loss (2)
  // latches stay until a scope=all reset (protocol §7).
  std::vector<int> remaining;
  for (size_t i = 0; i < faults_.size(); ++i) {
    if (faults_[i] == kFaultEstop || faults_[i] == kFaultCommsLost) {
      remaining.push_back(faults_[i]);
    }
  }
  faults_.swap(remaining);
}

// ---------------------------------------------------------------------------
// Emission
// ---------------------------------------------------------------------------

void ProtocolEngine::EmitBoot(const char* reason, const char* fw_label,
                              uint32_t now_ms, std::vector<std::string>& out) {
  DynamicJsonDocument doc(2048);
  JsonObject root = doc.to<JsonObject>();
  root["v"] = kProtocolVersion;
  root["type"] = "EVT_BOOT";
  root["seq"] = static_cast<int>(NextSeq());
  root["ts"] = static_cast<int>(now_ms);
  root["reason"] = reason;
  root["fw"] = fw_label;
  PushLine(doc, out);
}

void ProtocolEngine::EmitHeartbeat(uint32_t now_ms,
                                   std::vector<std::string>& out) {
  DynamicJsonDocument doc(2048);
  JsonObject root = doc.to<JsonObject>();
  root["v"] = kProtocolVersion;
  root["type"] = kMsgHeartbeat;
  root["seq"] = static_cast<int>(NextSeq());
  root["ts"] = static_cast<int>(now_ms);
  PushLine(doc, out);
}

void ProtocolEngine::EmitTelemetry(uint32_t now_ms,
                                   std::vector<std::string>& out) {
  const TelemetrySnapshot s = hw_.Snapshot();
  DynamicJsonDocument doc(2048);
  JsonObject root = doc.to<JsonObject>();
  root["v"] = kProtocolVersion;
  root["type"] = "TELE";
  root["seq"] = static_cast<int>(NextSeq());
  root["ts"] = static_cast<int>(now_ms);

  JsonObject enc = root.createNestedObject("enc");
  enc["dl"] = s.enc_dl;
  enc["dr"] = s.enc_dr;

  JsonArray us = root.createNestedArray("us");
  for (int i = 0; i < 4; ++i) us.add(s.us[i]);

  JsonObject imu = root.createNestedObject("imu");
  imu["ax"] = s.ax;
  imu["ay"] = s.ay;
  imu["az"] = s.az;
  imu["gx"] = s.gx;
  imu["gy"] = s.gy;
  imu["gz"] = s.gz;

  JsonObject mot = root.createNestedObject("mot");
  mot["pwm_l"] = s.pwm_l;
  mot["pwm_r"] = s.pwm_r;
  mot["spd_l"] = s.spd_l;
  mot["spd_r"] = s.spd_r;

  JsonObject collect = root.createNestedObject("collect");
  collect["trip"] = s.collect_trip;
  collect["state"] = s.collect_running ? "running" : "idle";

  JsonObject balls = root.createNestedObject("balls");
  balls["count"] = balls_count_;
  balls["full"] = StorageFull();

  JsonArray faults = root.createNestedArray("faults");
  for (size_t i = 0; i < faults_.size(); ++i) faults.add(faults_[i]);

  PushLine(doc, out);
}

std::string ProtocolEngine::EmitRespOk(int ack, std::vector<std::string>& out) {
  DynamicJsonDocument doc(2048);
  JsonObject root = doc.to<JsonObject>();
  root["v"] = kProtocolVersion;
  root["type"] = "RESP_OK";
  root["seq"] = static_cast<int>(NextSeq());
  root["ack"] = ack;
  std::string line;
  char buffer[kMaxLineBytes + 16];
  const size_t n = serializeJson(doc, buffer, sizeof(buffer));
  if (n > 0 && n < sizeof(buffer)) line.assign(buffer, n);
  out.push_back(line);
  last_resp_line_ = line;
  return line;
}

std::string ProtocolEngine::EmitRespErr(int ack, const char* code,
                                        const char* msg,
                                        std::vector<std::string>& out) {
  DynamicJsonDocument doc(2048);
  JsonObject root = doc.to<JsonObject>();
  root["v"] = kProtocolVersion;
  root["type"] = "RESP_ERR";
  root["seq"] = static_cast<int>(NextSeq());
  root["ack"] = ack;
  root["code"] = code;
  if (msg != nullptr && msg[0] != '\0') root["msg"] = msg;
  std::string line;
  char buffer[kMaxLineBytes + 16];
  const size_t n = serializeJson(doc, buffer, sizeof(buffer));
  if (n > 0 && n < sizeof(buffer)) line.assign(buffer, n);
  out.push_back(line);
  last_resp_line_ = line;
  return line;
}

void ProtocolEngine::EmitFault(int code, uint32_t now_ms, const char* info,
                               std::vector<std::string>& out) {
  DynamicJsonDocument doc(2048);
  JsonObject root = doc.to<JsonObject>();
  root["v"] = kProtocolVersion;
  root["type"] = "EVT_FAULT";
  root["seq"] = static_cast<int>(NextSeq());
  root["ts"] = static_cast<int>(now_ms);
  root["code"] = code;
  root["info"] = (info != nullptr && info[0] != '\0') ? info : FaultName(code);
  PushLine(doc, out);
}

void ProtocolEngine::EmitCollectEvent(const char* status, uint32_t now_ms,
                                      std::vector<std::string>& out) {
  DynamicJsonDocument doc(2048);
  JsonObject root = doc.to<JsonObject>();
  root["v"] = kProtocolVersion;
  root["type"] = "EVT_COLLECT";
  root["seq"] = static_cast<int>(NextSeq());
  root["ts"] = static_cast<int>(now_ms);
  root["status"] = status;
  JsonObject balls = root.createNestedObject("balls");
  balls["count"] = balls_count_;
  balls["full"] = StorageFull();
  PushLine(doc, out);
}

void ProtocolEngine::EmitDispenseEvent(const char* status, uint32_t now_ms,
                                       std::vector<std::string>& out) {
  DynamicJsonDocument doc(2048);
  JsonObject root = doc.to<JsonObject>();
  root["v"] = kProtocolVersion;
  root["type"] = "EVT_DISPENSE";
  root["seq"] = static_cast<int>(NextSeq());
  root["ts"] = static_cast<int>(now_ms);
  root["status"] = status;
  JsonObject balls = root.createNestedObject("balls");
  balls["count"] = balls_count_;
  balls["full"] = StorageFull();
  PushLine(doc, out);
}

void ProtocolEngine::StoreResponse(const std::string& type, int seq) {
  // Called whenever a RESP was emitted for a command; used by the duplicate
  // suppression rule (protocol §9).  last_resp_line_ was set by EmitResp*.
  last_cmd_type_ = type;
  last_cmd_seq_ = seq;
}

// ---------------------------------------------------------------------------
// Hardware/firmware layer entry points
// ---------------------------------------------------------------------------

void ProtocolEngine::RaiseFault(int code, uint32_t now_ms,
                                std::vector<std::string>& out,
                                const char* info) {
  if (HasFault(code)) return;  // latched faults are reported once per raise
  AddFault(code);
  EmitFault(code, now_ms, info, out);
}

void ProtocolEngine::TriggerEmergencyStop(uint32_t now_ms,
                                          std::vector<std::string>& out) {
  ApplyEmergencyStop(now_ms, out, "e-stop (local)");
}

void ProtocolEngine::ApplyEmergencyStop(uint32_t now_ms,
                                        std::vector<std::string>& out,
                                        const char* info) {
  hw_.StopDrive(true);
  if (intake_active_) {
    intake_active_ = false;
    hw_.StopIntake();
  }
  state_ = EngineState::kLatched;
  RaiseFault(kFaultEstop, now_ms, out, info);
}

void ProtocolEngine::OnCollectionVerified(uint32_t now_ms,
                                          std::vector<std::string>& out) {
  if (!intake_active_) return;  // spurious sensor trip; ignore
  intake_active_ = false;
  hw_.StopIntake();  // auto-stop the roller (protocol §10 success path)
  if (!StorageFull()) {
    ++balls_count_;
  }
  EmitCollectEvent("collected", now_ms, out);
}

void ProtocolEngine::OnDispenseResult(const char* status, uint32_t now_ms,
                                      std::vector<std::string>& out) {
  if (!dispense_busy_) return;
  dispense_busy_ = false;
  const bool ok = (std::strcmp(status, "ok") == 0);
  const bool failed = (std::strcmp(status, "failed") == 0);
  const bool empty = (std::strcmp(status, "empty") == 0);
  if (!ok && !failed && !empty) {
    RaiseFault(kFaultDispense, now_ms, out, "invalid dispense result");
    return;
  }
  if (ok) {
    balls_count_ -= dispense_count_;
    if (balls_count_ < 0) balls_count_ = 0;
  }
  if (failed) {
    RaiseFault(kFaultDispense, now_ms, out, "dispense mechanism timeout");
  }
  dispense_count_ = 0;
  EmitDispenseEvent(status, now_ms, out);
}

// ---------------------------------------------------------------------------
// Lifecycle
// ---------------------------------------------------------------------------

void ProtocolEngine::Begin(uint32_t now_ms, const char* reason,
                           const char* fw_label, std::vector<std::string>& out) {
  state_ = EngineState::kStarting;
  faults_.clear();
  last_rx_ms_ = now_ms;   // the 1000 ms laptop-liveness window starts now
  last_hb_ms_ = now_ms;
  last_tele_ms_ = now_ms;
  last_cmd_seq_ = -1;
  last_resp_line_.clear();
  intake_active_ = false;
  dispense_busy_ = false;
  dispense_count_ = 0;
  balls_count_ = 0;
  EmitBoot(reason, fw_label, now_ms, out);
}

bool ProtocolEngine::CompleteStartupChecks(std::vector<std::string>& out) {
  if (state_ == EngineState::kStarting && hw_.StartupChecksOk()) {
    state_ = EngineState::kReady;
    (void)out;  // no message needed: READY is observable via RESP_OK
  }
  return state_ == EngineState::kReady;
}

// ---------------------------------------------------------------------------
// Periodic processing
// ---------------------------------------------------------------------------

void ProtocolEngine::Tick(uint32_t now_ms, std::vector<std::string>& out) {
  // Heartbeat every 500 ms and telemetry at 20 Hz — in every state, so the
  // laptop can always see the ESP32 (protocol §8, flow F).
  if (now_ms - last_hb_ms_ >= kHeartbeatPeriodMs) {
    last_hb_ms_ = now_ms;
    EmitHeartbeat(now_ms, out);
  }
  if (now_ms - last_tele_ms_ >= kTelemetryPeriodMs) {
    last_tele_ms_ = now_ms;
    EmitTelemetry(now_ms, out);
  }

  // Communication-loss safe stop (protocol §7 rule 3): no valid message for
  // 1000 ms -> stop, latch COMMS_LOST (fault 2), stay NOT_READY until a
  // CMD_RESET scope=all re-arms.  A returning heartbeat never clears it.
  if (state_ != EngineState::kLatched &&
      now_ms - last_rx_ms_ >= kCommsLossTimeoutMs) {
    last_rx_ms_ = now_ms;  // latch once; do not re-trigger on later ticks
    state_ = EngineState::kLatched;
    hw_.StopDrive(true);
    if (intake_active_) {
      intake_active_ = false;
      hw_.StopIntake();
    }
    RaiseFault(kFaultCommsLost, now_ms, out,
               "no valid message from laptop within 1000 ms");
  }
}

// ---------------------------------------------------------------------------
// Incoming line handling
// ---------------------------------------------------------------------------

void ProtocolEngine::HandleLine(const std::string& line, uint32_t now_ms,
                                std::vector<std::string>& out) {
  if (line.size() > kMaxLineBytes) {
    ++malformed_;
    return;
  }

  DynamicJsonDocument doc(2048);
  DeserializationError err = deserializeJson(doc, line.c_str());
  if (err) {
    ++malformed_;  // completely malformed: discard + count, no RESP_ERR (§2)
    return;
  }
  JsonObject root = doc.as<JsonObject>();
  if (root.isNull()) {
    ++malformed_;
    return;
  }

  // Header recoverability: type (string) and seq (int 0..65535).
  if (!root["type"].is<const char*>()) {
    ++malformed_;
    return;
  }
  if (!root["seq"].is<int>()) {
    ++malformed_;
    return;
  }
  const char* type = root["type"].as<const char*>();
  const int seq = root["seq"].as<int>();
  if (seq < 0 || seq > static_cast<int>(kMaxSeq)) {
    ++malformed_;
    return;
  }

  // Version check (protocol §13).  v missing/invalid -> PARSE; v != 1 ->
  // VERSION + fault 8.  NOTE: liveness (protocol §7.3, "no VALID message
  // for 1000 ms") is granted below only after the message has passed every
  // validation step; malformed, unsupported-version and schema-invalid
  // messages never refresh last_rx_ms_.
  if (!root["v"].is<int>()) {
    EmitRespErr(seq, "PARSE", "missing or invalid v", out);
    StoreResponse(type, seq);
    return;
  }
  if (root["v"].as<int>() != kProtocolVersion) {
    RaiseFault(kFaultVersionMismatch, now_ms, out,
               "protocol version mismatch");
    EmitRespErr(seq, "VERSION", "unsupported protocol version", out);
    StoreResponse(type, seq);
    return;
  }

  // Duplicate command: replay the stored response, do not re-execute (§9).
  // A retransmission is valid communication (the laptop is demonstrably
  // alive), so it refreshes liveness.
  if (seq == last_cmd_seq_ && type == last_cmd_type_ &&
      !last_resp_line_.empty()) {
    last_rx_ms_ = now_ms;
    out.push_back(last_resp_line_);
    return;
  }

  if (std::strcmp(type, kMsgHeartbeat) == 0) {
    // No explicit response (ESP32 heartbeat is independent), but a heartbeat
    // is a valid message and counts toward liveness (protocol §4.1/§7).
    last_rx_ms_ = now_ms;
    return;
  }

  const bool known =
      (std::strcmp(type, kMsgCmdMove) == 0) ||
      (std::strcmp(type, kMsgCmdStop) == 0) ||
      (std::strcmp(type, kMsgCmdIntake) == 0) ||
      (std::strcmp(type, kMsgCmdDispense) == 0) ||
      (std::strcmp(type, kMsgCmdReset) == 0);
  if (!known) {
    // Unsupported type: answered, but it is not a valid message, so it does
    // NOT count toward laptop liveness.
    EmitRespErr(seq, "UNKNOWN_TYPE", "message type not defined", out);
    StoreResponse(type, seq);
    return;
  }

  // --- Per-type schema validation, then a shared liveness/state gate. ------
  // A message counts as valid (refreshes last_rx_ms_) only after passing its
  // schema checks.  State rejections (STARTING / LATCHED) happen AFTER
  // validation, so a schema-valid command rejected by state still proves the
  // laptop is alive, while a schema-invalid one does not.

  if (std::strcmp(type, kMsgCmdReset) == 0) {
    if (!root["scope"].is<const char*>()) {
      EmitRespErr(seq, "PARSE", "missing or invalid scope", out);
      StoreResponse(type, seq);
      return;
    }
    const char* scope = root["scope"].as<const char*>();
    if (std::strcmp(scope, "all") != 0 &&
        std::strcmp(scope, "faults") != 0) {
      EmitRespErr(seq, "RANGE", "scope must be 'faults' or 'all'", out);
      StoreResponse(type, seq);
      return;
    }
    last_rx_ms_ = now_ms;  // schema-valid command -> valid communication
    if (!CanExecuteCommand(type, seq, out)) {
      StoreResponse(type, seq);
      return;
    }
    if (std::strcmp(scope, "all") == 0) {
      faults_.clear();
      state_ = EngineState::kReady;
      EmitRespOk(seq, out);
    } else {
      ClearFaultsExceptLatches();
      EmitRespOk(seq, out);
    }
    StoreResponse(type, seq);
    return;
  }
  if (std::strcmp(type, kMsgCmdStop) == 0) {
    bool emergency = false;
    if (root["mode"].is<const char*>()) {
      const char* mode = root["mode"].as<const char*>();
      if (std::strcmp(mode, "normal") == 0) {
        emergency = false;
      } else if (std::strcmp(mode, "emergency") == 0) {
        emergency = true;
      } else {
        EmitRespErr(seq, "RANGE", "mode must be 'normal' or 'emergency'",
                    out);
        StoreResponse(type, seq);
        return;
      }
    } else if (!root["mode"].isNull() && !root["mode"].is<const char*>()) {
      EmitRespErr(seq, "PARSE", "mode must be a string", out);
      StoreResponse(type, seq);
      return;
    }
    // mode omitted -> normal (protocol §4.1)
    last_rx_ms_ = now_ms;  // schema-valid command -> valid communication
    if (!CanExecuteCommand(type, seq, out)) {
      StoreResponse(type, seq);
      return;
    }
    HandleCmdStop(emergency, seq, now_ms, out);
    StoreResponse(type, seq);
    return;
  }
  if (std::strcmp(type, kMsgCmdMove) == 0) {
    if (!root.containsKey("lin") || !root.containsKey("ang")) {
      EmitRespErr(seq, "PARSE", "missing required field lin/ang", out);
      StoreResponse(type, seq);
      return;
    }
    if (!IsNumber(root["lin"]) || !IsNumber(root["ang"])) {
      EmitRespErr(seq, "PARSE", "lin/ang must be numbers", out);
      StoreResponse(type, seq);
      return;
    }
    const double lin = root["lin"].as<double>();
    const double ang = root["ang"].as<double>();
    if (!std::isfinite(lin) || !std::isfinite(ang) || lin < -1.0 ||
        lin > 1.0 || ang < -1.0 || ang > 1.0) {
      EmitRespErr(seq, "RANGE", "lin/ang outside [-1, 1]", out);
      StoreResponse(type, seq);
      return;
    }
    last_rx_ms_ = now_ms;  // schema-valid command -> valid communication
    if (!CanExecuteCommand(type, seq, out)) {
      StoreResponse(type, seq);
      return;
    }
    HandleCmdMove(static_cast<float>(lin), static_cast<float>(ang), seq, out);
    StoreResponse(type, seq);
    return;
  }
  if (std::strcmp(type, kMsgCmdIntake) == 0) {
    if (!root["action"].is<const char*>()) {
      EmitRespErr(seq, "PARSE", "missing or invalid action", out);
      StoreResponse(type, seq);
      return;
    }
    const char* action = root["action"].as<const char*>();
    if (std::strcmp(action, "start") != 0 &&
        std::strcmp(action, "stop") != 0) {
      EmitRespErr(seq, "RANGE", "action must be 'start' or 'stop'", out);
      StoreResponse(type, seq);
      return;
    }
    last_rx_ms_ = now_ms;  // schema-valid command -> valid communication
    if (!CanExecuteCommand(type, seq, out)) {
      StoreResponse(type, seq);
      return;
    }
    HandleCmdIntake(std::strcmp(action, "start") == 0, seq, now_ms, out);
    StoreResponse(type, seq);
    return;
  }
  if (std::strcmp(type, kMsgCmdDispense) == 0) {
    if (!root["count"].is<int>()) {
      EmitRespErr(seq, "PARSE", "count must be an integer", out);
      StoreResponse(type, seq);
      return;
    }
    const int count = root["count"].as<int>();
    if (count < 1 || count > 10) {
      EmitRespErr(seq, "RANGE", "count outside [1, 10]", out);
      StoreResponse(type, seq);
      return;
    }
    last_rx_ms_ = now_ms;  // schema-valid command -> valid communication
    if (!CanExecuteCommand(type, seq, out)) {
      StoreResponse(type, seq);
      return;
    }
    HandleCmdDispense(count, seq, now_ms, out);
    StoreResponse(type, seq);
    return;
  }

  // Defensive: unreachable.
  EmitRespErr(seq, "INTERNAL", "unhandled command", out);
  StoreResponse(type, seq);
}

// ---------------------------------------------------------------------------
// Command handlers (state == kReady unless noted)
// ---------------------------------------------------------------------------

bool ProtocolEngine::CanExecuteCommand(const char* type, int seq,
                                       std::vector<std::string>& out) {
  // Startup: nothing executes until the startup checks finished (§7).
  if (state_ == EngineState::kStarting) {
    EmitRespErr(seq, "NOT_READY", "starting up: startup checks not complete",
                out);
    return false;
  }
  // Latched: only CMD_RESET (re-arm path, §7) is allowed.
  if (state_ == EngineState::kLatched &&
      std::strcmp(type, kMsgCmdReset) != 0) {
    EmitRespErr(seq, "NOT_READY",
                "latched: CMD_RESET scope=all required to re-arm", out);
    return false;
  }
  return true;
}

void ProtocolEngine::HandleCmdMove(float lin, float ang, int seq,
                                   std::vector<std::string>& out) {
  const ActResult r = hw_.SetDriveVelocity(lin, ang);
  if (r == ActResult::kOk) {
    EmitRespOk(seq, out);
  } else if (r == ActResult::kNotImplemented) {
    EmitRespErr(seq, "INTERNAL",
                "drive hardware not implemented (bring-up skeleton)", out);
  } else {
    EmitRespErr(seq, "INTERNAL", "drive control failure", out);
  }
}

void ProtocolEngine::HandleCmdStop(bool emergency, int seq, uint32_t now_ms,
                                   std::vector<std::string>& out) {
  if (emergency) {
    // Remote e-stop: same latch semantics as the physical button (§7 rule 2).
    EmitRespOk(seq, out);  // the stop itself was executed
    ApplyEmergencyStop(now_ms, out, "e-stop (remote command)");
    return;
  }
  const ActResult r = hw_.StopDrive(false);
  if (r == ActResult::kOk || r == ActResult::kNotImplemented) {
    // Safe motor state is the default on the skeleton; nothing to decelerate.
    EmitRespOk(seq, out);
  } else {
    EmitRespErr(seq, "INTERNAL", "drive stop failure", out);
  }
}

void ProtocolEngine::HandleCmdIntake(bool start, int seq, uint32_t now_ms,
                                     std::vector<std::string>& out) {
  if (start) {
    if (dispense_busy_) {
      EmitRespErr(seq, "BUSY", "dispense in progress", out);
      return;
    }
    if (intake_active_) {
      EmitRespErr(seq, "BUSY", "intake already running", out);
      return;
    }
    const ActResult r = hw_.StartIntake();
    if (r == ActResult::kOk) {
      intake_active_ = true;
      EmitRespOk(seq, out);
    } else if (r == ActResult::kNotImplemented) {
      EmitRespErr(seq, "INTERNAL",
                  "intake hardware not implemented (bring-up skeleton)", out);
    } else {
      EmitRespErr(seq, "INTERNAL", "intake start failure", out);
    }
    return;
  }
  // stop
  if (!intake_active_) {
    EmitRespOk(seq, out);  // idempotent stop
    return;
  }
  hw_.StopIntake();
  intake_active_ = false;
  // Laptop stopped the run before the verification sensor tripped: the run
  // is a failure (protocol §10 failure path).
  EmitCollectEvent("failed", now_ms, out);
  EmitRespOk(seq, out);
}

void ProtocolEngine::HandleCmdDispense(int count, int seq, uint32_t now_ms,
                                       std::vector<std::string>& out) {
  if (intake_active_) {
    EmitRespErr(seq, "BUSY", "intake running", out);
    return;
  }
  if (dispense_busy_) {
    EmitRespErr(seq, "BUSY", "dispense already in progress", out);
    return;
  }
  if (balls_count_ < count) {
    // Protocol §10: RESP_ERR + EVT_DISPENSE empty.
    EmitRespErr(seq, "INTERNAL", "storage empty", out);
    EmitDispenseEvent("empty", now_ms, out);
    return;
  }
  const ActResult r = hw_.OpenDispenseGate(count);
  if (r == ActResult::kOk) {
    dispense_busy_ = true;
    dispense_count_ = count;
    EmitRespOk(seq, out);  // EVT_DISPENSE follows when the gate finished
  } else if (r == ActResult::kNotImplemented) {
    EmitRespErr(seq, "INTERNAL",
                "dispense hardware not implemented (bring-up skeleton)", out);
  } else {
    EmitRespErr(seq, "INTERNAL", "dispense start failure", out);
  }
}

}  // namespace proto
}  // namespace ballrecovery
