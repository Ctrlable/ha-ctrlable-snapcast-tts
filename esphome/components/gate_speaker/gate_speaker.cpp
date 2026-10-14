#include "gate_speaker.h"

#include <algorithm>

#include <esp_timer.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>

#include "esphome/core/log.h"

namespace esphome::gate_speaker {

static const char *const TAG = "gate_speaker";

// How far ahead of "now" a swallowed stream may be written, like the DMA
// buffer a real speaker has. Short enough that stop() is immediate in effect.
static const int64_t LEAD_MS = 200;

void GateSpeaker::setup() {
  // Playback position for the media player, only while it is real.
  this->output_->add_audio_output_callback([this](uint32_t frames, int64_t write_us) {
    if (this->passthrough_.load())
      this->audio_output_callback_.call(frames, write_us);
  });
}

void GateSpeaker::dump_config() {
  ESP_LOGCONFIG(TAG, "Gate speaker:\n  Passthrough: %s", YESNO(this->passthrough_.load()));
}

void GateSpeaker::set_passthrough(bool passthrough) {
  if (this->passthrough_.exchange(passthrough) == passthrough)
    return;
  if (this->state_ != speaker::STATE_STOPPED)
    this->stop();
  if (passthrough) {
    this->output_->set_volume(this->volume_);
    this->output_->set_mute_state(this->mute_state_);
  } else {
    this->output_->stop();
  }
  ESP_LOGI(TAG, "%s", passthrough ? "playing through the speaker" : "swallowing audio (answers play elsewhere)");
}

int64_t GateSpeaker::played_bytes_(int64_t now_us) const {
  const uint32_t rate = this->audio_stream_info_.get_sample_rate();
  if (rate == 0)
    return this->written_.load();
  const int64_t bytes_per_s = this->audio_stream_info_.frames_to_bytes(rate);
  return (now_us - this->start_us_.load()) * bytes_per_s / 1000000;
}

void GateSpeaker::start() {
  if (this->passthrough_.load()) {
    this->output_->set_audio_stream_info(this->audio_stream_info_);
    this->output_->start();
    this->state_ = speaker::STATE_STARTING;
    return;
  }
  this->start_us_.store(esp_timer_get_time());
  this->written_.store(0);
  this->finishing_.store(false);
  this->state_ = speaker::STATE_RUNNING;
}

size_t GateSpeaker::play(const uint8_t *data, size_t length, TickType_t ticks_to_wait) {
  if (this->passthrough_.load()) {
    if (this->output_->is_stopped())
      this->output_->set_audio_stream_info(this->audio_stream_info_);
    if (this->state_ == speaker::STATE_STOPPED)
      this->state_ = speaker::STATE_STARTING;
    return this->output_->play(data, length, ticks_to_wait);
  }
  if (this->state_ != speaker::STATE_RUNNING)
    this->start();
  const size_t frame = std::max<size_t>(1, this->audio_stream_info_.frames_to_bytes(1));
  const uint32_t rate = this->audio_stream_info_.get_sample_rate();
  const int64_t lead = rate ? this->audio_stream_info_.frames_to_bytes(rate) * LEAD_MS / 1000 : (int64_t) length;
  TickType_t waited = 0;
  for (;;) {
    const int64_t now = esp_timer_get_time();
    const int64_t room = this->paused_ ? 0 : this->played_bytes_(now) + lead - this->written_.load();
    size_t n = room > 0 ? std::min<size_t>(length, (size_t) room) : 0;
    n -= n % frame;
    if (n > 0) {
      this->written_.fetch_add((int64_t) n);
      this->audio_output_callback_.call(this->audio_stream_info_.bytes_to_frames(n), now);
      return n;
    }
    if (waited >= ticks_to_wait)
      return 0;
    vTaskDelay(1);
    ++waited;
  }
}

bool GateSpeaker::has_buffered_data() const {
  if (this->passthrough_.load())
    return this->output_->has_buffered_data();
  return this->state_ == speaker::STATE_RUNNING && this->written_.load() > this->played_bytes_(esp_timer_get_time());
}

void GateSpeaker::finish() {
  if (this->passthrough_.load()) {
    this->output_->finish();
    return;
  }
  this->finishing_.store(true);
}

void GateSpeaker::stop() {
  if (this->passthrough_.load())
    this->output_->stop();
  this->finishing_.store(false);
  this->written_.store(0);
  this->state_ = speaker::STATE_STOPPED;
}

void GateSpeaker::loop() {
  if (this->passthrough_.load()) {
    // Mirror the real speaker, which starts and stops on its own task.
    if (this->output_->is_running())
      this->state_ = speaker::STATE_RUNNING;
    else if (this->output_->is_stopped())
      this->state_ = speaker::STATE_STOPPED;
    return;
  }
  if (this->finishing_.load() && !this->has_buffered_data()) {
    this->finishing_.store(false);
    this->state_ = speaker::STATE_STOPPED;
  }
}

void GateSpeaker::set_pause_state(bool pause_state) {
  if (this->passthrough_.load()) {
    this->output_->set_pause_state(pause_state);
    return;
  }
  if (pause_state == this->paused_)
    return;
  // Shift the virtual clock by the pause, so it is not counted as played and
  // what was queued before it still drains afterwards.
  if (pause_state)
    this->paused_at_us_ = esp_timer_get_time();
  else
    this->start_us_.fetch_add(esp_timer_get_time() - this->paused_at_us_);
  this->paused_ = pause_state;
}

bool GateSpeaker::get_pause_state() const {
  return this->passthrough_.load() ? this->output_->get_pause_state() : this->paused_;
}

void GateSpeaker::set_volume(float volume) {
  this->volume_ = volume;
  if (this->passthrough_.load())
    this->output_->set_volume(volume);
}

void GateSpeaker::set_mute_state(bool mute_state) {
  this->mute_state_ = mute_state;
  if (this->passthrough_.load())
    this->output_->set_mute_state(mute_state);
}

}  // namespace esphome::gate_speaker
