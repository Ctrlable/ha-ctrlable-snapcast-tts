// Host-side test for the rtp_mic control protocol (rtp_session.h).
//
//   c++ -std=c++17 -Wall -Wextra -I esphome/components/rtp_mic tests/rtp_mic/test_rtp_session.cpp -o /tmp/s && /tmp/s
//
// What it pins down, because each is a way a mic ends up streaming to the
// wrong place, or to nobody, without anyone noticing:
//   - the stream goes to the address the request CAME FROM, at the asked port;
//   - a second server cannot steal a live session, and is told who holds it;
//   - the same server may take over (a restarted manager reconnects early);
//   - a silent manager loses the session after the idle timeout, and a closed
//     connection loses it at once -- the mic must never stream forever;
//   - the token, when set, is enforced; when empty, it is not required;
//   - the service switch refuses new sessions and drops the current one.
#include "rtp_session.h"

#include <cstdio>
#include <string>

using namespace rtp_mic_core;

static int failures = 0;
#define CHECK(cond, why)                                         \
  do {                                                           \
    if (!(cond)) {                                               \
      std::printf("FAIL %s:%d %s\n", __FILE__, __LINE__, why);   \
      ++failures;                                                \
    }                                                            \
  } while (0)

static bool starts_with(const std::string &s, const std::string &p) { return s.rfind(p, 0) == 0; }

static const uint32_t BETA = 0xAE08010A;   // 10.1.8.174 as lwIP stores it
static const uint32_t OTHER = 0x3A08010A;  // 10.1.8.58

