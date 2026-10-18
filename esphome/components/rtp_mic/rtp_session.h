#pragma once
// The rtp_mic control protocol: who may make this device stream, and where.
//
// Pure logic -- no sockets, no clock -- so tests/rtp_mic/test_rtp_session.cpp
// executes exactly the state machine the device runs.
//
// WHY A PULL MODEL. The device never knows the server. The hardware manager
// opens a TCP connection to the device and asks for the stream; the device
// sends RTP back to the address the request CAME FROM. Nothing is typed into
// the device, no IP is compiled in, and the manager needs no ESPHome API key.
//
// THE PROTOCOL. One text line per request, '\n'-terminated:
//
//   START <udp_port> [beam=asr|wakeword] [ref=<ms>] [token=<t>]
//       -> OK STREAMING format=L16/16000/1 pt=96 ssrc=<hex> beam=<b> port=<p> [ref=<udp>|ref=off]
//   KEEPALIVE              -> OK ALIVE [ref=<udp>|ref=off]
//   STOP                   -> OK STOPPED
//   STATUS                 -> OK IDLE | OK STREAMING to=<ip>:<port> beam=<b>
//   LED <state>            -> (no reply) the session owner drives the LED ring
//   anything else          -> ERR bad-request
//
// LED is for Mic-only mode: the wake word and the pipeline run on the server,
// so the ring can only show what the server says. No reply, by design -- the
// manager sends these from its own thread while its main thread waits for the
// "OK ALIVE" to its KEEPALIVE, and a reply would be read as that. A non-owner's
// LED is dropped silently for the same reason. The state is a short word the
// yaml maps to its LED scripts (idle, wake, listening, thinking, speaking,
// error, ...); unknown words are the yaml's problem, not the protocol's.
//
// THE ECHO REFERENCE. `ref=<ms>` is the server OFFERING the room's mix back,
// to be played into the XVF3800's reference input <ms> behind its arrival.
// The device answers where to send it (`ref=<udp port>`) or that it does not
// want it (`ref=off`: it plays its answers itself, so the XVF already hears
// its own output). KEEPALIVE repeats that answer every time, so flipping the
// device between Snapcast and local answers starts or stops the server's
// sender within one keepalive -- and costs nothing on either side while off.
// A server that does not offer gets the old replies, unchanged.
//
// A session lives exactly as long as its TCP connection AND keeps talking:
// closing the connection ends it, and so does IDLE_TIMEOUT without a line
// (the manager sends KEEPALIVE every 10 s). A manager that crashes, loses
// power or changes address therefore stops the stream on its own -- nobody
// has to remember to turn the mic off.
//
// ONE SESSION. A START from a different host while a live session exists is
// refused with "ERR busy held-by=<ip>", so two servers cannot fight over one
// mic and the refusal says who to go and look at. The SAME host may take over
// (a restarted manager reconnects before its old connection has timed out).

#include <cstdint>
#include <cstdlib>
#include <string>
#include <utility>

namespace rtp_mic_core {

enum class Beam : uint8_t { ASR = 0, WAKEWORD = 1 };

inline const char *beam_name(Beam b) { return b == Beam::WAKEWORD ? "wakeword" : "asr"; }

struct Reply {
  Reply() = default;
  Reply(std::string l, bool c = false, bool ch = false) : line(std::move(l)), close(c), changed(ch) {}
  std::string line;        // sent back to the requester, without '\n'
  bool close{false};       // close this connection after replying
  bool changed{false};     // the streaming target changed; the caller re-reads the session
  std::string led;         // LED state to show (owner's LED request); empty = none
};

struct Session {
  bool active{false};
  int conn{-1};            // control connection that owns it
  uint32_t ip{0};          // peer address, network order as the socket gave it
  std::string ip_text;     // dotted, for replies and the "client" sensor
  uint16_t port{0};
  Beam beam{Beam::ASR};
  uint32_t last_seen_ms{0};
  bool ref_offered{false};
  uint16_t ref_delay_ms{0};
};

class SessionTable {
 public:
  static constexpr uint32_t IDLE_TIMEOUT_MS = 30000;
  static constexpr long MAX_REF_DELAY_MS = 250;  // half the device's 512 ms jitter ring
  static constexpr size_t MAX_LED_STATE = 24;

  // token: empty means no authentication (see the package's rtp_mic_token).
  explicit SessionTable(std::string token = "") : token_(std::move(token)) {}

  void set_enabled(bool enabled) { this->enabled_ = enabled; }
  bool enabled() const { return this->enabled_; }
  const Session &current() const { return this->session_; }

  // The UDP port the device takes a reference on, or 0 when it does not want
  // one (no reference wired, or answers play locally).
  void set_ref_port(uint16_t port) { this->ref_port_ = port; }
  uint16_t ref_port() const { return this->ref_port_; }

