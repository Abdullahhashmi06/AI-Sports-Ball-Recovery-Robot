// Additional native unit tests for the protocol engine (docs/
// COMMUNICATION_PROTOCOL.md): storage capacity/full behaviour, dispense
// failure/empty events, BUSY rejection, sequence wraparound, oversized-frame
// rejection, EVT_BOOT semantics, CMD_RESET scope=all clearing both latches,
// and TELE.imu carrying no yaw.  Run with:  pio test -e native

#include <unity.h>

#include <string>

#include "protocol.h"
#include "../test_util.h"

using ballrecovery::proto::EngineState;
using ballrecovery::proto::kMaxLineBytes;
using ballrecovery::proto::kTelemetryPeriodMs;
using ballrecovery::proto::testutil::Ctx;
using ballrecovery::proto::testutil::FindLine;
using ballrecovery::proto::testutil::HasRespErr;
using ballrecovery::proto::testutil::Parse;

static Ctx* g = nullptr;

void setUp(void) { g = new Ctx(); }
void tearDown(void) {
  delete g;
  g = nullptr;
}

namespace {

void MakeReady(Ctx& c) {
  c.engine.CompleteStartupChecks(c.out);
  c.out.clear();
}

std::string Cmd(int seq, const char* type, const char* fields) {
  std::string line = std::string("{\"v\":1,\"type\":\"") + type + "\",\"seq\":" +
                     std::to_string(seq);
  if (fields != nullptr && fields[0] != '\0') line += std::string(",") + fields;
  line += "}";
  return line;
}

std::string IntakeStart(int seq) {
  return Cmd(seq, "CMD_INTAKE", "\"action\":\"start\"");
}

std::string IntakeStop(int seq) {
  return Cmd(seq, "CMD_INTAKE", "\"action\":\"stop\"");
}

std::string Dispense(int seq, int count) {
  return Cmd(seq, "CMD_DISPENSE",
             (std::string("\"count\":") + std::to_string(count)).c_str());
}

void CollectOne(Ctx& c, int seq, uint32_t now) {
  c.engine.HandleLine(IntakeStart(seq), now, c.out);
  c.out.clear();
  c.engine.OnCollectionVerified(now + 1u, c.out);
  c.out.clear();
}

}  // namespace

// ---------------------------------------------------------------------------
// Oversized frames and EVT_BOOT
// ---------------------------------------------------------------------------

void test_oversized_line_is_rejected_before_parsing(void) {
  // > 512 bytes but otherwise a perfectly valid JSON message: the length
  // guard (protocol section 2) must drop it as malformed.
  std::string line = "{\"v\":1,\"type\":\"HEARTBEAT\",\"seq\":1,\"pad\":\"";
  line.append(600, 'a');
  line += "\"}";
  TEST_ASSERT_TRUE(line.size() > kMaxLineBytes);
  g->engine.HandleLine(line, 10u, g->out);
  TEST_ASSERT_EQUAL(0, (int)g->out.size());
  TEST_ASSERT_EQUAL(1u, g->engine.MalformedLines());
}

void test_boot_event_semantics(void) {
  const char* reasons[] = {"poweron", "watchdog", "reset"};
  for (int r = 0; r < 3; ++r) {
    Ctx c;
    c.engine.Begin(1000u, reasons[r], "fw-label", c.out);
    TEST_ASSERT_EQUAL(1, (int)c.out.size());  // EVT_BOOT only: no TELE/HB yet
    TEST_ASSERT_EQUAL(0u, (unsigned)c.count("TELE"));
    TEST_ASSERT_EQUAL(0u, (unsigned)c.count("HEARTBEAT"));
    DynamicJsonDocument doc = Parse(c.out[0]);
    TEST_ASSERT_EQUAL_STRING("EVT_BOOT", doc["type"].as<const char*>());
    TEST_ASSERT_EQUAL_STRING(reasons[r], doc["reason"].as<const char*>());
    TEST_ASSERT_EQUAL_STRING("fw-label", doc["fw"].as<const char*>());
    TEST_ASSERT_EQUAL(1, doc["v"].as<int>());
    TEST_ASSERT_EQUAL(1, doc["seq"].as<int>());
  }
}

// ---------------------------------------------------------------------------
// Storage capacity / full behaviour (protocol sections 4.2, 10)
// ---------------------------------------------------------------------------

