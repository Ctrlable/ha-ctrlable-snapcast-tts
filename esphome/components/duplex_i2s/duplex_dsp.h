#pragma once

// Plain C++ (no ESPHome, no IDF) so the host test can run it on a laptop:
//   c++ -std=c++17 -I esphome/components tests/duplex_i2s/test_duplex_dsp.cpp
//
// The bus runs at 48 kHz both ways -- one clock for the ES8311 and the ES7210
// -- and the wake word, the stop word and the voice assistant all want 16 kHz,
// so the mic side picks one slot of the stereo frame and decimates by 3.

#include <cstddef>
#include <cstdint>

#include "decim_taps.h"

namespace esphome::duplex_i2s {

// 48 kHz -> 16 kHz. Stateful: blocks of any length, any split, give exactly
// the same output as one long block (the test checks this).
class Decimator3 {
 public:
  void reset() {
    for (int i = 0; i < 2 * DECIM_TAPS; i++)
      this->hist_[i] = 0;
    this->pos_ = 0;
    this->phase_ = 0;
  }

  // Returns the number of 16 kHz samples written to `out` (at most n/3 + 1).
  size_t process(const int16_t *in, size_t n, int16_t *out) {
    size_t produced = 0;
    for (size_t i = 0; i < n; i++) {
      // The history is stored twice, so the newest DECIM_TAPS samples are always
      // contiguous at hist_[pos_ .. pos_ + DECIM_TAPS) -- no wrap in the inner
      // loop (it runs 4 x 16000 times a second on the ESP32-S3).
      this->pos_ = this->pos_ == 0 ? DECIM_TAPS - 1 : this->pos_ - 1;
      this->hist_[this->pos_] = in[i];
      this->hist_[this->pos_ + DECIM_TAPS] = in[i];
      if (++this->phase_ < 3)
        continue;
      this->phase_ = 0;
      const int16_t *h = &this->hist_[this->pos_];  // h[0] newest
      int32_t acc = 0;
      for (int k = 0; k < DECIM_TAPS; k++)
        acc += (int32_t) DECIM_COEFFS[k] * h[k];
      acc = (acc + (1 << 14)) >> 15;
      out[produced++] = (int16_t) (acc > 32767 ? 32767 : (acc < -32768 ? -32768 : acc));
    }
    return produced;
  }

 protected:
  int16_t hist_[2 * DECIM_TAPS]{};
  int pos_{0};
  int phase_{0};
};

// Is the speaker making sound, or did it recently? The echo canceller only
// has work while it is: with nothing playing the mic passes straight through
// and the CPU stays with the wake word (on the CoreS3 an always-on canceller
// left micro_wake_word starved, 2026-10-18). `hangover` covers the room's
// echo tail after the last sound.
class PlaybackGate {
 public:
  PlaybackGate(int16_t threshold, uint32_t hangover) : threshold_(threshold), hangover_(hangover) {}

  // One chunk of the reference; true while the canceller should run.
  bool feed(const int16_t *ref, size_t n) {
    bool loud = false;
    for (size_t i = 0; i < n && !loud; i++)
      loud = ref[i] > this->threshold_ || ref[i] < -this->threshold_;
    if (loud) {
      this->quiet_ = 0;
    } else if (this->quiet_ < this->hangover_) {
      this->quiet_ += (uint32_t) n;
    }
    return this->quiet_ < this->hangover_;
  }

 protected:
  int16_t threshold_;
  uint32_t hangover_;
  uint32_t quiet_{0xFFFFFFFFu >> 1};  // starts idle: nothing has played yet
};

// Stereo 16-bit frames (L, R, L, R, ...) -> one slot.
inline void pick_slot(const int16_t *stereo, size_t frames, int slot, int16_t *mono) {
  for (size_t i = 0; i < frames; i++)
    mono[i] = stereo[2 * i + slot];
}

// Mono -> the same sample in both slots: the ES8311 takes the left one, but
// which slot a codec reads is a register away from changing, so feed both.
inline void mono_to_stereo(const int16_t *mono, size_t frames, int16_t *stereo) {
  for (size_t i = 0; i < frames; i++) {
    stereo[2 * i] = mono[i];
    stereo[2 * i + 1] = mono[i];
  }
}

}  // namespace esphome::duplex_i2s
