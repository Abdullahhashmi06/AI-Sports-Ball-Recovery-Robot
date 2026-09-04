// Native unit tests: engine state machine, safety latches, timing, telemetry
// structure and the storage/collection interface of the protocol engine
// (docs/COMMUNICATION_PROTOCOL.md §7, §8, §10).  Run with: pio test -e native

#include <unity.h>

#include <string>

#include "protocol.h"
#include "../test_util.h"

using ballrecovery::proto::EngineState;
using ballrecovery::proto::kCommsLossTimeoutMs;
using ballrecovery::proto::kHeartbeatPeriodMs;
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

static void cmd(const std::string& json, uint32_t now) {
  g->engine.HandleLine(json, now, g->out);
}

static void MakeReady(Ctx& c) {
  c.engine.CompleteStartupChecks(c.out);
  c.out.clear();
}

// ---------------------------------------------------------------------------
// Timers and telemetry structure (protocol §8, §4.2)
// ---------------------------------------------------------------------------

void test_telemetry_and_heartbeat_cadence(void) {
  g->engine.Begin(0u, "poweron", "test-fw", g->out);
  MakeReady(*g);
  // Keep the laptop liveness fresh so no comms-loss latch interferes.
  for (uint32_t t = 1u; t <= 1000u; ++t) {
    if (t == 200u || t == 800u) {
      cmd("{\"v\":1,\"type\":\"HEARTBEAT\",\"seq\":1}", t);
    }
    g->engine.Tick(t, g->out);
  }
  TEST_ASSERT_EQUAL(20u, (unsigned)g->count("TELE"));     // every 50 ms
  TEST_ASSERT_EQUAL(2u, (unsigned)g->count("HEARTBEAT")); // 500 ms + 1000 ms
}

void test_telemetry_schema(void) {
  MakeReady(*g);
  g->engine.Tick(kTelemetryPeriodMs, g->out);
  const std::string tele = FindLine(g->out, "TELE");
  TEST_ASSERT_FALSE(tele.empty());
  DynamicJsonDocument doc = Parse(tele);
  TEST_ASSERT_EQUAL_STRING("TELE", doc["type"].as<const char*>());
  TEST_ASSERT_EQUAL(1, doc["v"].as<int>());
  TEST_ASSERT_TRUE(doc["enc"].containsKey("dl"));
  TEST_ASSERT_TRUE(doc["enc"].containsKey("dr"));
  TEST_ASSERT_EQUAL(4u, (unsigned)doc["us"].size());
  TEST_ASSERT_TRUE(doc["imu"].containsKey("gz"));
  TEST_ASSERT_TRUE(doc["mot"].containsKey("spd_r"));
  TEST_ASSERT_TRUE(doc["collect"].containsKey("state"));
  TEST_ASSERT_TRUE(doc["balls"].containsKey("count"));
  TEST_ASSERT_EQUAL(0, doc["balls"]["count"].as<int>());
  TEST_ASSERT_EQUAL(0, doc["faults"].size());
}

void test_telemetry_reports_engine_and_hardware_state(void) {
  MakeReady(*g);
  g->hw.snap.enc_dl = 12;
  g->hw.snap.enc_dr = -3;
  g->hw.snap.us[0] = 150;
  g->hw.snap.collect_trip = true;
  g->engine.OnCollectionVerified(60u, g->out);  // ignored: no intake run
  g->engine.Tick(kTelemetryPeriodMs, g->out);
  const std::string tele = FindLine(g->out, "TELE");
  DynamicJsonDocument doc = Parse(tele);
  TEST_ASSERT_EQUAL(12, doc["enc"]["dl"].as<int>());
  TEST_ASSERT_EQUAL(-3, doc["enc"]["dr"].as<int>());
  TEST_ASSERT_EQUAL(150, doc["us"][0].as<int>());
  TEST_ASSERT_EQUAL(9999, doc["us"][1].as<int>());
}

// ---------------------------------------------------------------------------
// Communication loss -> latch -> CMD_RESET scope=all re-arm (protocol §7.3)
// ---------------------------------------------------------------------------

