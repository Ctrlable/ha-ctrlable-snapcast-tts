// Host-side test for the echo-reference path (rtp_core.h): RTP parsing, the
// 16->48 kHz upsampler and the fixed-delay jitter buffer.
//
//   c++ -std=c++17 -Wall -Wextra -I esphome/components/rtp_mic tests/rtp_mic/test_rtp_ref.cpp -o /tmp/r && /tmp/r
//
// The jitter buffer is held to what the XVF3800's canceller needs, which is
// NOT what a playback buffer does:
//   - the delay from arrival to playout is the configured one, exactly;
//   - network jitter inside the delay changes NOTHING in the output;
//   - a packet later than its moment is dropped (silence), never played late,
//     because playing it late would shift every sample after it;
//   - a server clock running fast or slow is trimmed one sample at a time,
//     and the delay stays within the 1 ms deadband for good -- while jitter
//     alone (test 3, 20 s of it) never triggers a single trim;
//   - a sender restart (new timestamps) re-anchors at once;
//   - a dead stream goes silent, not stuck repeating the last audio.
#include "rtp_core.h"

#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <vector>

using namespace rtp_mic_core;

static int failures = 0;
#define CHECK(cond, why)                                         \
  do {                                                           \
    if (!(cond)) {                                               \
      std::printf("FAIL %s:%d %s\n", __FILE__, __LINE__, why);   \
      ++failures;                                                \
    }                                                            \
  } while (0)

// Sample value for RTP timestamp t: a ramp that never repeats inside a test,
// so any output sample tells exactly which input sample it was.
static int16_t val(uint32_t t) { return static_cast<int16_t>((t * 7u) % 30011u); }

static std::vector<uint8_t> payload(uint32_t ts, size_t n) {
  std::vector<uint8_t> p(2 * n);
  for (size_t k = 0; k < n; ++k) {
    const uint16_t u = static_cast<uint16_t>(val(ts + static_cast<uint32_t>(k)));
    p[2 * k] = u >> 8;
    p[2 * k + 1] = u & 0xff;
  }
  return p;
}

// Which input timestamp produced output sample s (or -1 for silence/unknown).
static long which(int16_t s, uint32_t lo, uint32_t hi) {
  for (uint32_t t = lo; t < hi; ++t)
    if (val(t) == s && s != 0)
      return static_cast<long>(t);
  return -1;
}