  Reply handle_line(int conn, uint32_t peer_ip, const std::string &peer_text, const std::string &raw, uint32_t now,
                    uint32_t ssrc, uint8_t payload_type) {
    std::string line = raw;
    while (!line.empty() && (line.back() == '\r' || line.back() == ' '))
      line.pop_back();
    std::string verb, rest;
    split_first_(line, verb, rest);

    if (verb == "START")
      return this->start_(conn, peer_ip, peer_text, rest, now, ssrc, payload_type);
    if (verb == "KEEPALIVE") {
      if (this->owns_(conn)) {
        this->session_.last_seen_ms = now;
        return {"OK ALIVE" + this->ref_answer_()};
      }
      return {"ERR no-session"};
    }
    if (verb == "STOP") {
      if (this->owns_(conn)) {
        this->session_ = Session{};
        return {"OK STOPPED", false, true};
      }
      return {"ERR no-session"};
    }
    if (verb == "LED") {
      Reply r;
      if (this->owns_(conn) && !rest.empty() && rest.size() <= MAX_LED_STATE)
        r.led = rest;
      return r;
    }
    if (verb == "STATUS") {
      if (!this->session_.active)
        return {"OK IDLE"};
      return {"OK STREAMING to=" + this->session_.ip_text + ":" + std::to_string(this->session_.port) +
              " beam=" + beam_name(this->session_.beam)};
    }
    return {"ERR bad-request", true};
  }

  // The owning connection went away: the stream stops with it.
  bool on_disconnect(int conn) {
    if (!this->owns_(conn))
      return false;
    this->session_ = Session{};
    return true;
  }

  // Called from loop(). True when the session just expired.
  bool tick(uint32_t now) {
    if (!this->session_.active)
      return false;
    if (now - this->session_.last_seen_ms < IDLE_TIMEOUT_MS)
      return false;
    this->session_ = Session{};
    return true;
  }

  // Switched off (the "RTP mic service" switch): drop any session at once.
  bool disable() {
    this->enabled_ = false;
    if (!this->session_.active)
      return false;
    this->session_ = Session{};
    return true;
  }

 protected:
  bool owns_(int conn) const { return this->session_.active && this->session_.conn == conn; }

  static void split_first_(const std::string &s, std::string &first, std::string &rest) {
    const auto sp = s.find(' ');
    if (sp == std::string::npos) {
      first = s;
      rest.clear();
    } else {
      first = s.substr(0, sp);
      rest = s.substr(sp + 1);
    }
  }

  Reply start_(int conn, uint32_t peer_ip, const std::string &peer_text, const std::string &args, uint32_t now,
               uint32_t ssrc, uint8_t payload_type) {
    if (!this->enabled_)
      return {"ERR disabled", true};

    std::string port_s, rest = args, tok;
    split_first_(rest, port_s, rest);
    char *end = nullptr;
    const long port = std::strtol(port_s.c_str(), &end, 10);
    if (port_s.empty() || end == nullptr || *end != '\0' || port < 1 || port > 65535)
      return {"ERR bad-port", true};

    Beam beam = Beam::ASR;
    bool ref_offered = false;
    long ref_delay = 0;
    std::string token;
    while (!rest.empty()) {
      split_first_(rest, tok, rest);
      if (tok == "beam=asr") {
        beam = Beam::ASR;
      } else if (tok == "beam=wakeword") {
        beam = Beam::WAKEWORD;
      } else if (tok.rfind("ref=", 0) == 0) {
        const std::string v = tok.substr(4);
        char *vend = nullptr;
        ref_delay = std::strtol(v.c_str(), &vend, 10);
        if (v.empty() || vend == nullptr || *vend != '\0' || ref_delay < 0 || ref_delay > MAX_REF_DELAY_MS)
          return {"ERR bad-ref", true};
        ref_offered = true;
      } else if (tok.rfind("token=", 0) == 0) {
        token = tok.substr(6);
      } else if (!tok.empty()) {
        return {"ERR bad-request", true};
      }
    }
    // Constant-time only matters against a timing attacker on the LAN, which
    // this token does not pretend to stop; a plain compare is honest.
    if (!this->token_.empty() && token != this->token_)
      return {"ERR auth", true};

    if (this->session_.active && this->session_.conn != conn && this->session_.ip != peer_ip &&
        now - this->session_.last_seen_ms < IDLE_TIMEOUT_MS)
      return {"ERR busy held-by=" + this->session_.ip_text, true};

    const bool changed = !this->session_.active || this->session_.ip != peer_ip ||
                         this->session_.port != static_cast<uint16_t>(port) || this->session_.beam != beam ||
                         this->session_.ref_offered != ref_offered ||
                         this->session_.ref_delay_ms != static_cast<uint16_t>(ref_delay);
    this->session_.active = true;
    this->session_.conn = conn;
    this->session_.ip = peer_ip;
    this->session_.ip_text = peer_text;
    this->session_.port = static_cast<uint16_t>(port);
    this->session_.beam = beam;
    this->session_.last_seen_ms = now;
    this->session_.ref_offered = ref_offered;
    this->session_.ref_delay_ms = static_cast<uint16_t>(ref_delay);

    char ssrc_hex[9];
    static const char *const HEX = "0123456789abcdef";
    for (int i = 0; i < 8; ++i)
      ssrc_hex[i] = HEX[(ssrc >> (28 - 4 * i)) & 0xf];
    ssrc_hex[8] = '\0';
    return {"OK STREAMING format=L16/16000/1 pt=" + std::to_string(payload_type) + " ssrc=" + ssrc_hex +
                " beam=" + beam_name(beam) + " port=" + std::to_string(port) + this->ref_answer_(),
            false, changed};
  }

  // Only to a server that offered: an old manager sees the old replies.
  std::string ref_answer_() const {
    if (!this->session_.ref_offered)
      return "";
    return this->ref_port_ ? " ref=" + std::to_string(this->ref_port_) : std::string(" ref=off");
  }

  std::string token_;
  uint16_t ref_port_{0};
  bool enabled_{true};
  Session session_;
};

}  // namespace rtp_mic_core