void test_comms_loss_latches_and_heartbeat_does_not_clear(void) {
  MakeReady(*g);
  // Tick up to just below the timeout: nothing happens.
  g->engine.Tick(kCommsLossTimeoutMs - 1u, g->out);
  g->clear();

  // No valid message for >= 1000 ms -> safe stop, fault 2, latched NOT_READY.
  g->engine.Tick(kCommsLossTimeoutMs, g->out);
  TEST_ASSERT_FALSE(g->engine.IsReady());
  TEST_ASSERT_TRUE(g->engine.State() == EngineState::kLatched);
  TEST_ASSERT_TRUE(g->engine.HasFault(2));
  TEST_ASSERT_TRUE((unsigned)g->count("EVT_FAULT") >= 1u);
  const std::string fault = FindLine(g->out, "EVT_FAULT");
  DynamicJsonDocument fd = Parse(fault);
  TEST_ASSERT_EQUAL(2, fd["code"].as<int>());
  TEST_ASSERT_TRUE(g->hw.last_stop_emergency);  // emergency stop executed

  // A returning heartbeat does NOT clear the latch (protocol §7 rule 3).
  g->clear();
  cmd("{\"v\":1,\"type\":\"HEARTBEAT\",\"seq\":99}",
      kCommsLossTimeoutMs + 1u);
  TEST_ASSERT_EQUAL(0, (int)g->out.size());
  TEST_ASSERT_TRUE(g->engine.State() == EngineState::kLatched);

  // Motion is rejected while latched...
  g->clear();
  cmd("{\"v\":1,\"type\":\"CMD_MOVE\",\"seq\":1,\"lin\":0.1,\"ang\":0.0}",
      kCommsLossTimeoutMs + 2u);
  TEST_ASSERT_TRUE(HasRespErr(g->out, "NOT_READY"));

  // ...and scope=faults is not enough to re-arm...
  g->clear();
  cmd("{\"v\":1,\"type\":\"CMD_RESET\",\"seq\":2,\"scope\":\"faults\"}",
      kCommsLossTimeoutMs + 3u);
  TEST_ASSERT_TRUE((unsigned)g->count("RESP_OK") == 1u);
  TEST_ASSERT_TRUE(g->engine.State() == EngineState::kLatched);
  g->clear();
  cmd("{\"v\":1,\"type\":\"CMD_MOVE\",\"seq\":3,\"lin\":0.1,\"ang\":0.0}",
      kCommsLossTimeoutMs + 4u);
  TEST_ASSERT_TRUE(HasRespErr(g->out, "NOT_READY"));

  // ...only CMD_RESET scope=all re-arms.
  g->clear();
  cmd("{\"v\":1,\"type\":\"CMD_RESET\",\"seq\":4,\"scope\":\"all\"}",
      kCommsLossTimeoutMs + 5u);
  TEST_ASSERT_TRUE((unsigned)g->count("RESP_OK") == 1u);
  TEST_ASSERT_TRUE(g->engine.IsReady());
  TEST_ASSERT_FALSE(g->engine.HasFault(2));
  g->clear();
  cmd("{\"v\":1,\"type\":\"CMD_MOVE\",\"seq\":5,\"lin\":0.1,\"ang\":0.0}",
      kCommsLossTimeoutMs + 6u);
  TEST_ASSERT_TRUE((unsigned)g->count("RESP_OK") == 1u);
  TEST_ASSERT_EQUAL(1, g->hw.move_calls);
}

// ---------------------------------------------------------------------------
// Emergency stop (protocol §7 rule 2 / flow G)
// ---------------------------------------------------------------------------

void test_emergency_stop_latches_and_requires_reset_all(void) {
  MakeReady(*g);
  cmd("{\"v\":1,\"type\":\"CMD_STOP\",\"seq\":10,\"mode\":\"emergency\"}",
      100u);
  TEST_ASSERT_TRUE((unsigned)g->count("RESP_OK") == 1u);   // the stop was executed
  TEST_ASSERT_TRUE((unsigned)g->count("EVT_FAULT") == 1u); // fault 1 reported
  TEST_ASSERT_TRUE(g->engine.State() == EngineState::kLatched);
  TEST_ASSERT_TRUE(g->engine.HasFault(1));

  g->clear();
  cmd("{\"v\":1,\"type\":\"CMD_MOVE\",\"seq\":11,\"lin\":0.1,\"ang\":0.0}",
      110u);
  TEST_ASSERT_TRUE(HasRespErr(g->out, "NOT_READY"));

  g->clear();
  cmd("{\"v\":1,\"type\":\"CMD_RESET\",\"seq\":12,\"scope\":\"all\"}", 120u);
  TEST_ASSERT_TRUE((unsigned)g->count("RESP_OK") == 1u);
  TEST_ASSERT_TRUE(g->engine.IsReady());
  TEST_ASSERT_FALSE(g->engine.HasFault(1));
}