void test_full_bin_caps_storage_and_reports_full(void) {
  Ctx c(2);  // small capacity so "full" is reachable in a unit test
  MakeReady(c);

  CollectOne(c, 10, 100u);
  CollectOne(c, 11, 200u);
  TEST_ASSERT_EQUAL(2, c.engine.StorageCount());
  TEST_ASSERT_TRUE(c.engine.StorageFull());

  c.engine.Tick(300u, c.out);
  DynamicJsonDocument td = Parse(FindLine(c.out, "TELE"));
  TEST_ASSERT_EQUAL(2, td["balls"]["count"].as<int>());
  TEST_ASSERT_TRUE(td["balls"]["full"].as<bool>());
  c.out.clear();

  // A third verified ball cannot be stored: the count stays capped.
  c.engine.HandleLine(IntakeStart(12), 400u, c.out);
  TEST_ASSERT_TRUE(c.count("RESP_OK") == 1u);
  c.out.clear();
  c.engine.OnCollectionVerified(450u, c.out);
  TEST_ASSERT_EQUAL(2, c.engine.StorageCount());
  DynamicJsonDocument evt = Parse(FindLine(c.out, "EVT_COLLECT"));
  TEST_ASSERT_EQUAL_STRING("collected", evt["status"].as<const char*>());
  TEST_ASSERT_EQUAL(2, evt["balls"]["count"].as<int>());
  TEST_ASSERT_TRUE(evt["balls"]["full"].as<bool>());
}

void test_dispense_failed_raises_fault_9(void) {
  Ctx c;
  MakeReady(c);
  CollectOne(c, 20, 100u);
  TEST_ASSERT_EQUAL(1, c.engine.StorageCount());

  c.engine.HandleLine(Dispense(21, 1), 200u, c.out);
  TEST_ASSERT_TRUE(c.count("RESP_OK") == 1u);  // gate accepted the request
  c.out.clear();
  c.engine.OnDispenseResult("failed", 300u, c.out);
  TEST_ASSERT_TRUE(c.engine.HasFault(9));
  TEST_ASSERT_EQUAL(1, c.engine.StorageCount());  // unchanged on failure
  DynamicJsonDocument evt = Parse(FindLine(c.out, "EVT_DISPENSE"));
  TEST_ASSERT_EQUAL_STRING("failed", evt["status"].as<const char*>());
}

void test_dispense_empty_result_event(void) {
  Ctx c;
  MakeReady(c);
  CollectOne(c, 30, 100u);
  c.engine.HandleLine(Dispense(31, 1), 200u, c.out);  // gate becomes busy
  TEST_ASSERT_TRUE(c.count("RESP_OK") == 1u);
  c.out.clear();
  c.engine.OnDispenseResult("empty", 300u, c.out);
  TEST_ASSERT_FALSE(c.engine.HasFault(9));       // "empty" is not a fault
  TEST_ASSERT_EQUAL(1, c.engine.StorageCount());  // nothing left the bin
  DynamicJsonDocument evt = Parse(FindLine(c.out, "EVT_DISPENSE"));
  TEST_ASSERT_EQUAL_STRING("empty", evt["status"].as<const char*>());
}

void test_busy_rejections(void) {
  Ctx c;
  MakeReady(c);

  // Restarting an active intake run -> BUSY.
  c.engine.HandleLine(IntakeStart(40), 100u, c.out);
  TEST_ASSERT_TRUE(c.count("RESP_OK") == 1u);
  c.out.clear();
  c.engine.HandleLine(IntakeStart(41), 110u, c.out);
  TEST_ASSERT_TRUE(HasRespErr(c.out, "BUSY"));
  c.out.clear();

  // Dispensing while the intake is running -> BUSY.
  c.engine.HandleLine(Dispense(42, 1), 120u, c.out);
  TEST_ASSERT_TRUE(HasRespErr(c.out, "BUSY"));
  c.out.clear();

  // Stop the run, restart it: BUSY clears.
  c.engine.HandleLine(IntakeStop(43), 130u, c.out);
  TEST_ASSERT_TRUE(c.count("RESP_OK") == 1u);
  c.out.clear();
  c.engine.HandleLine(IntakeStart(44), 140u, c.out);
  TEST_ASSERT_TRUE(c.count("RESP_OK") == 1u);
  c.out.clear();
  c.engine.HandleLine(IntakeStop(45), 150u, c.out);
  c.out.clear();

  // Dispensing while a dispense is already in progress -> BUSY.
  CollectOne(c, 46, 200u);
  c.engine.HandleLine(Dispense(47, 1), 250u, c.out);
  TEST_ASSERT_TRUE(c.count("RESP_OK") == 1u);
  c.out.clear();
  c.engine.HandleLine(Dispense(48, 1), 260u, c.out);
  TEST_ASSERT_TRUE(HasRespErr(c.out, "BUSY"));
  c.engine.OnDispenseResult("ok", 300u, c.out);  // finish the first dispense
}

// ---------------------------------------------------------------------------
// Sequence wraparound at 65535 (protocol section 9)
// ---------------------------------------------------------------------------

