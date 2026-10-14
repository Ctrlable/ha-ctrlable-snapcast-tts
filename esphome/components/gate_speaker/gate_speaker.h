#pragma once

#include <atomic>

#include "esphome/components/speaker/speaker.h"
#include "esphome/core/component.h"

namespace esphome::gate_speaker {

// See __init__.py. play() runs on the media pipeline's decode task, loop() on
// the main loop, hence the atomics.
class GateSpeaker : public Component, public speaker::Speaker {
 public:
  void setup() override;
  void loop() override;
  void dump_config() override;

  size_t play(const uint8_t *data, size_t length) override { return this->play(data, length, 0); }
  size_t play(const uint8_t *data, size_t length, TickType_t ticks_to_wait) override;
  void start() override;
  void stop() override;
  void finish() override;
  void set_pause_state(bool pause_state) override;
  bool get_pause_state() const override;
  bool has_buffered_data() const override;
  void set_volume(float volume) override;
  void set_mute_state(bool mute_state) override;

  void set_output(speaker::Speaker *output) { this->output_ = output; }
  // Takes effect at once; meant to be flipped between answers.
  void set_passthrough(bool passthrough);
  bool get_passthrough() const { return this->passthrough_.load(); }

 protected:
  int64_t played_bytes_(int64_t now_us) const;

  speaker::Speaker *output_{nullptr};
  std::atomic<bool> passthrough_{false};

  // Swallow mode: a virtual DAC draining at the stream's real rate.
  std::atomic<int64_t> start_us_{0};
  std::atomic<int64_t> written_{0};
  std::atomic<bool> finishing_{false};
  bool paused_{false};
  int64_t paused_at_us_{0};
};

}  // namespace esphome::gate_speaker