int main() {
  // 1. START: streams to the caller, replies with the format.
  {
    SessionTable t;
    Reply r = t.handle_line(5, BETA, "10.1.8.174", "START 46000\r", 1000, 0xdeadbeef, 96);
    CHECK(starts_with(r.line, "OK STREAMING format=L16/16000/1 pt=96 ssrc=deadbeef beam=asr port=46000"),
          "START reply carries format, pt, ssrc, beam and port");
    CHECK(r.changed && !r.close, "a new session is a change and keeps the connection");
    CHECK(t.current().active && t.current().ip == BETA && t.current().port == 46000, "target = caller:port");
    CHECK(t.current().ip_text == "10.1.8.174", "peer text kept for the client sensor");
    Reply s = t.handle_line(5, BETA, "10.1.8.174", "STATUS", 1500, 0, 96);
    CHECK(s.line == "OK STREAMING to=10.1.8.174:46000 beam=asr", "STATUS reports the live target");
  }

  // 2. beam selection, and bad requests close the connection.
  {
    SessionTable t;
    Reply r = t.handle_line(1, BETA, "10.1.8.174", "START 46002 beam=wakeword", 0, 1, 96);
    CHECK(t.current().beam == Beam::WAKEWORD, "beam=wakeword honoured");
    CHECK(r.line.find("beam=wakeword") != std::string::npos, "reply echoes the beam");
    CHECK(t.handle_line(2, BETA, "x", "START 0", 0, 1, 96).line == "ERR bad-port", "port 0 refused");
    CHECK(t.handle_line(2, BETA, "x", "START 70000", 0, 1, 96).line == "ERR bad-port", "port > 65535 refused");
    CHECK(t.handle_line(2, BETA, "x", "START abc", 0, 1, 96).close, "garbage port closes the connection");
    CHECK(t.handle_line(2, BETA, "x", "START 5 beam=front", 0, 1, 96).line == "ERR bad-request", "unknown option");
    CHECK(t.handle_line(2, BETA, "x", "HELLO", 0, 1, 96).line == "ERR bad-request", "unknown verb");
    CHECK(t.current().port == 46002, "failed requests leave the live session alone");
  }

  // 3. one session: another host is refused and told who holds it.
  {
    SessionTable t;
    t.handle_line(1, BETA, "10.1.8.174", "START 46000", 0, 1, 96);
    Reply r = t.handle_line(2, OTHER, "10.1.8.58", "START 47000", 5000, 1, 96);
    CHECK(r.line == "ERR busy held-by=10.1.8.174", "second host refused with the holder");
    CHECK(r.close, "the refused connection is closed");
    CHECK(t.current().ip == BETA && t.current().port == 46000, "the holder keeps streaming");
    CHECK(t.handle_line(2, OTHER, "10.1.8.58", "STOP", 5000, 1, 96).line == "ERR no-session",
          "a non-owner cannot STOP someone else's stream");
    CHECK(t.handle_line(2, OTHER, "10.1.8.58", "KEEPALIVE", 5000, 1, 96).line == "ERR no-session",
          "a non-owner's keepalive does not extend the session");
    // Same host, new connection (manager restarted): takes over.
    Reply again = t.handle_line(3, BETA, "10.1.8.174", "START 46010", 6000, 1, 96);
    CHECK(starts_with(again.line, "OK STREAMING"), "same host may take over");
    CHECK(t.current().conn == 3 && t.current().port == 46010, "ownership moves to the new connection");
    CHECK(!t.on_disconnect(1), "the old connection closing no longer ends the session");
    CHECK(t.current().active, "...so the stream continues");
  }

  // 4. keepalive and the idle timeout.
  {
    SessionTable t;
    t.handle_line(1, BETA, "10.1.8.174", "START 46000", 1000, 1, 96);
    CHECK(t.handle_line(1, BETA, "10.1.8.174", "KEEPALIVE", 20000, 1, 96).line == "OK ALIVE", "keepalive ok");
    CHECK(!t.tick(20000 + SessionTable::IDLE_TIMEOUT_MS - 1), "alive just inside the timeout");
    CHECK(t.tick(20000 + SessionTable::IDLE_TIMEOUT_MS), "expires at the timeout");
    CHECK(!t.current().active, "expired session is gone");
    // After expiry another host may start.
    Reply r = t.handle_line(2, OTHER, "10.1.8.58", "START 47000", 60000, 1, 96);
    CHECK(starts_with(r.line, "OK STREAMING"), "another host may start once the holder went silent");
    // A stale-but-not-yet-ticked holder also does not block (tick runs in loop(), may lag).
    SessionTable u;
    u.handle_line(1, BETA, "10.1.8.174", "START 46000", 0, 1, 96);
    CHECK(starts_with(u.handle_line(2, OTHER, "10.1.8.58", "START 47000", SessionTable::IDLE_TIMEOUT_MS, 1, 96).line,
                      "OK STREAMING"),
          "a holder silent for the whole timeout does not block, even before tick()");
  }

  // 5. disconnect ends the stream; STOP ends it.
  {
    SessionTable t;
    t.handle_line(4, BETA, "10.1.8.174", "START 46000", 0, 1, 96);
    CHECK(t.on_disconnect(4), "owner disconnect reported");
    CHECK(!t.current().active, "stream ends with its connection");
    t.handle_line(4, BETA, "10.1.8.174", "START 46000", 0, 1, 96);
    Reply r = t.handle_line(4, BETA, "10.1.8.174", "STOP", 10, 1, 96);
    CHECK(r.line == "OK STOPPED" && r.changed, "STOP replies and is a change");
    CHECK(t.handle_line(4, BETA, "10.1.8.174", "STATUS", 10, 1, 96).line == "OK IDLE", "idle after STOP");
  }

  // 6. token.
  {
    SessionTable open;
    CHECK(starts_with(open.handle_line(1, BETA, "b", "START 46000 token=whatever", 0, 1, 96).line, "OK"),
          "no token configured: any token (or none) accepted");
    SessionTable t("s3cret");
    CHECK(t.handle_line(1, BETA, "b", "START 46000", 0, 1, 96).line == "ERR auth", "missing token refused");
    CHECK(t.handle_line(1, BETA, "b", "START 46000 token=nope", 0, 1, 96).line == "ERR auth", "wrong token refused");
    CHECK(!t.current().active, "no session after auth failure");
    CHECK(starts_with(t.handle_line(1, BETA, "b", "START 46000 beam=asr token=s3cret", 0, 1, 96).line, "OK"),
          "right token accepted");
  }

  // 7. the service switch.
  {
    SessionTable t;
    t.handle_line(1, BETA, "b", "START 46000", 0, 1, 96);
    CHECK(t.disable(), "disabling drops a live session");
    CHECK(!t.current().active, "no stream while disabled");
    CHECK(t.handle_line(1, BETA, "b", "START 46000", 0, 1, 96).line == "ERR disabled", "START refused while off");
    t.set_enabled(true);
    CHECK(starts_with(t.handle_line(1, BETA, "b", "START 46000", 0, 1, 96).line, "OK"), "works again when on");
  }

  // 8. the echo reference offer.
  {
    SessionTable t;
    t.set_ref_port(6056);
    Reply plain = t.handle_line(1, BETA, "b", "START 46000", 0, 1, 96);
    CHECK(plain.line.find("ref=") == std::string::npos, "no offer, no ref answer: an old manager sees old replies");
    CHECK(t.handle_line(1, BETA, "b", "KEEPALIVE", 5, 1, 96).line == "OK ALIVE", "old keepalive reply unchanged");

    Reply r = t.handle_line(1, BETA, "b", "START 46000 beam=asr ref=60", 10, 1, 96);
    CHECK(r.line.find(" ref=6056") != std::string::npos, "offer answered with the reference port");
    CHECK(r.changed, "taking up the offer is a change (the device must open the port)");
    CHECK(t.current().ref_offered && t.current().ref_delay_ms == 60, "offer and delay kept");
    CHECK(t.handle_line(1, BETA, "b", "KEEPALIVE", 20, 1, 96).line == "OK ALIVE ref=6056", "keepalive repeats it");

    // The device switched to playing answers itself: the server must hear so.
    t.set_ref_port(0);
    CHECK(t.handle_line(1, BETA, "b", "KEEPALIVE", 30, 1, 96).line == "OK ALIVE ref=off", "local mode says off");
    Reply again = t.handle_line(1, BETA, "b", "START 46000 beam=asr ref=60", 40, 1, 96);
    CHECK(again.line.find(" ref=off") != std::string::npos && !again.changed, "re-START in local mode: off");

    CHECK(t.handle_line(2, BETA, "b", "START 46000 ref=", 50, 1, 96).line == "ERR bad-ref", "empty delay refused");
    CHECK(t.handle_line(2, BETA, "b", "START 46000 ref=-5", 50, 1, 96).line == "ERR bad-ref", "negative refused");
    CHECK(t.handle_line(2, BETA, "b", "START 46000 ref=900", 50, 1, 96).line == "ERR bad-ref",
          "beyond the device's jitter ring refused, not silently clipped");
    CHECK(t.handle_line(2, BETA, "b", "START 46000 ref=40ms", 50, 1, 96).close, "garbage closes");
    CHECK(t.current().ref_delay_ms == 60, "bad offers leave the live session alone");

    Reply d = t.handle_line(1, BETA, "b", "START 46000 beam=asr ref=80", 60, 1, 96);
    CHECK(d.changed && t.current().ref_delay_ms == 80, "a new delay is a change (the buffer must re-anchor)");
  }

  if (failures) {
    std::printf("%d failure(s)\n", failures);
    return 1;
  }
  std::printf("OK\n");
  return 0;
}
