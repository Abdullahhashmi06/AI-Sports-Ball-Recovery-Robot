// Native unit tests: line parsing, header validation, schema checks and
// duplicate suppression of the protocol engine (docs/COMMUNICATION_PROTOCOL.md
// §2, §3, §9).  Run with:  pio test -e native

#include <unity.h>

#include <cstring>
#include <string>

#include "protocol.h"
#include "../test_util.h"

using ballrecovery::proto::EngineState;
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

// ---------------------------------------------------------------------------
// Boot event
// ---------------------------------------------------------------------------

void test_boot_event_emitted_with_reason_and_fw(void) {
  g->engine.Begin(1000u, "poweron", "test-fw", g->out);
  TEST_ASSERT_EQUAL(1, (int)g->out.size());
  DynamicJsonDocument doc = Parse(g->out[0]);
  TEST_ASSERT_EQUAL(1, doc["v"].as<int>());
  TEST_ASSERT_EQUAL_STRING("EVT_BOOT", doc["type"].as<const char*>());
  TEST_ASSERT_EQUAL_STRING("poweron", doc["reason"].as<const char*>());
  TEST_ASSERT_EQUAL_STRING("test-fw", doc["fw"].as<const char*>());
  TEST_ASSERT_EQUAL(1, doc["seq"].as<int>());
}

void test_starting_until_startup_checks_complete(void) {
  TEST_ASSERT_TRUE(g->engine.State() == EngineState::kStarting);
  TEST_ASSERT_FALSE(g->engine.IsReady());
  TEST_ASSERT_TRUE(g->engine.CompleteStartupChecks(g->out));
  TEST_ASSERT_TRUE(g->engine.IsReady());
}

// ---------------------------------------------------------------------------
// Malformed / header handling (protocol §2: discard + count, NO RESP_ERR)
// ---------------------------------------------------------------------------

void test_completely_malformed_lines_are_discarded(void) {
  cmd("this is not json {{{", 10u);
  cmd("[1, 2, 3]", 10u);
  cmd("\"just a string\"", 10u);
  TEST_ASSERT_EQUAL(0, (int)g->out.size());
  TEST_ASSERT_EQUAL(3u, g->engine.MalformedLines());
}

void test_unrecoverable_header_is_discarded(void) {
  cmd("{\"v\":1,\"seq\":5}", 10u);                      // no type
  cmd("{\"v\":1,\"type\":\"CMD_MOVE\"}", 10u);          // no seq
  cmd("{\"v\":1,\"type\":5,\"seq\":5}", 10u);           // type not a string
  cmd("{\"v\":1,\"type\":\"CMD_MOVE\",\"seq\":70000}", 10u);  // seq oob
  TEST_ASSERT_EQUAL(0, (int)g->out.size());
  TEST_ASSERT_EQUAL(4u, g->engine.MalformedLines());
}

// ---------------------------------------------------------------------------
// Version handling (protocol §13)
// ---------------------------------------------------------------------------

void test_missing_v_answers_parse(void) {
  cmd("{\"type\":\"CMD_STOP\",\"seq\":3,\"mode\":\"normal\"}", 10u);
  TEST_ASSERT_TRUE(HasRespErr(g->out, "PARSE"));
}

void test_version_mismatch_answers_version_and_raises_fault_8(void) {
  cmd("{\"v\":2,\"type\":\"HEARTBEAT\",\"seq\":4}", 10u);
  TEST_ASSERT_TRUE(HasRespErr(g->out, "VERSION"));
  const std::string fault = FindLine(g->out, "EVT_FAULT");
  TEST_ASSERT_FALSE(fault.empty());
  DynamicJsonDocument doc = Parse(fault);
  TEST_ASSERT_EQUAL(8, doc["code"].as<int>());
  // The fault is latched and visible in the next TELE.
  g->clear();
  g->engine.Tick(60u, g->out);
  const std::string tele = FindLine(g->out, "TELE");
  DynamicJsonDocument td = Parse(tele);
  TEST_ASSERT_EQUAL(1u, (unsigned)td["faults"].size());
  TEST_ASSERT_EQUAL(8, td["faults"][0].as<int>());
}

