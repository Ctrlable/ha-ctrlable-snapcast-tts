// Host-side test for the rtp_mic component's pure logic (no ESPHome, no lwIP).
//
//   c++ -std=c++17 -I esphome/components/rtp_mic tests/rtp_mic/test_rtp_core.cpp -o /tmp/t && /tmp/t
//
// What it pins down, because each one is a way the stream reaches PipeWire
// looking fine and being wrong:
//   - the RTP header bytes, since PipeWire drops a packet with a bad version or
//     payload type silently;
//   - L16 is BIG-endian on the wire; sending the ESP32's native little-endian
//     samples produces loud noise that still "works";
//   - the channel pick, because the XVF3800 sends two different beams and the
//     wrong one is the one that wakes on snoring;
//   - the 48k->16k path, which must actually low-pass, or a 10 kHz whistle in
//     the room folds down to 6 kHz -- inside the speech band.
#include "rtp_core.h"

#include <cmath>
#include <cstdio>
#include <cstring>
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

static void put_i32le(std::vector<uint8_t> &v, int32_t s) {
  uint32_t u = static_cast<uint32_t>(s);
  for (int i = 0; i < 4; ++i) v.push_back((u >> (8 * i)) & 0xff);
}

int main() {
  // 1. header
  uint8_t h[12];
  pack_rtp_header(h, 96, false, 0x1234, 0xA0B0C0D0u, 0x01020304u);
  const uint8_t want[12] = {0x80, 96, 0x12, 0x34, 0xA0, 0xB0, 0xC0, 0xD0, 0x01, 0x02, 0x03, 0x04};
  CHECK(std::memcmp(h, want, 12) == 0, "RTP header bytes");
  pack_rtp_header(h, 96, true, 0, 0, 0);
  CHECK(h[1] == (0x80 | 96), "marker bit sits on the payload-type byte");

  // 2. channel pick + 32->16 bit, from interleaved stereo int32 LE frames
  std::vector<uint8_t> frames;
  put_i32le(frames, 0x11110000);  // ch0
  put_i32le(frames, 0x22220000);  // ch1
  put_i32le(frames, -0x10000);    // ch0 = -1 in 16 bit
  put_i32le(frames, 0x7fff0000);  // ch1
  int16_t out[2];
  size_t n = pick_channel_s32_to_s16(frames.data(), frames.size(), 2, 1, out);
  CHECK(n == 2, "two frames in, two samples out");
  CHECK(out[0] == 0x2222 && out[1] == 0x7fff, "channel 1 picked, top 16 bits kept");
  n = pick_channel_s32_to_s16(frames.data(), frames.size(), 2, 0, out);
  CHECK(out[0] == 0x1111 && out[1] == -1, "channel 0 picked, sign preserved");

  // 3. big-endian L16
  int16_t s[2] = {0x0102, -2};
  uint8_t be[4];
  write_l16_be(s, 2, be);
  CHECK(be[0] == 0x01 && be[1] == 0x02 && be[2] == 0xff && be[3] == 0xfe, "L16 is big-endian");

  // 4. passthrough at 16 kHz: the formatBCE driver already hands out 16 kHz
  Decimator d16(1);
  std::vector<int16_t> in16 = {1, 2, 3, 4};
  std::vector<int16_t> o16(4);
  CHECK(d16.process(in16.data(), 4, o16.data()) == 4 && o16 == in16, "factor 1 is a passthrough");

  // 5. 48k -> 16k: count, passband kept, aliasing removed
  auto rms_after = [](double hz) {
    Decimator d(3);
    const int N = 48000;
    std::vector<int16_t> x(N), y(N);
    for (int i = 0; i < N; ++i) x[i] = static_cast<int16_t>(10000 * std::sin(2 * M_PI * hz * i / 48000.0));
    size_t m = d.process(x.data(), N, y.data());
    double acc = 0;
    for (size_t i = 200; i < m; ++i) acc += double(y[i]) * y[i];  // skip the filter's warm-up
    return std::make_pair(m, std::sqrt(acc / (m - 200)));
  };
  auto pass = rms_after(1000);
  CHECK(pass.first == 16000, "3:1 decimation count");
  CHECK(pass.second > 6500, "1 kHz passes (~7071 rms in)");
  auto stop = rms_after(10000);
  CHECK(stop.second < 700, "10 kHz is filtered, not folded to 6 kHz");

  // 6. packetiser: 20 ms of 16 kHz = 320 samples = 640 payload bytes; seq and
  //    timestamp advance; a short tail is held, not sent.
  Packetizer p(96, 0xCAFEBABEu, 320);
  std::vector<std::vector<uint8_t>> sent;
  auto sink = [&](const uint8_t *buf, size_t len) { sent.emplace_back(buf, buf + len); };
  std::vector<int16_t> chunk(700, 7);
  p.push(chunk.data(), chunk.size(), sink);
  CHECK(sent.size() == 2, "700 samples -> 2 full packets, 60 held");
  CHECK(sent.size() == 2 && sent[0].size() == 12 + 640, "packet is header + 640 bytes");
  if (sent.size() == 2) {
    uint16_t seq0 = (sent[0][2] << 8) | sent[0][3], seq1 = (sent[1][2] << 8) | sent[1][3];
    uint32_t ts0 = (uint32_t(sent[0][4]) << 24) | (sent[0][5] << 16) | (sent[0][6] << 8) | sent[0][7];
    uint32_t ts1 = (uint32_t(sent[1][4]) << 24) | (sent[1][5] << 16) | (sent[1][6] << 8) | sent[1][7];
    CHECK(uint16_t(seq1 - seq0) == 1, "sequence advances by one");
    CHECK(ts1 - ts0 == 320, "timestamp advances by samples per packet");
    CHECK(sent[0][1] == (0x80 | 96), "first packet after start carries the marker");
    CHECK(sent[1][1] == 96, "later packets do not");
    CHECK(sent[0][12] == 0x00 && sent[0][13] == 0x07, "payload is the samples, big-endian");
  }
  p.push(chunk.data(), 260, sink);
  CHECK(sent.size() == 3, "held 60 + 260 completes the third packet");

  if (failures) {
    std::printf("%d failure(s)\n", failures);
    return 1;
  }
  std::printf("OK\n");
  return 0;
}
