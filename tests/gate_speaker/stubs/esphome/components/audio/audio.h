#pragma once
// Host stub of ESPHome 2026.9's AudioStreamInfo (the members gate_speaker uses).
#include <cstddef>
#include <cstdint>
namespace esphome::audio {
class AudioStreamInfo {
 public:
  AudioStreamInfo(uint8_t bits_per_sample = 16, uint8_t channels = 1, uint32_t sample_rate = 16000)
      : bits_per_sample_(bits_per_sample), channels_(channels), sample_rate_(sample_rate),
        bytes_per_sample_((bits_per_sample + 7) / 8) {}
  uint32_t get_sample_rate() const { return this->sample_rate_; }
  uint32_t bytes_to_frames(size_t bytes) const { return (bytes / (this->bytes_per_sample_ * this->channels_)); }
  size_t frames_to_bytes(uint32_t frames) const { return frames * this->bytes_per_sample_ * this->channels_; }
 protected:
  uint8_t bits_per_sample_;
  uint8_t channels_;
  uint32_t sample_rate_;
  size_t bytes_per_sample_;
};
}  // namespace esphome::audio