int main() {
  // 1. RTP parsing: plain, CSRC + extension + padding, and junk.
  {
    std::vector<uint8_t> pkt(12 + 8);
    pack_rtp_header(pkt.data(), 96, true, 513, 0x01020304, 0xabcdef01);
    RtpView v;
    CHECK(parse_rtp(pkt.data(), pkt.size(), v), "plain packet parses");
    CHECK(v.marker && v.seq == 513 && v.ts == 0x01020304 && v.payload_len == 8 && v.payload == pkt.data() + 12,
          "fields and payload of a plain packet");

    // 1 CSRC, a 1-word extension, 4 payload bytes, 3 bytes padding.
    std::vector<uint8_t> x = {0xB1, 0x60, 0, 1, 0, 0, 0, 9, 1, 2, 3, 4,  // V2 P X CC=1
                              5, 6, 7, 8,                                // CSRC
                              0xbe, 0xde, 0, 1, 9, 9, 9, 9,              // extension, 1 word
                              0x11, 0x22, 0x33, 0x44,                    // payload
                              0, 0, 3};                                  // padding
    CHECK(parse_rtp(x.data(), x.size(), v), "CSRC + extension + padding parses");
    CHECK(v.payload_len == 4 && v.payload[0] == 0x11 && v.ts == 9 && !v.marker, "payload found behind all of it");

    std::vector<uint8_t> bad = pkt;
    bad[0] = 0x40;
    CHECK(!parse_rtp(bad.data(), bad.size(), v), "version 1 rejected");
    CHECK(!parse_rtp(pkt.data(), 11, v), "short packet rejected");
    std::vector<uint8_t> overpad = pkt;
    overpad[0] |= 0x20;
    overpad.back() = 200;
    CHECK(!parse_rtp(overpad.data(), overpad.size(), v), "padding longer than the payload rejected");
  }

  // 2. Upsampler: exact on the input points, linear between, continuous across calls.
  {
    Upsampler3 up;
    const int16_t in1[2] = {300, 600};
    const int16_t in2[1] = {0};
    int16_t out[6];
    up.process(in1, 2, out);
    CHECK(out[0] == 100 && out[1] == 200 && out[2] == 300, "first triple ramps up from silence");
    CHECK(out[3] == 400 && out[4] == 500 && out[5] == 600, "second triple");
    up.process(in2, 1, out);
    CHECK(out[0] == 400 && out[1] == 200 && out[2] == 0, "continues from the previous call's last sample");
    const int16_t ext[2] = {32767, -32768};
    up.process(ext, 2, out);
    CHECK(out[2] == 32767 && out[5] == -32768 && out[3] < 32767 && out[4] > -32768, "full scale, no overflow");
  }

  // 3. Fixed delay, with jitter: packets of 160 (10 ms) arrive with up to
  //    +40 ms of random lateness against a 60 ms delay. Output must be the
  //    exact input stream, `delay` behind -- no sample lost, none moved.
  {
    const uint32_t D = 960;  // 60 ms
    RefJitterBuffer jb(D);
    std::srand(1);
    const uint32_t T0 = 4000000000u;  // wraps uint32 during the run
    std::vector<int16_t> out;
    // Simulate 5 s in 1-sample time steps; packets are "sent" every 160
    // samples and delivered after a random 0..640-sample delay.
    struct Pending { uint32_t due, ts; };
    std::vector<Pending> q;
    for (uint32_t now = 0; now < 320000; ++now) {  // 20 s: long past settling
      if (now % 160 == 0)
        q.push_back({now + static_cast<uint32_t>(std::rand() % 640), T0 + now});
      for (size_t i = 0; i < q.size();) {
        if (q[i].due <= now) {
          auto p = payload(q[i].ts, 160);
          jb.put(q[i].ts, false, p.data(), 160);
          q.erase(q.begin() + i);
        } else {
          ++i;
        }
      }
      if (now % 85 == 0 && now > 0) {  // the device drains in mic-task-sized chunks
        int16_t buf[85];
        jb.take(buf, 85);
        out.insert(out.end(), buf, buf + 85);
      }
    }
    // Output sample i is played at device time i (+ the first chunk boundary).
    // Find where the stream starts and check it is perfectly contiguous.
    long first = -1;
    size_t at = 0;
    for (; at < out.size(); ++at) {
      first = which(out[at], T0, T0 + 4000);
      if (first >= 0)
        break;
    }
    CHECK(first >= 0, "the stream starts");
    size_t bad = 0;
    for (size_t i = at; i < out.size(); ++i)
      if (out[i] != val(static_cast<uint32_t>(first) + static_cast<uint32_t>(i - at)))
        ++bad;
    CHECK(bad == 0, "jitter inside the delay leaves the output bit-exact and contiguous");
    CHECK(jb.late_samples() == 0, "nothing was late");
    CHECK(jb.resyncs() == 1, "anchored once, never again");
    CHECK(jb.trims() == 0, "jitter is not drift: the delay was never trimmed");
  }

  // 4. A packet later than its moment is dropped -- silence for that 10 ms,
  //    and every sample after it still lands exactly where it would have.
  {
    RefJitterBuffer jb(320);  // 20 ms
    int16_t buf[160];
    auto p0 = payload(0, 160);
    jb.put(0, false, p0.data(), 160);
    jb.take(buf, 160);  // plays -320..-161: silence
    jb.take(buf, 160);  // plays -160..-1
    jb.take(buf, 160);  // plays 0..159
    CHECK(buf[0] == val(0) && buf[159] == val(159), "on time");
    // Packet 160..319 never arrives in time; 320..479 does.
    auto p2 = payload(320, 160);
    jb.put(320, false, p2.data(), 160);
    jb.take(buf, 160);
    bool silent = true;
    for (auto s : buf) silent &= s == 0;
    CHECK(silent, "the missing 10 ms is silence");
    auto p1 = payload(160, 160);
    jb.put(160, false, p1.data(), 160);  // finally arrives -- too late
    CHECK(jb.late_samples() == 160, "late packet counted, not played");
    jb.take(buf, 160);
    CHECK(buf[0] == val(320) && buf[159] == val(479), "the next packet plays exactly on its own time");
    CHECK(jb.resyncs() == 1, "lateness never re-anchors (that would move the delay)");
  }

  // 5. Drift: the server's clock runs 100 ppm fast (it sends 16001.6 samples
  //    per device second). The delay must stay inside the deadband for good.
  {
    const uint32_t D = 960;
    RefJitterBuffer jb(D);
    double sent = 0;           // server samples produced, fractional
    uint32_t next_ts = 0;
    int16_t buf[85];
    double worst_ms = 0;
    for (uint32_t now = 0; now < 16000u * 120u; now += 85) {  // 2 minutes
      sent += 85 * 1.0001;
      while (next_ts + 160 <= sent) {
        auto p = payload(next_ts, 160);
        jb.put(next_ts, false, p.data(), 160);
        next_ts += 160;
      }
      jb.take(buf, 85);
      if (now > 16000u * 30u) {
        const double err = std::fabs(jb.drift_samples() / 16.0);
        if (err > worst_ms)
          worst_ms = err;
      }
    }
    CHECK(jb.trims() >= 150, "100 ppm over 2 min needs ~190 one-sample trims");
    CHECK(worst_ms <= 2.0, "delay held within 2 ms after settling");
    CHECK(jb.resyncs() == 1 && jb.late_samples() == 0, "drift never re-anchors or drops audio");
  }

  // 6. Sender restart: new random timestamps far away -> re-anchor at once,
  //    with the same delay. And the marker bit forces it too.
  {
    RefJitterBuffer jb(320);
    int16_t buf[160];
    auto a = payload(1000, 160);
    jb.put(1000, false, a.data(), 160);
    jb.take(buf, 160);
    jb.take(buf, 160);
    jb.take(buf, 160);
    CHECK(buf[0] == val(1000), "playing the first stream");
    auto b = payload(900000000u, 160);
    jb.put(900000000u, false, b.data(), 160);
    CHECK(jb.resyncs() == 2, "a far timestamp re-anchors");
    jb.take(buf, 160);
    jb.take(buf, 160);
    jb.take(buf, 160);
    CHECK(buf[0] == val(900000000u), "the new stream plays the same delay behind");
    auto c = payload(900000160u, 160);
    jb.put(900000160u, true, c.data(), 160);
    CHECK(jb.resyncs() == 3, "the marker bit re-anchors");
  }

  // 7. A dead stream goes silent and unprimes; it does not loop old audio.
  {
    RefJitterBuffer jb(320);
    int16_t buf[160];
    for (uint32_t ts = 0; ts < 1600; ts += 160) {
      auto p = payload(ts, 160);
      jb.put(ts, false, p.data(), 160);
    }
    size_t nonzero_after = 0;
    for (int i = 0; i < 200; ++i) {  // 2 s of takes, no packets
      jb.take(buf, 160);
      if (i > 20)
        for (auto s : buf) nonzero_after += s != 0;
    }
    CHECK(nonzero_after == 0, "after the last packet plays, only silence");
    CHECK(!jb.primed(), "unprimed after a second of nothing");
  }

  if (failures) {
    std::printf("%d failure(s)\n", failures);
    return 1;
  }
  std::printf("OK\n");
  return 0;
}
