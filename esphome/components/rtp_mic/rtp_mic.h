#pragma once

#include "esphome/components/microphone/microphone_source.h"
#include "esphome/core/component.h"

#include "rtp_core.h"
#include "rtp_session.h"

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
  void set_payload_type(uint8_t pt) { this->payload_type_ = pt; }
  void set_packet_ms(uint8_t ms) { this->packet_ms_ = ms; }
  void set_control_port(uint16_t port) { this->control_port_ = port; }
  void set_token(const std::string &token) { this->sessions_ = rtp_mic_core::SessionTable(token); }

  // The "RTP mic service" switch. Off refuses new sessions and drops the
  // current one at once -- the local privacy switch for this mic.
  void set_service_enabled(bool enabled);
  bool is_service_enabled() const { return this->sessions_.enabled(); }

  bool is_streaming() const { return this->streaming_.load(); }
  // "10.1.8.174:46000 (asr)", or "" when nobody is listening.
  std::string get_client() const;
  uint32_t get_packets_sent() const { return this->packets_sent_.load(); }
  uint32_t get_packets_dropped() const { return this->packets_dropped_.load(); }

 protected:
  struct Conn {
    int fd{-1};
    uint32_t ip{0};
    std::string ip_text;
    std::string buf;
  };

  void on_audio_(const std::vector<uint8_t> &data);
  void open_control_();
  void accept_();
  void serve_(Conn &c);
  bool close_(Conn &c);
  void apply_session_();

  microphone::MicrophoneSource *source_{nullptr};
  uint8_t payload_type_{96};
  uint8_t packet_ms_{20};
  uint16_t control_port_{6055};
  uint32_t ssrc_{0};

  rtp_mic_core::SessionTable sessions_;
  int listen_fd_{-1};
  // One live session plus one more connection to refuse politely; a third is
  // closed without a word. This is a mic on a LAN, not a server farm.
  static constexpr size_t MAX_CONNS = 2;
  Conn conns_[MAX_CONNS];

  int sock_{-1};
  std::mutex addr_lock_;
  struct sockaddr_in addr_ {};
  bool addr_valid_{false};

  std::atomic<uint8_t> channel_{0};
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