void test_sequence_wraps_at_65535(void) {
  Ctx c;
  // Drive enough periodic emissions (TELE + heartbeats) to pass 65 535.
  // Intentionally the longest test in the suite (~66 000 engine ticks).
  uint32_t now = 0u;
  for (int i = 0; i < 66000; ++i) {
    now += 50u;
    c.engine.Tick(now, c.out);
  }
  TEST_ASSERT_TRUE(c.out.size() > 65536u);

  // Find the wrap: an emission numbered 65535 directly followed by one
  // numbered 0, then 1 again.
  size_t wrap = c.out.size();
  for (size_t i = 0; i + 1 < c.out.size(); ++i) {
    if (c.out[i].find("\"seq\":65535") != std::string::npos &&
        c.out[i + 1].find("\"seq\":0") != std::string::npos) {
      wrap = i;
      break;
    }
  }
  TEST_ASSERT_TRUE(wrap < c.out.size() - 2u);
  if (wrap < c.out.size() - 2u) {
    DynamicJsonDocument a = Parse(c.out[wrap]);
    DynamicJsonDocument b = Parse(c.out[wrap + 1]);
    DynamicJsonDocument d = Parse(c.out[wrap + 2]);
    TEST_ASSERT_EQUAL(65535, a["seq"].as<int>());
    TEST_ASSERT_EQUAL(0, b["seq"].as<int>());
    TEST_ASSERT_EQUAL(1, d["seq"].as<int>());
  }
}

// ---------------------------------------------------------------------------
// CMD_RESET scope=all clears both relevant latches (protocol section 7)
// ---------------------------------------------------------------------------

void test_reset_all_clears_estop_and_comms_faults(void) {
  Ctx c;
  MakeReady(c);

  // E-stop latch (fault 1).
  c.engine.HandleLine(Cmd(50, "CMD_STOP", "\"mode\":\"emergency\""), 100u,
                      c.out);
  TEST_ASSERT_TRUE(c.engine.State() == EngineState::kLatched);
  c.out.clear();
  // Simulated comms-loss fault on top (fault 2).
  c.engine.RaiseFault(2, 150u, c.out, "simulated comms loss");
  TEST_ASSERT_TRUE(c.engine.HasFault(1));
  TEST_ASSERT_TRUE(c.engine.HasFault(2));
  c.out.clear();

  // scope=faults keeps the latches.
  c.engine.HandleLine(Cmd(51, "CMD_RESET", "\"scope\":\"faults\""), 200u,
                      c.out);
  TEST_ASSERT_TRUE(c.engine.HasFault(1));
  TEST_ASSERT_TRUE(c.engine.HasFault(2));
  TEST_ASSERT_TRUE(c.engine.State() == EngineState::kLatched);
  c.out.clear();

  // scope=all clears both and re-arms.
  c.engine.HandleLine(Cmd(52, "CMD_RESET", "\"scope\":\"all\""), 210u, c.out);
  TEST_ASSERT_TRUE(c.engine.IsReady());
  TEST_ASSERT_FALSE(c.engine.HasFault(1));
  TEST_ASSERT_FALSE(c.engine.HasFault(2));
}

// ---------------------------------------------------------------------------
// TELE must not contain the optional imu.yaw (laptop-side fusion default)
// ---------------------------------------------------------------------------

void test_telemetry_has_no_imu_yaw(void) {
  MakeReady(*g);
  g->engine.Tick(kTelemetryPeriodMs, g->out);
  DynamicJsonDocument td = Parse(FindLine(g->out, "TELE"));
  TEST_ASSERT_FALSE(td["imu"].containsKey("yaw"));
}

// ---------------------------------------------------------------------------
// Runner.  PlatformIO's native env builds ONE binary per test_*/ group, and
// Unity's plain runner does not call setUp/tearDown itself, so each test is
// wrapped explicitly here.
// ---------------------------------------------------------------------------

#define RUN_WITH_FIXTURE(fn) \
  do { \
    setUp(); \
    RUN_TEST(fn); \
    tearDown(); \
  } while (0)

int main(void) {
  UNITY_BEGIN();
  RUN_WITH_FIXTURE(test_oversized_line_is_rejected_before_parsing);
  RUN_WITH_FIXTURE(test_boot_event_semantics);
  RUN_WITH_FIXTURE(test_full_bin_caps_storage_and_reports_full);
  RUN_WITH_FIXTURE(test_dispense_failed_raises_fault_9);
  RUN_WITH_FIXTURE(test_dispense_empty_result_event);
  RUN_WITH_FIXTURE(test_busy_rejections);
  RUN_WITH_FIXTURE(test_sequence_wraps_at_65535);
  RUN_WITH_FIXTURE(test_reset_all_clears_estop_and_comms_faults);
  RUN_WITH_FIXTURE(test_telemetry_has_no_imu_yaw);
  return UNITY_END();
}

#undef RUN_WITH_FIXTURE