// ---------------------------------------------------------------------------
// Unknown / invalid content (protocol §3: valid header -> matching RESP_ERR)
// ---------------------------------------------------------------------------

void test_unknown_type_answers_unknown_type(void) {
  cmd("{\"v\":1,\"type\":\"CMD_FLY\",\"seq\":4}", 10u);
  TEST_ASSERT_TRUE(HasRespErr(g->out, "UNKNOWN_TYPE"));
}

void test_esp32_own_message_types_are_unknown_to_the_esp32(void) {
  cmd("{\"v\":1,\"type\":\"TELE\",\"seq\":4}", 10u);
  TEST_ASSERT_TRUE(HasRespErr(g->out, "UNKNOWN_TYPE"));
}

// ---------------------------------------------------------------------------
// Field validation
// ---------------------------------------------------------------------------

static void MakeReady(Ctx& c) {
  c.engine.CompleteStartupChecks(c.out);
  c.out.clear();
}

void test_cmd_move_field_validation(void) {
  MakeReady(*g);
  cmd("{\"v\":1,\"type\":\"CMD_MOVE\",\"seq\":10,\"lin\":0.5}", 10u);
  TEST_ASSERT_TRUE(HasRespErr(g->out, "PARSE"));  // missing ang
  g->clear();
  cmd("{\"v\":1,\"type\":\"CMD_MOVE\",\"seq\":11,\"ang\":0.5}", 10u);
  TEST_ASSERT_TRUE(HasRespErr(g->out, "PARSE"));  // missing lin
  g->clear();
  cmd("{\"v\":1,\"type\":\"CMD_MOVE\",\"seq\":12,\"lin\":\"fast\",\"ang\":0.0}",
      10u);
  TEST_ASSERT_TRUE(HasRespErr(g->out, "PARSE"));  // wrong type
  g->clear();
  cmd("{\"v\":1,\"type\":\"CMD_MOVE\",\"seq\":13,\"lin\":1.5,\"ang\":0.0}",
      10u);
  TEST_ASSERT_TRUE(HasRespErr(g->out, "RANGE"));  // out of range
  g->clear();
  cmd("{\"v\":1,\"type\":\"CMD_MOVE\",\"seq\":14,\"lin\":0.0,\"ang\":-1.2}",
      10u);
  TEST_ASSERT_TRUE(HasRespErr(g->out, "RANGE"));
  g->clear();
  cmd("{\"v\":1,\"type\":\"CMD_MOVE\",\"seq\":15,\"lin\":0.5,\"ang\":-0.25}",
      10u);
  TEST_ASSERT_TRUE((unsigned)g->count("RESP_OK") == 1u);
  TEST_ASSERT_EQUAL(1, g->hw.move_calls);
  TEST_ASSERT_FLOAT_WITHIN(1e-6f, 0.5f, g->hw.last_lin);
  TEST_ASSERT_FLOAT_WITHIN(1e-6f, -0.25f, g->hw.last_ang);
}

void test_cmd_stop_mode_validation_and_default(void) {
  MakeReady(*g);
  cmd("{\"v\":1,\"type\":\"CMD_STOP\",\"seq\":20,\"mode\":\"panic\"}", 10u);
  TEST_ASSERT_TRUE(HasRespErr(g->out, "RANGE"));
  g->clear();
  cmd("{\"v\":1,\"type\":\"CMD_STOP\",\"seq\":21,\"mode\":123}", 10u);
  TEST_ASSERT_TRUE(HasRespErr(g->out, "PARSE"));
  g->clear();
  // mode omitted -> defaults to normal (protocol §4.1) and is executed.
  cmd("{\"v\":1,\"type\":\"CMD_STOP\",\"seq\":22}", 10u);
  TEST_ASSERT_TRUE((unsigned)g->count("RESP_OK") == 1u);
  TEST_ASSERT_EQUAL(1, g->hw.stop_calls);
  TEST_ASSERT_FALSE(g->hw.last_stop_emergency);
}

