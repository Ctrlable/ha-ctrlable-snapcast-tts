#pragma once

#include "esphome/components/microphone/microphone_source.h"
#include "esphome/core/component.h"
#include "esphome/core/defines.h"

#ifdef USE_RTP_MIC_REFERENCE
#include "esphome/components/mixer/speaker/mixer_speaker.h"
#include "esphome/components/speaker/speaker.h"
#endif

#include "rtp_core.h"
#include "rtp_session.h"

#include <atomic>
#include <memory>
#include <mutex>
#include <string>
#include <vector>

#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
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

  // The echo reference (see rtp_core.h). `local` = this device plays its
  // answers itself: the XVF then hears its own output and needs no reference,
  // so the device declines it and the server stops sending. Call from the
  // "Play answers on this device" switch.
  void set_answers_local(bool local);
  bool is_reference_active() const { return this->ref_active_.load(); }
#ifdef USE_RTP_MIC_REFERENCE
  void set_reference(speaker::Speaker *source, mixer_speaker::MixerSpeaker *mixer, uint16_t port, uint32_t queue_ms) {
    this->ref_speaker_ = source;
    this->ref_mixer_ = mixer;
    this->ref_port_ = port;
    this->ref_queue_ms_ = queue_ms;
  }
#endif

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
  void apply_reference_();
  void feed_reference_();
#ifdef USE_RTP_MIC_REFERENCE
  static void ref_task_(void *arg);
#endif

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
  std::atomic<uint32_t> mic_stack_free_{0};
  bool stack_reported_{false};

  // Echo reference. Touched by the main loop only to open/close/re-anchor,
  // under ref_lock_; everything per-packet happens on ref_task_, which the
  // mic task only wakes (never on the mic task itself: its 4 KB stack also
  // carries the wake word, and the reference work overflowed it).
  bool answers_local_{false};
  std::atomic<bool> ref_active_{false};
#ifdef USE_RTP_MIC_REFERENCE
  speaker::Speaker *ref_speaker_{nullptr};
  mixer_speaker::MixerSpeaker *ref_mixer_{nullptr};
  uint16_t ref_port_{6056};
  uint32_t ref_queue_ms_{20};
  std::mutex ref_lock_;
  int ref_sock_{-1};
  uint32_t ref_peer_{0};
  uint16_t ref_delay_ms_{0};
  std::unique_ptr<rtp_mic_core::RefJitterBuffer> ref_jb_;
  rtp_mic_core::Upsampler3 ref_up_;
  std::vector<uint8_t> ref_pkt_;
  std::vector<int16_t> ref16_;
  std::vector<int16_t> ref48_;
  uint32_t ref_foreign_{0};
  // Created the first time a reference starts; a device that answers locally
  // never pays for it.
  TaskHandle_t ref_task_handle_{nullptr};
#endif
};

}  // namespace esphome::rtp_mic
