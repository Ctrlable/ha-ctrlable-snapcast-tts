#pragma once

#ifdef USE_ESP32

#include <atomic>
#include <cmath>
#include <memory>
#include <string>

#include <freertos/FreeRTOS.h>
#include <freertos/task.h>

#include "esp_aec.h"
#include "gap_watch.h"

#include "esphome/components/microphone/microphone.h"
#include "esphome/components/ring_buffer/ring_buffer.h"
#include "esphome/components/rtp_mic/rtp_core.h"
#include "esphome/components/network/util.h"
#include "esphome/core/component.h"

namespace esphome::aec_mic {

// See __init__.py. One task owns everything per-sample: it drains the source
// mic's ring, reads the reference socket, feeds and drains the jitter buffer
// (single-threaded by design), runs the AEC and delivers the result -- so the
// source mic's own task only copies bytes, and nothing heavy runs on it.
class AecMic : public Component, public microphone::Microphone {
 public:
  void setup() override;
  void loop() override;
  void dump_config() override;
  float get_setup_priority() const override { return setup_priority::AFTER_WIFI; }

  void start() override;
  void stop() override;

  void set_source(microphone::Microphone *m) { this->source_ = m; }
  void set_reference_port(uint16_t p) { this->ref_port_ = p; }
  void set_reference_from(const std::string &ip) { this->ref_from_text_ = ip; }
  void set_filter_length(int n) { this->filter_length_ = n; }
  void set_aec_mode(int m) { this->aec_mode_ = m; }
  // Takes effect at once (the jitter buffer re-anchors on the next packet).
  void set_delay_ms(uint32_t ms) { this->delay_ms_.store(ms); }
  uint32_t get_delay_ms() const { return this->delay_ms_.load(); }
  // Reference gain in dB, applied before the canceller.
  void set_reference_gain_db(float db) { this->ref_gain_q8_.store((int32_t) (256.0f * powf(10.0f, db / 20.0f))); }

  // Diagnostic: `seconds` of mic / reference (as fed to the AEC) / output,
  // 16 kHz int16 x3 interleaved, sent as one raw TCP stream to host:port.
  void capture(uint32_t seconds, const std::string &host, uint16_t port);

 protected:
  static void task_(void *arg);
  void run_();
  void open_socket_();
  void read_reference_();
  static void send_task_(void *arg);
  void watch_gaps_(size_t samples);

  microphone::Microphone *source_{nullptr};
  uint16_t ref_port_{6057};
  std::string ref_from_text_;
  uint32_t ref_from_{0};  // network order; 0 = anyone
  int filter_length_{8};
  int aec_mode_{1};
  std::atomic<uint32_t> delay_ms_{100};
  std::atomic<int32_t> ref_gain_q8_{256};
  uint32_t applied_delay_ms_{0};
  uint32_t dropped_samples_{0};
  uint32_t realigns_{0};
  std::atomic<bool> realign_{false};
  std::atomic<uint32_t> gap_ms_{0};
  GapWatch gaps_;  // touched only on the source's task

  std::atomic<int> listeners_{0};
  std::atomic<bool> running_{false};
  std::unique_ptr<ring_buffer::RingBuffer> mic_ring_;
  TaskHandle_t task_handle_{nullptr};
  aec_handle_t *aec_{nullptr};
  int chunk_{0};

  int sock_{-1};
  rtp_mic_core::RefJitterBuffer jb_;
  uint8_t pkt_[1500];
  std::atomic<uint32_t> ref_packets_{0}, ref_foreign_{0};

  int16_t *cap_buf_{nullptr};
  uint32_t cap_len_{0};
  std::atomic<uint32_t> cap_pos_{0};
  std::atomic<bool> capturing_{false};
  std::string cap_host_;
  uint16_t cap_port_{0};
};

}  // namespace esphome::aec_mic

#endif  // USE_ESP32