void test_cmd_reset_and_dispense_field_validation(void) {
  MakeReady(*g);
  cmd("{\"v\":1,\"type\":\"CMD_RESET\",\"seq\":30,\"scope\":\"x\"}", 10u);
  TEST_ASSERT_TRUE(HasRespErr(g->out, "RANGE"));
  g->clear();
  cmd("{\"v\":1,\"type\":\"CMD_RESET\",\"seq\":31}", 10u);
  TEST_ASSERT_TRUE(HasRespErr(g->out, "PARSE"));  // scope required
  g->clear();
  cmd("{\"v\":1,\"type\":\"CMD_INTAKE\",\"seq\":32,\"action\":\"spin\"}", 10u);
  TEST_ASSERT_TRUE(HasRespErr(g->out, "RANGE"));
  g->clear();
  cmd("{\"v\":1,\"type\":\"CMD_DISPENSE\",\"seq\":33,\"count\":0}", 10u);
  TEST_ASSERT_TRUE(HasRespErr(g->out, "RANGE"));
  g->clear();
  cmd("{\"v\":1,\"type\":\"CMD_DISPENSE\",\"seq\":34,\"count\":1.5}", 10u);
  TEST_ASSERT_TRUE(HasRespErr(g->out, "PARSE"));
  g->clear();
  cmd("{\"v\":1,\"type\":\"CMD_DISPENSE\",\"seq\":35}", 10u);
  TEST_ASSERT_TRUE(HasRespErr(g->out, "PARSE"));
}

void test_not_ready_before_startup_checks(void) {
  // Startup checks NOT complete: every command is rejected (protocol §7).
  cmd("{\"v\":1,\"type\":\"CMD_MOVE\",\"seq\":40,\"lin\":0.1,\"ang\":0.0}",
      10u);
  TEST_ASSERT_TRUE(HasRespErr(g->out, "NOT_READY"));
  g->clear();
  cmd("{\"v\":1,\"type\":\"CMD_RESET\",\"seq\":41,\"scope\":\"all\"}", 10u);
  TEST_ASSERT_TRUE(HasRespErr(g->out, "NOT_READY"));
  TEST_ASSERT_EQUAL(0, g->hw.move_calls);
}

// ---------------------------------------------------------------------------
// Duplicate suppression (protocol §9)
// ---------------------------------------------------------------------------

void test_duplicate_command_replays_stored_response_without_reexecute(void) {
  MakeReady(*g);
  cmd("{\"v\":1,\"type\":\"CMD_STOP\",\"seq\":50,\"mode\":\"normal\"}", 10u);
  TEST_ASSERT_TRUE((unsigned)g->count("RESP_OK") == 1u);
  TEST_ASSERT_EQUAL(1, g->hw.stop_calls);

  // Same seq + same type again (laptop retransmission): stored response is
  // replayed and the command is NOT re-executed.
  g->clear();
  cmd("{\"v\":1,\"type\":\"CMD_STOP\",\"seq\":50,\"mode\":\"normal\"}", 20u);
  TEST_ASSERT_TRUE((unsigned)g->count("RESP_OK") == 1u);
  TEST_ASSERT_EQUAL(1, g->hw.stop_calls);

  // A new seq is a new command and executes normally.
  g->clear();
  cmd("{\"v\":1,\"type\":\"CMD_STOP\",\"seq\":51,\"mode\":\"normal\"}", 30u);
  TEST_ASSERT_EQUAL(2, g->hw.stop_calls);
}