void test_local_emergency_stop_triggers_latch(void) {
  MakeReady(*g);
  g->engine.TriggerEmergencyStop(200u, g->out);
  TEST_ASSERT_TRUE(g->engine.State() == EngineState::kLatched);
  TEST_ASSERT_TRUE(g->engine.HasFault(1));
}

// ---------------------------------------------------------------------------
// Collection + storage (protocol §10)
// ---------------------------------------------------------------------------

void test_intake_run_collect_and_verify(void) {
  MakeReady(*g);
  cmd("{\"v\":1,\"type\":\"CMD_INTAKE\",\"seq\":20,\"action\":\"start\"}",
      100u);
  TEST_ASSERT_TRUE((unsigned)g->count("RESP_OK") == 1u);
  TEST_ASSERT_EQUAL(1, g->hw.intake_start_calls);

  // Verification sensor trips -> auto-stop, count +1, EVT_COLLECT collected.
  g->clear();
  g->engine.OnCollectionVerified(150u, g->out);
  TEST_ASSERT_EQUAL(1, g->hw.intake_stop_calls);
  TEST_ASSERT_EQUAL(1, g->engine.StorageCount());
  const std::string evt = FindLine(g->out, "EVT_COLLECT");
  DynamicJsonDocument doc = Parse(evt);
  TEST_ASSERT_EQUAL_STRING("collected", doc["status"].as<const char*>());
  TEST_ASSERT_EQUAL(1, doc["balls"]["count"].as<int>());

  // Count visible in TELE.
  g->clear();
  g->engine.Tick(200u, g->out);
  DynamicJsonDocument td = Parse(FindLine(g->out, "TELE"));
  TEST_ASSERT_EQUAL(1, td["balls"]["count"].as<int>());
}

void test_intake_stopped_without_ball_is_failed_run(void) {
  MakeReady(*g);
  cmd("{\"v\":1,\"type\":\"CMD_INTAKE\",\"seq\":21,\"action\":\"start\"}",
      100u);
  g->clear();
  cmd("{\"v\":1,\"type\":\"CMD_INTAKE\",\"seq\":22,\"action\":\"stop\"}",
      150u);
  TEST_ASSERT_TRUE((unsigned)g->count("RESP_OK") == 1u);
  const std::string evt = FindLine(g->out, "EVT_COLLECT");
  DynamicJsonDocument doc = Parse(evt);
  TEST_ASSERT_EQUAL_STRING("failed", doc["status"].as<const char*>());
  TEST_ASSERT_EQUAL(0, g->engine.StorageCount());
}

void test_dispense_empty_when_no_balls(void) {
  MakeReady(*g);
  cmd("{\"v\":1,\"type\":\"CMD_DISPENSE\",\"seq\":30,\"count\":1}", 100u);
  TEST_ASSERT_TRUE(HasRespErr(g->out, "INTERNAL"));  // "storage empty"
  TEST_ASSERT_TRUE((unsigned)g->count("EVT_DISPENSE") == 1u);
  DynamicJsonDocument doc = Parse(FindLine(g->out, "EVT_DISPENSE"));
  TEST_ASSERT_EQUAL_STRING("empty", doc["status"].as<const char*>());
  TEST_ASSERT_EQUAL(0, g->hw.dispense_calls);
}

