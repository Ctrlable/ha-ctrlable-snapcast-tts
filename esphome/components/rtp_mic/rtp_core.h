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


// ---------------------------------------------------------------------------
// The echo reference: the room's mix, sent BACK to the device and played into
// the XVF3800's reference input (silently -- the DAC stays muted), so the
// XVF's own echo canceller can subtract the Snapcast audio it otherwise never
// sees. Server-side cancelling of the XVF's output was measured useless
// (2026-10-10: 0.0-0.3 dB on both beams): its output is already non-linearly
// processed. The canceller has to sit before that processing, i.e. in the XVF.

// What an RTP packet carries, without copying it.
struct RtpView {
  bool marker{false};
  uint16_t seq{0};
  uint32_t ts{0};
  const uint8_t *payload{nullptr};
  size_t payload_len{0};
};

// RFC 3550 framing, including CSRCs, the extension and padding, because the
// sender is PipeWire's rtp-sink, not code we control. False on anything that
// is not version-2 RTP or does not add up.
inline bool parse_rtp(const uint8_t *p, size_t n, RtpView &out) {
  if (n < RTP_HEADER_LEN || (p[0] >> 6) != 2)
    return false;
  size_t hdr = RTP_HEADER_LEN + 4u * (p[0] & 0x0f);
  if (n < hdr)
    return false;
  if (p[0] & 0x10) {
    if (n < hdr + 4)
      return false;
    hdr += 4 + 4u * ((static_cast<size_t>(p[hdr + 2]) << 8) | p[hdr + 3]);
    if (n < hdr)
      return false;
  }
  size_t end = n;
  if (p[0] & 0x20) {
    const size_t pad = p[n - 1];
    if (pad == 0 || pad > end - hdr)
      return false;
    end -= pad;
  }
  out.marker = (p[1] & 0x80) != 0;
  out.seq = static_cast<uint16_t>((p[2] << 8) | p[3]);
  out.ts = (static_cast<uint32_t>(p[4]) << 24) | (static_cast<uint32_t>(p[5]) << 16) |
           (static_cast<uint32_t>(p[6]) << 8) | p[7];
  out.payload = p + hdr;
  out.payload_len = end - hdr;
  return true;
}

// 16 kHz -> 48 kHz by linear interpolation. Crude on purpose: the XVF3800
// runs its pipeline at 16 kHz and low-passes everything it is given back down
// to 8 kHz, so a better filter here would buy nothing but CPU. The output lags
// the input by exactly one 16 kHz sample, a constant the delay setting absorbs.
class Upsampler3 {
 public:
  void reset() { prev_ = 0; }
  // `out` needs room for 3 * n samples.
  void process(const int16_t *in, size_t n, int16_t *out) {
    for (size_t i = 0; i < n; ++i) {
      const int32_t a = prev_, b = in[i];
      out[3 * i] = static_cast<int16_t>(a + (b - a) / 3);
      out[3 * i + 1] = static_cast<int16_t>(a + 2 * (b - a) / 3);
      out[3 * i + 2] = static_cast<int16_t>(b);
      prev_ = in[i];
    }
  }

 private:
  int16_t prev_{0};
};

// A FIXED-delay jitter buffer, indexed by RTP timestamp.
//
// The XVF's canceller needs the reference-to-echo delay CONSTANT -- XMOS: "any
// inconsistency in this delay will result in severely degraded algorithmic
// performance". So unlike a playback buffer this never grows to ride out a bad
// patch: the first packet fixes the playout point `delay` samples behind it,
// a packet that misses its moment is dropped (that stretch of reference is
// silence), and the delay itself moves only by the slow drift trim below.
//
// Drift: the server's sound card and the XVF's I2S clock differ by tens of
// ppm. The average lead of arriving packets over the playout point is watched;
// when it strays more than 1 ms from where it SETTLED after anchoring, one
// sample is skipped or repeated, at most every half second (125 ppm of
// correction) -- a step the canceller follows without noticing, unlike a jump.
// Where it settled, not `delay`: the anchor packet's own lateness is random,
// so the average lead differs from `delay` by the mean jitter, and trimming
// toward `delay` would move the very alignment this exists to hold.
//
// Single-threaded by design: the device's mic task both feeds and drains it.
class RefJitterBuffer {
 public:
  static constexpr uint32_t RING = 8192;           // 512 ms at 16 kHz
  static constexpr uint32_t MASK = RING - 1;
  static constexpr uint32_t STALE = 16000;         // 1 s without a packet: the stream ended
  static constexpr uint32_t TRIM_EVERY = 8000;     // samples between drift corrections
  static constexpr int32_t TRIM_DEADBAND = 16;     // 1 ms
  static constexpr uint32_t SETTLE_PACKETS = 1000;  // ~4 averaging time constants at 100 packets/s

  explicit RefJitterBuffer(uint32_t delay_samples = 960) : ring_(RING, 0) { this->set_delay(delay_samples); }

