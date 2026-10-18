#pragma once

#ifdef USE_ESP32

#include <atomic>
#include <memory>
#include <string>

#include <driver/i2s_std.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>

#include "esphome/components/microphone/microphone.h"
#include "esphome/components/ring_buffer/ring_buffer.h"
#include "esphome/components/speaker/speaker.h"
#include "esphome/core/component.h"
#include "esphome/core/helpers.h"

#include "duplex_dsp.h"

#ifdef USE_DUPLEX_I2S_AEC
#include "esp_aec.h"
#endif
#ifdef USE_DUPLEX_I2S_AFE
#include "esp_afe_sr_iface.h"
#include "esp_afe_sr_models.h"
#endif

namespace esphome::duplex_i2s {

// What the speaker task sent, sample for sample, for the microphone task's
// echo canceller. One producer, one consumer. TX and RX run off the same
// clock, so the two counts advance in lockstep and the delay between a sample
// leaving and its echo arriving stays fixed -- that is all an AEC needs.
class RefRing {
 public:
  static constexpr uint32_t SIZE = 8192;  // power of two; ~170 ms at 48 kHz
  void push(const int16_t *s, uint32_t n) {
    uint32_t h = this->head_.load(std::memory_order_relaxed);
    for (uint32_t i = 0; i < n; i++)
      this->buf_[(h + i) & (SIZE - 1)] = s[i];
    this->head_.store(h + n, std::memory_order_release);
  }
  // Pops exactly n samples (zeros if not there yet). Keeps the backlog
  // bounded so a stall on either side cannot grow the delay without limit.
  void pop(int16_t *d, uint32_t n) {
    uint32_t h = this->head_.load(std::memory_order_acquire);
    uint32_t t = this->tail_;
    if (h - t > SIZE / 2)
      t = h - SIZE / 4;  // fell behind: resync (the AEC re-converges)
    for (uint32_t i = 0; i < n; i++) {
      if (t != h) {
        d[i] = this->buf_[t & (SIZE - 1)];
        t++;
      } else {
        d[i] = 0;
      }
    }
    this->tail_ = t;
  }

 protected:
  int16_t buf_[SIZE]{};
  std::atomic<uint32_t> head_{0};
  uint32_t tail_{0};
};

// See __init__.py. Owns the port: TX and RX are created together, on one
// clock, at setup, and stay enabled for good.
class DuplexI2S : public Component {
 public:
  void setup() override;
  void dump_config() override;
  float get_setup_priority() const override { return setup_priority::HARDWARE; }

  void set_pins(int mclk, int bclk, int lrclk, int dout, int din) {
    this->mclk_ = mclk;
    this->bclk_ = bclk;
    this->lrclk_ = lrclk;
    this->dout_ = dout;
    this->din_ = din;
  }
  void set_sample_rate(uint32_t rate) { this->sample_rate_ = rate; }
  void set_port(int port) { this->port_ = port; }
  // 2: standard stereo I2S. 4: TDM, four 16-bit slots per frame -- how the
  // ES7210 delivers MIC1..MIC4 (MIC3 = the speaker copy) on one data line.
  void set_slots(int slots) { this->slots_ = slots; }
  int get_slots() const { return this->slots_; }
  // TDM framing: 0 Philips, 1 MSB, 2 PCM short, 3 PCM long.
  void set_tdm_format(int f) { this->tdm_format_ = f; }
  void set_slot_width(int w) { this->slot_width_ = w; }
  // Bytes per sample word in memory: 2 for 16-bit slots, 4 for 32-bit.
  int get_word_bytes() const { return this->slots_ == 4 && this->slot_width_ == 32 ? 4 : 2; }

  uint32_t get_sample_rate() const { return this->sample_rate_; }
  i2s_chan_handle_t tx() const { return this->tx_; }
  i2s_chan_handle_t rx() const { return this->rx_; }
  bool ready() const { return this->tx_ != nullptr && this->rx_ != nullptr; }
  RefRing &ref_ring() { return this->ref_ring_; }

 protected:
  int mclk_, bclk_, lrclk_, dout_, din_;
  uint32_t sample_rate_{48000};
  int port_{0};
  int slots_{2};
  int tdm_format_{2};
  int slot_width_{16};
  i2s_chan_handle_t tx_{nullptr};
  i2s_chan_handle_t rx_{nullptr};
  RefRing ref_ring_;
};

// Speaker. play() runs on the media pipeline's task, the TX task on its own,
// loop() on the main loop -- hence the atomics.
class DuplexSpeaker : public Component, public speaker::Speaker, public Parented<DuplexI2S> {
 public:
  void setup() override;
  void loop() override;
  void dump_config() override;
  float get_setup_priority() const override { return setup_priority::HARDWARE - 1.0f; }