void test_dispense_ok_decrements_storage(void) {
  MakeReady(*g);
  // Collect one ball first.
  cmd("{\"v\":1,\"type\":\"CMD_INTAKE\",\"seq\":31,\"action\":\"start\"}",
      100u);
  g->engine.OnCollectionVerified(150u, g->out);
  g->clear();

  cmd("{\"v\":1,\"type\":\"CMD_DISPENSE\",\"seq\":32,\"count\":1}", 200u);
  TEST_ASSERT_TRUE((unsigned)g->count("RESP_OK") == 1u);
  TEST_ASSERT_EQUAL(1, g->hw.dispense_calls);
  TEST_ASSERT_EQUAL(1, g->hw.last_dispense_count);

  // Async gate completion reports EVT_DISPENSE ok and decrements the count.
  g->clear();
  g->engine.OnDispenseResult("ok", 300u, g->out);
  TEST_ASSERT_EQUAL(0, g->engine.StorageCount());
  DynamicJsonDocument doc = Parse(FindLine(g->out, "EVT_DISPENSE"));
  TEST_ASSERT_EQUAL_STRING("ok", doc["status"].as<const char*>());
  TEST_ASSERT_EQUAL(0, doc["balls"]["count"].as<int>());
}

void test_intake_dispense_mutual_exclusion(void) {
  MakeReady(*g);
  // Dispense while an intake run is active -> BUSY.
  cmd("{\"v\":1,\"type\":\"CMD_INTAKE\",\"seq\":40,\"action\":\"start\"}",
      100u);
  g->clear();
  cmd("{\"v\":1,\"type\":\"CMD_DISPENSE\",\"seq\":41,\"count\":1}", 110u);
  TEST_ASSERT_TRUE(HasRespErr(g->out, "BUSY"));
}

// ---------------------------------------------------------------------------
// Fault clearing (protocol §4.2: faults are latched until cleared)
// ---------------------------------------------------------------------------

void test_scope_faults_clears_non_latching_faults(void) {
  MakeReady(*g);
  g->engine.RaiseFault(3, 100u, g->out, "test hard stop");
  g->engine.RaiseFault(4, 100u, g->out, "test us fault");
  TEST_ASSERT_TRUE(g->engine.HasFault(3));
  TEST_ASSERT_TRUE(g->engine.HasFault(4));
  TEST_ASSERT_TRUE(g->engine.IsReady());  // these faults do not latch NOT_READY

  g->clear();
  cmd("{\"v\":1,\"type\":\"CMD_RESET\",\"seq\":50,\"scope\":\"faults\"}",
      200u);
  TEST_ASSERT_TRUE((unsigned)g->count("RESP_OK") == 1u);
  TEST_ASSERT_FALSE(g->engine.HasFault(3));
  TEST_ASSERT_FALSE(g->engine.HasFault(4));
  TEST_ASSERT_TRUE(g->engine.IsReady());
}

void test_faults_listed_in_telemetry(void) {
  MakeReady(*g);
  g->engine.RaiseFault(9, 100u, g->out, "dispense mechanism");
  g->clear();
  g->engine.Tick(150u, g->out);
  DynamicJsonDocument td = Parse(FindLine(g->out, "TELE"));
  TEST_ASSERT_EQUAL(1u, (unsigned)td["faults"].size());
  TEST_ASSERT_EQUAL(9, td["faults"][0].as<int>());
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
  RUN_WITH_FIXTURE(test_telemetry_and_heartbeat_cadence);
  RUN_WITH_FIXTURE(test_telemetry_schema);
  RUN_WITH_FIXTURE(test_telemetry_reports_engine_and_hardware_state);
  RUN_WITH_FIXTURE(test_comms_loss_latches_and_heartbeat_does_not_clear);
  RUN_WITH_FIXTURE(test_emergency_stop_latches_and_requires_reset_all);
  RUN_WITH_FIXTURE(test_local_emergency_stop_triggers_latch);
  RUN_WITH_FIXTURE(test_intake_run_collect_and_verify);
  RUN_WITH_FIXTURE(test_intake_stopped_without_ball_is_failed_run);
  RUN_WITH_FIXTURE(test_dispense_empty_when_no_balls);
  RUN_WITH_FIXTURE(test_dispense_ok_decrements_storage);
  RUN_WITH_FIXTURE(test_intake_dispense_mutual_exclusion);
  RUN_WITH_FIXTURE(test_scope_faults_clears_non_latching_faults);
  RUN_WITH_FIXTURE(test_faults_listed_in_telemetry);
  return UNITY_END();
}

#undef RUN_WITH_FIXTURE
