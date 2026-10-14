#pragma once
// Host copy of ESPHome 2026.9.1's speaker::Speaker, minus the audio_dac and
// the USE_ESP32 guard (the TickType_t overload is what the media pipeline calls).
#include <cstddef>
#include <cstdint>
#include <vector>
#include <freertos/FreeRTOS.h>
#include "esphome/core/helpers.h"
#include "esphome/components/audio/audio.h"

namespace esphome::speaker {

enum State : uint8_t { STATE_STOPPED = 0, STATE_STARTING, STATE_RUNNING, STATE_STOPPING };

class Speaker {
 public:
  virtual ~Speaker() = default;
  virtual size_t play(const uint8_t *data, size_t length, TickType_t /*ticks_to_wait*/) { return this->play(data, length); }
  virtual size_t play(const uint8_t *data, size_t length) = 0;
  virtual void start() = 0;
  virtual void stop() = 0;
  virtual void finish() { this->stop(); }
  virtual void set_pause_state(bool /*pause_state*/) {}
  virtual bool get_pause_state() const { return false; }
  virtual bool has_buffered_data() const = 0;
  bool is_running() const { return this->state_ == STATE_RUNNING; }
  bool is_stopped() const { return this->state_ == STATE_STOPPED; }
  virtual void set_volume(float volume) { this->volume_ = volume; }
  virtual float get_volume() { return this->volume_; }
  virtual void set_mute_state(bool mute_state) { this->mute_state_ = mute_state; }
  virtual bool get_mute_state() { return this->mute_state_; }
  void set_audio_stream_info(const audio::AudioStreamInfo &info) { this->audio_stream_info_ = info; }
  audio::AudioStreamInfo &get_audio_stream_info() { return this->audio_stream_info_; }
  template<typename F> void add_audio_output_callback(F &&callback) {
    this->audio_output_callback_.add(std::forward<F>(callback));
  }

 protected:
  State state_{STATE_STOPPED};
  audio::AudioStreamInfo audio_stream_info_;
  float volume_{1.0f};
  bool mute_state_{false};
  CallbackManager<void(uint32_t, int64_t)> audio_output_callback_{};
};

}  // namespace esphome::speaker
