#pragma once

// Plain C++ (no IDF) so the host test can run it:
//   c++ -std=c++17 -I esphome/components tests/aec_mic/test_gap_watch.cpp
//
// Counts mic samples against a clock. The lowest (clock - samples) over each
// 500 ms window only rises when samples went missing: a late callback is made
// up by the next ones within the window, a lost block never is.

#include <cstddef>
#include <cstdint>

namespace esphome::aec_mic {

class GapWatch {
 public:
  static constexpr int64_t WINDOW = 8000;    // 500 ms at 16 kHz
  static constexpr int64_t THRESHOLD = 480;  // 30 ms: well above callback jitter

  // `now` in 16 kHz samples. Returns the samples found missing (0 if none).
  int64_t feed(int64_t now, size_t samples) {
    this->total_ += (int64_t) samples;
    const int64_t d = now - this->total_;
    if (!this->primed_) {
      this->primed_ = true;
      this->floor_ = this->win_min_ = d;
      this->win_start_ = now;
      return 0;
    }
    if (d < this->win_min_)
      this->win_min_ = d;
    if (now - this->win_start_ < WINDOW)
      return 0;
    int64_t lost = this->win_min_ - this->floor_;
    // The floor follows the window minimum down at once, up only past the
    // threshold: window minima wander a little with jitter, and those small
    // rises must not add up.
    if (lost > THRESHOLD || lost < 0)
      this->floor_ = this->win_min_;
    this->win_min_ = d;
    this->win_start_ = now;
    return lost > THRESHOLD ? lost : 0;
  }

 protected:
  bool primed_{false};
  int64_t total_{0}, floor_{0}, win_min_{0}, win_start_{0};
};

}  // namespace esphome::aec_mic
