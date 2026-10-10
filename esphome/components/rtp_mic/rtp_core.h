#pragma once
// Pure logic of the rtp_mic component: no ESPHome, no lwIP, so the host test
// (tests/rtp_mic/test_rtp_core.cpp) can execute exactly what runs on the device.

#include <cmath>
#include <cstddef>
#include <cstdint>
#include <vector>

namespace rtp_mic_core {

static constexpr size_t RTP_HEADER_LEN = 12;

// RFC 3550 fixed header: V=2, no padding, no extension, no CSRCs.
inline void pack_rtp_header(uint8_t *out, uint8_t payload_type, bool marker, uint16_t seq, uint32_t timestamp,
                            uint32_t ssrc) {
  out[0] = 0x80;
  out[1] = static_cast<uint8_t>((marker ? 0x80 : 0x00) | (payload_type & 0x7f));
  out[2] = seq >> 8;
  out[3] = seq & 0xff;
  out[4] = timestamp >> 24;
  out[5] = (timestamp >> 16) & 0xff;
  out[6] = (timestamp >> 8) & 0xff;
  out[7] = timestamp & 0xff;
  out[8] = ssrc >> 24;
  out[9] = (ssrc >> 16) & 0xff;
  out[10] = (ssrc >> 8) & 0xff;
  out[11] = ssrc & 0xff;
}

// Interleaved 32-bit little-endian frames -> one channel as 16-bit, keeping the
// top 16 bits (the XVF3800 left-justifies its 24-bit output). Returns samples.
inline size_t pick_channel_s32_to_s16(const uint8_t *data, size_t len, uint8_t channels, uint8_t channel,
                                      int16_t *out) {
  const size_t frame_bytes = 4u * channels;
  const size_t frames = len / frame_bytes;
  for (size_t f = 0; f < frames; ++f) {
    const uint8_t *s = data + f * frame_bytes + 4u * channel;
    out[f] = static_cast<int16_t>(static_cast<uint16_t>(s[2]) | (static_cast<uint16_t>(s[3]) << 8));
  }
  return frames;
}

// L16 (RFC 3551) is network byte order.
inline void write_l16_be(const int16_t *in, size_t n, uint8_t *out) {
  for (size_t i = 0; i < n; ++i) {
    const uint16_t u = static_cast<uint16_t>(in[i]);
    out[2 * i] = u >> 8;
    out[2 * i + 1] = u & 0xff;
  }
}

// Integer-factor decimator. Factor 1 is a passthrough: formatBCE's i2s_audio
// fork already keeps every third 48 kHz frame and reports 16 kHz, so on this
// device nothing is resampled here. Factor 3 exists for a stock microphone that
// really delivers 48 kHz -- dropping samples without a low-pass would fold
// 8-24 kHz room noise down into the speech band.
class Decimator {
 public:
  explicit Decimator(unsigned factor) : factor_(factor ? factor : 1) {
    if (factor_ == 1)
      return;
    // Blackman-windowed sinc, cutoff just under the new Nyquist.
    const int taps = 16 * static_cast<int>(factor_) + 1;
    const double fc = 0.45 / factor_;  // of the input rate
    coeffs_.resize(taps);
    double sum = 0;
    for (int i = 0; i < taps; ++i) {
      const double m = i - (taps - 1) / 2.0;
      const double sinc = m == 0 ? 2 * fc : std::sin(2 * M_PI * fc * m) / (M_PI * m);
      const double w = 0.42 - 0.5 * std::cos(2 * M_PI * i / (taps - 1)) + 0.08 * std::cos(4 * M_PI * i / (taps - 1));
      coeffs_[i] = static_cast<float>(sinc * w);
      sum += coeffs_[i];
    }
    for (auto &c : coeffs_) c = static_cast<float>(c / sum);
    history_.assign(taps, 0.0f);
  }

  // Returns the number of output samples written to `out` (room for n needed).
  size_t process(const int16_t *in, size_t n, int16_t *out) {
    if (factor_ == 1) {
      for (size_t i = 0; i < n; ++i) out[i] = in[i];
      return n;
    }
    size_t m = 0;
    const size_t taps = coeffs_.size();
    for (size_t i = 0; i < n; ++i) {
      history_[pos_] = in[i];
      pos_ = (pos_ + 1) % taps;
      if (++phase_ < factor_)
        continue;
      phase_ = 0;
      float acc = 0;
      size_t k = pos_;  // oldest sample
      for (size_t t = 0; t < taps; ++t) {
        acc += coeffs_[t] * history_[k];
        if (++k == taps)
          k = 0;
      }
      if (acc > 32767.f)
        acc = 32767.f;
      if (acc < -32768.f)
        acc = -32768.f;
      out[m++] = static_cast<int16_t>(std::lrintf(acc));
    }
    return m;
  }

 private:
  unsigned factor_;
  unsigned phase_{0};
  size_t pos_{0};
  std::vector<float> coeffs_;
  std::vector<float> history_;
};

// Collects samples into fixed-size RTP packets and hands each finished packet
// to `send`. A partial packet is held until the next push, so packet size and
// timestamp spacing stay constant -- PipeWire's receiver sizes its buffer from
// what it sees, and ragged packets make it hunt.
class Packetizer {
 public:
  Packetizer(uint8_t payload_type, uint32_t ssrc, size_t samples_per_packet)
      : pt_(payload_type), ssrc_(ssrc), spp_(samples_per_packet), buf_(RTP_HEADER_LEN + 2 * samples_per_packet) {}

  // Next packet gets the marker bit and a fresh start; call when streaming
  // (re)starts so the receiver resynchronises instead of seeing a long gap.
  void restart() {
    fill_ = 0;
    marker_ = true;
  }

  template<typename Send> void push(const int16_t *in, size_t n, Send &&send) {
    while (n > 0) {
      const size_t take = (spp_ - fill_) < n ? (spp_ - fill_) : n;
      write_l16_be(in, take, buf_.data() + RTP_HEADER_LEN + 2 * fill_);
      fill_ += take;
      in += take;
      n -= take;
      if (fill_ == spp_) {
        pack_rtp_header(buf_.data(), pt_, marker_, seq_, ts_, ssrc_);
        send(buf_.data(), buf_.size());
        marker_ = false;
        ++seq_;
        ts_ += static_cast<uint32_t>(spp_);
        fill_ = 0;
      }
    }
  }

 private:
  uint8_t pt_;
  uint32_t ssrc_;
  size_t spp_;
  std::vector<uint8_t> buf_;
  size_t fill_{0};
  bool marker_{true};
  uint16_t seq_{0};
  uint32_t ts_{0};
};

}  // namespace rtp_mic_core