  void set_delay(uint32_t samples) {
    this->delay_ = samples > RING / 2 ? RING / 2 : samples;
    this->reset();
  }
  uint32_t delay() const { return this->delay_; }

  void reset() {
    this->primed_ = false;
    for (auto &s : this->ring_) s = 0;
    this->skip_ = this->repeat_ = false;
    this->since_trim_ = 0;
    this->idle_ = 0;
  }

  // `n` big-endian L16 samples starting at RTP timestamp `ts`.
  void put(uint32_t ts, bool marker, const uint8_t *be, size_t n) {
    if (n == 0 || n > RING / 4)
      return;
    const int32_t off = static_cast<int32_t>(ts - this->play_ts_);
    if (!this->primed_ || marker || off <= -static_cast<int32_t>(RING / 2) ||
        off + static_cast<int32_t>(n) > static_cast<int32_t>(RING))
      this->resync_(ts);
    for (size_t k = 0; k < n; ++k) {
      const uint32_t t = ts + static_cast<uint32_t>(k);
      if (static_cast<int32_t>(t - this->play_ts_) < 0) {
        ++this->late_;
        continue;
      }
      this->ring_[t & MASK] = static_cast<int16_t>(static_cast<uint16_t>(be[2 * k] << 8) | be[2 * k + 1]);
    }
    this->idle_ = 0;
    const int32_t lead = static_cast<int32_t>(ts - this->play_ts_);
    this->lead_q8_ += (lead * 256 - this->lead_q8_) / 256;  // ~2.5 s average at 100 packets/s
    ++this->packets_;
    if (++this->since_anchor_ == SETTLE_PACKETS)
      this->target_q8_ = this->lead_q8_;
  }

  // Always produces `n` samples: silence where nothing arrived in time.
  void take(int16_t *out, size_t n) {
    if (!this->primed_) {
      for (size_t i = 0; i < n; ++i) out[i] = 0;
      return;
    }
    for (size_t i = 0; i < n; ++i) {
      if (this->skip_) {
        this->ring_[this->play_ts_ & MASK] = 0;
        ++this->play_ts_;
        this->skip_ = false;
      }
      if (this->repeat_ && i > 0) {
        out[i] = out[i - 1];
        this->repeat_ = false;
        continue;
      }
      out[i] = this->ring_[this->play_ts_ & MASK];
      this->ring_[this->play_ts_ & MASK] = 0;
      ++this->play_ts_;
    }
    this->idle_ += static_cast<uint32_t>(n);
    if (this->idle_ > STALE) {
      this->primed_ = false;  // the next packet starts afresh, wherever it lands
      return;
    }
    this->since_trim_ += static_cast<uint32_t>(n);
    if (this->since_trim_ >= TRIM_EVERY && this->since_anchor_ >= SETTLE_PACKETS) {
      this->since_trim_ = 0;
      const int32_t err_q8 = this->lead_q8_ - this->target_q8_;
      if (err_q8 > TRIM_DEADBAND * 256) {
        this->skip_ = true;  // packets arrive ever earlier: the server clock is fast
        this->lead_q8_ -= 256;
        ++this->trims_;
      } else if (err_q8 < -TRIM_DEADBAND * 256) {
        this->repeat_ = true;
        this->lead_q8_ += 256;
        ++this->trims_;
      }
    }
  }

  bool primed() const { return this->primed_; }
  // Average lead of arrivals over playout, and its drift from where it settled.
  int32_t lead_ms() const { return this->lead_q8_ / 256 / 16; }
  int32_t drift_samples() const {
    return this->since_anchor_ >= SETTLE_PACKETS ? (this->lead_q8_ - this->target_q8_) / 256 : 0;
  }
  uint32_t packets() const { return this->packets_; }
  uint32_t late_samples() const { return this->late_; }
  uint32_t resyncs() const { return this->resyncs_; }
  uint32_t trims() const { return this->trims_; }

 private:
  void resync_(uint32_t ts) {
    for (auto &s : this->ring_) s = 0;
    this->play_ts_ = ts - this->delay_;
    this->lead_q8_ = static_cast<int32_t>(this->delay_) * 256;
    this->skip_ = this->repeat_ = false;
    this->since_trim_ = 0;
    this->since_anchor_ = 0;
    this->target_q8_ = this->lead_q8_;
    this->primed_ = true;
    ++this->resyncs_;
  }

  std::vector<int16_t> ring_;
  uint32_t delay_{0};
  uint32_t play_ts_{0};
  bool primed_{false};
  bool skip_{false}, repeat_{false};
  uint32_t since_trim_{0};
  uint32_t idle_{0};
  int32_t lead_q8_{0};
  int32_t target_q8_{0};
  uint32_t since_anchor_{0};
  uint32_t packets_{0}, late_{0}, resyncs_{0}, trims_{0};
};

}  // namespace rtp_mic_core