void test_heartbeat_never_triggers_a_response_or_duplicate_store(void) {
  MakeReady(*g);
  cmd("{\"v\":1,\"type\":\"HEARTBEAT\",\"seq\":60}", 10u);
  cmd("{\"v\":1,\"type\":\"HEARTBEAT\",\"seq\":60}", 20u);
  TEST_ASSERT_EQUAL(0, (int)g->out.size());
}

// ---------------------------------------------------------------------------
// Not-implemented hardware is reported honestly (no fabrication)
// ---------------------------------------------------------------------------

void test_not_implemented_drive_answers_internal(void) {
  g->hw.move_result = ballrecovery::proto::ActResult::kNotImplemented;
  MakeReady(*g);
  cmd("{\"v\":1,\"type\":\"CMD_MOVE\",\"seq\":70,\"lin\":0.2,\"ang\":0.0}",
      10u);
  TEST_ASSERT_TRUE(HasRespErr(g->out, "INTERNAL"));
  TEST_ASSERT_EQUAL(1, g->hw.move_calls);
}

// ---------------------------------------------------------------------------
// RESP payload shape
// ---------------------------------------------------------------------------

void test_resp_ok_echoes_ack_seq(void) {
  MakeReady(*g);
  cmd("{\"v\":1,\"type\":\"CMD_RESET\",\"seq\":77,\"scope\":\"all\"}", 10u);
  const std::string ok = FindLine(g->out, "RESP_OK");
  DynamicJsonDocument doc = Parse(ok);
  TEST_ASSERT_EQUAL(77, doc["ack"].as<int>());
  TEST_ASSERT_EQUAL(1, doc["v"].as<int>());
}

void test_resp_err_echoes_ack_seq_and_code(void) {
  MakeReady(*g);
  cmd("{\"v\":1,\"type\":\"CMD_MOVE\",\"seq\":88,\"lin\":9.9,\"ang\":0.0}",
      10u);
  const std::string err = FindLine(g->out, "RESP_ERR");
  DynamicJsonDocument doc = Parse(err);
  TEST_ASSERT_EQUAL(88, doc["ack"].as<int>());
  TEST_ASSERT_EQUAL_STRING("RANGE", doc["code"].as<const char*>());
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
  RUN_WITH_FIXTURE(test_boot_event_emitted_with_reason_and_fw);
  RUN_WITH_FIXTURE(test_starting_until_startup_checks_complete);
  RUN_WITH_FIXTURE(test_completely_malformed_lines_are_discarded);
  RUN_WITH_FIXTURE(test_unrecoverable_header_is_discarded);
  RUN_WITH_FIXTURE(test_missing_v_answers_parse);
  RUN_WITH_FIXTURE(test_version_mismatch_answers_version_and_raises_fault_8);
  RUN_WITH_FIXTURE(test_unknown_type_answers_unknown_type);
  RUN_WITH_FIXTURE(test_esp32_own_message_types_are_unknown_to_the_esp32);
  RUN_WITH_FIXTURE(test_cmd_move_field_validation);
  RUN_WITH_FIXTURE(test_cmd_stop_mode_validation_and_default);
  RUN_WITH_FIXTURE(test_cmd_reset_and_dispense_field_validation);
  RUN_WITH_FIXTURE(test_not_ready_before_startup_checks);
  RUN_WITH_FIXTURE(test_duplicate_command_replays_stored_response_without_reexecute);
  RUN_WITH_FIXTURE(test_heartbeat_never_triggers_a_response_or_duplicate_store);
  RUN_WITH_FIXTURE(test_not_implemented_drive_answers_internal);
  RUN_WITH_FIXTURE(test_resp_ok_echoes_ack_seq);
  RUN_WITH_FIXTURE(test_resp_err_echoes_ack_seq_and_code);
  return UNITY_END();
}

#undef RUN_WITH_FIXTURE

// vim: set ts=2 sts=2 sw=2:
