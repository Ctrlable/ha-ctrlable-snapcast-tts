#pragma once

#include "esphome/components/microphone/microphone_source.h"
#include "esphome/core/component.h"

#include "rtp_core.h"

#include <atomic>
#include <memory>
#include <mutex>
#include <string>
#include <vector>

#include <lwip/sockets.h>

namespace esphome::rtp_mic {

class RtpMic : public Component {
 public:
  void setup() override;
  void loop() override;
  void dump_config() override;
  float get_setup_priority() const override { return setup_priority::AFTER_WIFI; }

  void set_microphone_source(microphone::MicrophoneSource *source) { this->source_ = source; }
  void set_channel(uint8_t channel) { this->channel_.store(channel); }
  void set_payload_type(uint8_t pt) { this->payload_type_ = pt; }
  void set_packet_ms(uint8_t ms) { this->packet_ms_ = ms; }

  // "host:port", or "" to clear. Resolved in loop(), never in the mic task.
  void set_destination(const std::string &host_port);
  const std::string &get_destination() const { return this->destination_; }

  // Streaming needs both: switched on AND a resolvable destination.
  void set_enabled(bool enabled);
  bool is_enabled() const { return this->enabled_; }
  bool is_streaming() const { return this->streaming_.load(); }
  uint8_t get_channel() const { return this->channel_.load(); }
  uint32_t get_packets_sent() const { return this->packets_sent_.load(); }
  uint32_t get_packets_dropped() const { return this->packets_dropped_.load(); }

 protected:
  void on_audio_(const std::vector<uint8_t> &data);
  void update_state_();
  bool resolve_();

  microphone::MicrophoneSource *source_{nullptr};
  std::atomic<uint8_t> channel_{0};
  uint8_t payload_type_{96};
  uint8_t packet_ms_{20};

  std::string destination_;
  bool destination_dirty_{false};
  bool enabled_{false};

  int sock_{-1};
  std::mutex addr_lock_;
  struct sockaddr_in addr_ {};
  bool addr_valid_{false};

  std::atomic<bool> streaming_{false};
  std::unique_ptr<rtp_mic_core::Decimator> decimator_;
  std::unique_ptr<rtp_mic_core::Packetizer> packetizer_;
  std::vector<int16_t> picked_;
  std::vector<int16_t> decimated_;
  std::atomic<bool> restart_packetizer_{false};

  std::atomic<uint32_t> packets_sent_{0};
  std::atomic<uint32_t> packets_dropped_{0};
  uint32_t last_report_ms_{0};
};

}  // namespace esphome::rtp_mic