  size_t play(const uint8_t *data, size_t length) override { return this->play(data, length, 0); }
  size_t play(const uint8_t *data, size_t length, TickType_t ticks_to_wait) override;
  void start() override;
  void stop() override;
  void finish() override;
  void set_pause_state(bool pause_state) override { this->paused_.store(pause_state); }
  bool get_pause_state() const override { return this->paused_.load(); }
  bool has_buffered_data() const override;

  void set_buffer_duration(uint32_t ms) { this->buffer_ms_ = ms; }

 protected:
  static void tx_task_(void *arg);
  void run_tx_();

  uint32_t buffer_ms_{500};
  std::unique_ptr<ring_buffer::RingBuffer> ring_;
  TaskHandle_t task_{nullptr};
  std::atomic<bool> paused_{false};
  std::atomic<bool> finishing_{false};
  std::atomic<bool> stop_requested_{false};  // the TX task empties the ring, never another task
  std::atomic<uint32_t> last_real_ms_{0};
  std::atomic<uint8_t> in_channels_{1};
};

// Microphone: 16 kHz mono 16-bit out of one slot of the 48 kHz stereo RX.
class DuplexMicrophone : public Component, public microphone::Microphone, public Parented<DuplexI2S> {
 public:
  void setup() override;
  void loop() override;
  void dump_config() override;
  float get_setup_priority() const override { return setup_priority::HARDWARE - 1.0f; }

  void start() override;
  void stop() override;

  void set_slot(int slot) { this->slot_ = slot; }
  // The slot carrying a copy of what the speaker plays; -1: none.
  void set_reference_slot(int slot) { this->ref_slot_ = slot; }
  // Use the samples the speaker task sent as the echo reference (no wiring
  // needed: the Waveshare 1.85C has no hardware loopback, measured).
  void set_reference_from_speaker(bool on) { this->ref_from_tx_ = on; }
  // Diagnostic: record `seconds` of mic / reference / AEC output (16 kHz,
  // interleaved int16 x3) and send it as one raw TCP stream to host:port.
  void capture(uint32_t seconds, const std::string &host, uint16_t port);
  // ESP-SR aec_mode_t value (0 SR low cost, 1 SR high perf, 5/6 full duplex).
  void set_aec_mode(int mode) { this->aec_mode_ = mode; }
  // Residual-echo suppression after the linear filter: -1 off (linear only),
  // 0 normal, 1 aggressive (ESP-SR's default), 2 very aggressive.
  void set_aec_nlp(int level) { this->aec_nlp_ = level; }
  // ESP-SR's full front end instead of the bare AEC: both mics + reference
  // (TDM only), echo cancelled per mic, then the mics combined. `format` is
  // one letter per slot: M mic, R reference, N unused (this board: "MRMN").
  void set_afe(const std::string &format) { this->afe_format_ = format; }
  // Logs the 16 kHz signal's RMS / peak / clipped samples once a second.
  void set_level_log(bool on) { this->level_log_ = on; }

 protected:
  static void rx_task_(void *arg);
  void run_rx_();
  // Only reads the I2S into rx_ring_, so a slow AFE feed() can never make
  // the 30 ms of RX DMA overflow (it did: audio arrived at ~0.8x real time).
  static void reader_task_(void *arg);
  std::unique_ptr<ring_buffer::RingBuffer> rx_ring_;

  int slot_{1};
  int ref_slot_{-1};
  bool ref_from_tx_{false};
  int aec_mode_{0};
  int aec_nlp_{0};
  std::string afe_format_;
#ifdef USE_DUPLEX_I2S_AFE
  static void fetch_task_(void *arg);
  void run_fetch_();
  const esp_afe_sr_iface_t *afe_iface_{nullptr};
  esp_afe_sr_data_t *afe_data_{nullptr};
  int afe_feed_chunk_{0};
  std::atomic<uint32_t> cap_pos_out_{0};
#endif
  Decimator3 slot_decim_[4];
  static void send_task_(void *arg);
  int16_t *cap_buf_{nullptr};
  uint32_t cap_len_{0};                  // frames (x3 samples)
  std::atomic<uint32_t> cap_pos_{0};
  std::atomic<bool> capturing_{false};
  std::string cap_host_;
  uint16_t cap_port_{0};
  bool level_log_{false};
  Decimator3 ref_decim_;
#ifdef USE_DUPLEX_I2S_AEC
  aec_handle_t *aec_{nullptr};
  int aec_chunk_{0};
#endif
  std::atomic<int> listeners_{0};
  std::atomic<bool> running_{false};  // what the RX task reads; state_ is the main loop's
  TaskHandle_t task_{nullptr};
  Decimator3 decim_;
};

}  // namespace esphome::duplex_i2s

#endif  // USE_ESP32
