#include "duplex_i2s.h"

#ifdef USE_ESP32

#include <algorithm>
#include <cmath>
#include <cstring>
#include <vector>

#include <driver/i2s_tdm.h>
#include <esp_heap_caps.h>
#include <freertos/idf_additions.h>
#include <esp_timer.h>
#include <lwip/sockets.h>
#include <lwip/netdb.h>

#include "esphome/core/hal.h"
#include "esphome/core/log.h"

namespace esphome::duplex_i2s {

static const char *const TAG = "duplex_i2s";

// 5 ms per DMA buffer at 48 kHz, 6 of them: 30 ms of slack either way. The
// TX task blocks on the DMA, which is what paces it.
static const uint32_t DMA_FRAMES = 240;
static const uint32_t DMA_DESCS = 6;
static const uint32_t TX_FRAMES = DMA_FRAMES;
// 16 ms of RX per read, as i2s_audio's microphone does: 768 frames -> 256 out.
static const uint32_t RX_FRAMES = 768;
static const UBaseType_t TASK_PRIORITY = 23;

// ---------------------------------------------------------------- the port --

void DuplexI2S::setup() {
  i2s_chan_config_t cc = I2S_CHANNEL_DEFAULT_CONFIG((i2s_port_t) this->port_, I2S_ROLE_MASTER);
  cc.dma_desc_num = DMA_DESCS;
  cc.dma_frame_num = DMA_FRAMES;
  cc.auto_clear = true;  // an underrun sends silence, never the last buffer again
  esp_err_t err = i2s_new_channel(&cc, &this->tx_, &this->rx_);
  if (err != ESP_OK) {
    ESP_LOGE(TAG, "i2s_new_channel: %s", esp_err_to_name(err));
    this->mark_failed();
    return;
  }

  if (this->slots_ == 4) {
    const i2s_data_bit_width_t dw = this->slot_width_ == 32 ? I2S_DATA_BIT_WIDTH_32BIT : I2S_DATA_BIT_WIDTH_16BIT;
    const auto mask = (i2s_tdm_slot_mask_t) (I2S_TDM_SLOT0 | I2S_TDM_SLOT1 | I2S_TDM_SLOT2 | I2S_TDM_SLOT3);
    i2s_tdm_slot_config_t slot;
    switch (this->tdm_format_) {
      case 1:
        slot = I2S_TDM_MSB_SLOT_DEFAULT_CONFIG(dw, I2S_SLOT_MODE_STEREO, mask);
        break;
      case 2:
        slot = I2S_TDM_PCM_SHORT_SLOT_DEFAULT_CONFIG(dw, I2S_SLOT_MODE_STEREO, mask);
        break;
      case 3:
        slot = I2S_TDM_PCM_LONG_SLOT_DEFAULT_CONFIG(dw, I2S_SLOT_MODE_STEREO, mask);
        break;
      default:
        slot = I2S_TDM_PHILIPS_SLOT_DEFAULT_CONFIG(dw, I2S_SLOT_MODE_STEREO, mask);
    }
    // The ES7210 sends MIC1..MIC4 only in its DSP/PCM modes, packed in 16-bit
    // slots straight after the frame sync; in I2S framing it sends just
    // MIC1/MIC2, one per half frame (measured 2026-10-17). The ES8311 must be
    // put in its DSP mode to match -- see the satellite yaml.
    i2s_tdm_config_t cfg = {
        .clk_cfg = I2S_TDM_CLK_DEFAULT_CONFIG(this->sample_rate_),
        .slot_cfg = slot,
        .gpio_cfg =
            {
                .mclk = (gpio_num_t) this->mclk_,
                .bclk = (gpio_num_t) this->bclk_,
                .ws = (gpio_num_t) this->lrclk_,
                .dout = (gpio_num_t) this->dout_,
                .din = (gpio_num_t) this->din_,
                .invert_flags = {.mclk_inv = false, .bclk_inv = false, .ws_inv = false},
            },
    };
    cfg.clk_cfg.mclk_multiple = I2S_MCLK_MULTIPLE_256;  // 12.288 MHz: what both codecs' tables expect
    if ((err = i2s_channel_init_tdm_mode(this->tx_, &cfg)) != ESP_OK ||
        (err = i2s_channel_init_tdm_mode(this->rx_, &cfg)) != ESP_OK) {
      ESP_LOGE(TAG, "TDM init: %s", esp_err_to_name(err));
      this->mark_failed();
      return;
    }
  } else {
    i2s_std_config_t cfg = {
        .clk_cfg = I2S_STD_CLK_DEFAULT_CONFIG(this->sample_rate_),
        .slot_cfg = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_16BIT, I2S_SLOT_MODE_STEREO),
        .gpio_cfg =
            {
                .mclk = (gpio_num_t) this->mclk_,
                .bclk = (gpio_num_t) this->bclk_,
                .ws = (gpio_num_t) this->lrclk_,
                .dout = (gpio_num_t) this->dout_,
                .din = (gpio_num_t) this->din_,
                .invert_flags = {.mclk_inv = false, .bclk_inv = false, .ws_inv = false},
            },
    };
    cfg.clk_cfg.mclk_multiple = I2S_MCLK_MULTIPLE_256;
    if ((err = i2s_channel_init_std_mode(this->tx_, &cfg)) != ESP_OK ||
        (err = i2s_channel_init_std_mode(this->rx_, &cfg)) != ESP_OK) {
      ESP_LOGE(TAG, "I2S init: %s", esp_err_to_name(err));
      this->mark_failed();
      return;
    }
  }
  if ((err = i2s_channel_enable(this->tx_)) != ESP_OK || (err = i2s_channel_enable(this->rx_)) != ESP_OK) {
    ESP_LOGE(TAG, "enabling the port: %s", esp_err_to_name(err));
    this->mark_failed();
  }
}

void DuplexI2S::dump_config() {
  ESP_LOGCONFIG(TAG,
                "Duplex I2S:\n"
                "  Port: %d, %" PRIu32 " Hz, 16-bit, %s, MCLK x256\n"
                "  MCLK %d  BCLK %d  LRCLK %d  DOUT %d  DIN %d",
                this->port_, this->sample_rate_,
                this->slots_ == 4 ? (this->slot_width_ == 32 ? "TDM 4 x 32-bit slots" : "TDM 4 x 16-bit slots") : "stereo",
                this->mclk_,
                this->bclk_, this->lrclk_, this->dout_, this->din_);
  if (this->is_failed())
    ESP_LOGE(TAG, "  FAILED to start");
}

// ------------------------------------------------------------- the speaker --

void DuplexSpeaker::setup() {
  // Room for stereo input too; the media player sends mono here.
  const size_t bytes = (size_t) this->buffer_ms_ * 48 * 4;
  this->ring_ = ring_buffer::RingBuffer::create(bytes);
  if (this->ring_ == nullptr) {
    ESP_LOGE(TAG, "speaker: no memory for a %u byte buffer", (unsigned) bytes);
    this->mark_failed();
    return;
  }
  // Stacks in PSRAM: the AFE leaves too little internal DMA-capable RAM for the
  // display's frame transfers otherwise ("spi: Transmit failed - err 101").
  if (xTaskCreatePinnedToCoreWithCaps(DuplexSpeaker::tx_task_, "duplex_tx", 4096, this, TASK_PRIORITY, &this->task_,
                                      tskNO_AFFINITY, MALLOC_CAP_SPIRAM) != pdPASS) {
    ESP_LOGE(TAG, "speaker: task failed to start");
    this->mark_failed();
  }
}

void DuplexSpeaker::dump_config() {
  ESP_LOGCONFIG(TAG, "Duplex speaker:\n  Buffer: %" PRIu32 " ms", this->buffer_ms_);
}

void DuplexSpeaker::start() {
  if (this->is_failed() || !this->parent_->ready())
    return;
  const auto &info = this->audio_stream_info_;
  if (info.get_bits_per_sample() != 16 || info.get_sample_rate() != this->parent_->get_sample_rate() ||
      info.get_channels() < 1 || info.get_channels() > 2) {
    ESP_LOGE(TAG, "speaker: cannot play %u-bit %u ch %" PRIu32 " Hz (bus is 16-bit %" PRIu32 " Hz)",
             info.get_bits_per_sample(), info.get_channels(), info.get_sample_rate(),
             this->parent_->get_sample_rate());
    return;
  }
  this->in_channels_.store(info.get_channels());
  this->finishing_.store(false);
  this->state_ = speaker::STATE_RUNNING;
}

size_t DuplexSpeaker::play(const uint8_t *data, size_t length, TickType_t ticks_to_wait) {
  if (this->state_ != speaker::STATE_RUNNING)
    this->start();
  if (this->state_ != speaker::STATE_RUNNING)
    return 0;
  // A stop() just before must not wipe what arrives right after it: wait for
  // the TX task to have emptied the ring first.
  for (int i = 0; i < 20 && this->stop_requested_.load(); i++)
    vTaskDelay(1);
  return this->ring_->write_without_replacement(data, length, ticks_to_wait);
}

void DuplexSpeaker::stop() {
  this->stop_requested_.store(true);
  this->finishing_.store(false);
  this->state_ = speaker::STATE_STOPPED;
}

void DuplexSpeaker::finish() { this->finishing_.store(true); }

bool DuplexSpeaker::has_buffered_data() const { return this->ring_ != nullptr && this->ring_->available() > 0; }

void DuplexSpeaker::loop() {
  // Finished once the ring is empty and the DMA has had time to play the
  // last of it (6 x 5 ms of descriptors).
  if (this->state_ == speaker::STATE_RUNNING && this->finishing_.load() && this->ring_->available() == 0 &&
      millis() - this->last_real_ms_.load() > 50) {
    this->finishing_.store(false);
    this->state_ = speaker::STATE_STOPPED;
  }
}

void DuplexSpeaker::tx_task_(void *arg) { static_cast<DuplexSpeaker *>(arg)->run_tx_(); }

void DuplexSpeaker::run_tx_() {
  const int slots = this->parent_->get_slots();
  std::vector<uint8_t> in(TX_FRAMES * 4 + 4);  // up to stereo, plus a partial frame carried over
  std::vector<int16_t> st(TX_FRAMES * 2);       // stereo
  std::vector<int16_t> out(TX_FRAMES * 2);       // stereo mode: what goes on the wire
  const bool wide = this->parent_->get_word_bytes() == 4;
  std::vector<int32_t> out32(wide ? TX_FRAMES * 4 : 0);   // TDM, 32-bit words
  std::vector<int16_t> out16(slots == 4 && !wide ? TX_FRAMES * 4 : 0);  // TDM, 16-bit words
  size_t carry = 0;
  for (;;) {
    if (this->stop_requested_.load()) {
      this->ring_->reset();
      carry = 0;
      this->stop_requested_.store(false);
    }
    const size_t bpf = 2 * this->in_channels_.load();  // bytes per input frame
    size_t frames = 0;
    if (!this->paused_.load()) {
      size_t got = this->ring_->read(in.data() + carry, TX_FRAMES * bpf - carry, 0);
      size_t total = carry + got;
      frames = total / bpf;
      const int16_t *src = reinterpret_cast<const int16_t *>(in.data());
      if (bpf == 2) {
        mono_to_stereo(src, frames, st.data());
      } else {
        std::memcpy(st.data(), src, frames * 4);
      }
      carry = total - frames * bpf;
      if (carry)
        std::memmove(in.data(), in.data() + frames * bpf, carry);
    }
    if (frames < TX_FRAMES)
      std::memset(st.data() + frames * 2, 0, (TX_FRAMES - frames) * 4);
    // The echo reference: exactly what goes out, silence included, so the
    // ring advances one sample per frame like the RX side.
    {
      int16_t left[TX_FRAMES];
      for (size_t i = 0; i < TX_FRAMES; i++)
        left[i] = st[2 * i];
      this->parent_->ref_ring().push(left, TX_FRAMES);
    }
    size_t written = 0;
    if (slots == 4 && wide) {
      // L, R, L, R: the DAC reads one slot as left and another as right; which
      // depends on its format, so every slot carries the signal.
      for (size_t i = 0; i < TX_FRAMES; i++) {
        out32[4 * i] = out32[4 * i + 2] = (int32_t) st[2 * i] << 16;
        out32[4 * i + 1] = out32[4 * i + 3] = (int32_t) st[2 * i + 1] << 16;
      }
      i2s_channel_write(this->parent_->tx(), out32.data(), TX_FRAMES * 16, &written, portMAX_DELAY);
    } else if (slots == 4) {
      for (size_t i = 0; i < TX_FRAMES; i++) {
        out16[4 * i] = out16[4 * i + 2] = st[2 * i];
        out16[4 * i + 1] = out16[4 * i + 3] = st[2 * i + 1];
      }
      i2s_channel_write(this->parent_->tx(), out16.data(), TX_FRAMES * 8, &written, portMAX_DELAY);
    } else {
      std::memcpy(out.data(), st.data(), TX_FRAMES * 4);
      i2s_channel_write(this->parent_->tx(), out.data(), TX_FRAMES * 4, &written, portMAX_DELAY);
    }
    if (frames > 0) {
      this->last_real_ms_.store(millis());
      this->audio_output_callback_(frames, esp_timer_get_time());
    }
  }
}

// ---------------------------------------------------------- the microphone --

void DuplexMicrophone::setup() {
  this->decim_.reset();
  this->ref_decim_.reset();
  this->audio_stream_info_ = audio::AudioStreamInfo(16, 1, 16000);
#ifdef USE_DUPLEX_I2S_AEC
  if (this->ref_slot_ >= 0 || this->ref_from_tx_) {
    // Speech-recognition AEC: built to keep a wake word working over the
    // device's own playback. 4 x 16 ms of echo tail is plenty for a speaker
    // two centimetres away.
    this->aec_ = aec_create(16000, 4, 1, (aec_mode_t) this->aec_mode_);
    if (this->aec_ == nullptr) {
      ESP_LOGE(TAG, "microphone: aec_create failed; running without echo cancellation");
    } else {
      this->aec_chunk_ = aec_get_chunksize(this->aec_);
      // Talking over the device ("stop" mid-answer) looks like echo to an
      // aggressive suppressor: on a capture the spoken "stop"s came out QUIETER
      // than the residual echo around them (2026-10-18).
      if (this->aec_nlp_ >= 0)
        aec_set_nlp_level(this->aec_, (aec_nlp_level_t) this->aec_nlp_);
    }
  }
#endif
#ifdef USE_DUPLEX_I2S_AFE
  if (!this->afe_format_.empty()) {
    for (auto &d : this->slot_decim_)
      d.reset();
    // LOW_COST: HIGH_PERF with two mics ran at ~0.7x real time beside the
    // face animation (one log line per 1.4 s instead of 1 s), i.e. it would
    // drop audio.
    afe_config_t *cfg = afe_config_init(this->afe_format_.c_str(), nullptr, AFE_TYPE_FD, AFE_MODE_LOW_COST);
    if (cfg != nullptr) {
      // As the working 1.85C-BOX V2 profile in n-IA-hane/esphome-intercom
      // (esp_afe: type fd, high_perf, mic_num 2, aec_filter_length 4, nlp
      // normal, more_psram). Wake word, VAD and AGC stay with ESPHome.
      cfg->aec_init = true;
      cfg->aec_mode = (aec_mode_t) 5;  // AEC_MODE_FD_LOW_COST, matching the AFE type/mode
      // 2, not the profile's 4: the speaker sits 2 cm from the mics, so the
      // echo path is short, and the per-mic AEC was most of core 0.
      cfg->aec_filter_length = 2;
      cfg->aec_nlp_level = AEC_NLP_LEVEL_NORMAL;
      cfg->se_init = true;
      cfg->ns_init = false;  // CPU; the wake word and STT cope with steady noise
      cfg->ns_model_name = nullptr;  // no model partition: WebRTC NS
      cfg->afe_ns_mode = AFE_NS_MODE_WEBRTC;
      cfg->vad_init = false;
      cfg->wakenet_init = false;
      cfg->agc_init = false;
      cfg->memory_alloc_mode = AFE_MEMORY_ALLOC_MORE_PSRAM;
      // The working 1.85C profile's layout: AFE task core 1 / priority 5, feed
      // (this component's processing task) on core 0, Wi-Fi pinned to core 1
      // by sdkconfig. At priority 18 it starved ESPHome's main loop -- the API
      // dropped mid-answer (00:48:54). The "0.8x real time" that looked like
      // the AFE was RX DMA overflow, now absorbed by the reader task's ring.
      cfg->afe_perferred_core = 1;
      cfg->afe_perferred_priority = 5;
      cfg->afe_ringbuf_size = 8;
      cfg = afe_config_check(cfg);
      this->afe_iface_ = esp_afe_handle_from_config(cfg);
      if (this->afe_iface_ != nullptr)
        this->afe_data_ = this->afe_iface_->create_from_config(cfg);
      afe_config_free(cfg);
    }
    if (this->afe_data_ == nullptr) {
      ESP_LOGE(TAG, "microphone: AFE create failed; falling back to the plain mic");
    } else {
      this->afe_feed_chunk_ = this->afe_iface_->get_feed_chunksize(this->afe_data_);
      xTaskCreatePinnedToCoreWithCaps(DuplexMicrophone::fetch_task_, "duplex_afe_out", 6144, this, 6, nullptr, 1,
                                      MALLOC_CAP_SPIRAM);
    }
  }
#endif
  // The data callbacks (wake word, voice assistant, on_data) run on this task,
  // exactly as on i2s_audio's mic_task, which gets 4096; the AEC needs more.
  {
    const int slots = this->parent_->get_slots();
    const size_t word = this->parent_->get_word_bytes();
    this->rx_ring_ = ring_buffer::RingBuffer::create((size_t) 12000 * slots * word);  // 250 ms
    if (this->rx_ring_ == nullptr ||
        xTaskCreatePinnedToCore(DuplexMicrophone::reader_task_, "duplex_rd", 3072, this, TASK_PRIORITY + 1, nullptr,
                                0) != pdPASS) {
      ESP_LOGE(TAG, "microphone: reader failed to start");
      this->mark_failed();
      return;
    }
  }
  // Internal stack, unlike the others: with the AFE, its feed() runs the echo
  // cancellers on THIS task, and on a PSRAM stack they ran at ~0.8x real time.
  if (xTaskCreatePinnedToCore(DuplexMicrophone::rx_task_, "duplex_rx", 8192, this, 10, &this->task_, 0) != pdPASS) {
    ESP_LOGE(TAG, "microphone: task failed to start");
    this->mark_failed();
  }
}

void DuplexMicrophone::dump_config() {
  ESP_LOGCONFIG(TAG, "Duplex microphone:\n  Slot: %d, 48 kHz -> 16 kHz mono", this->slot_);
#ifdef USE_DUPLEX_I2S_AFE
  if (!this->afe_format_.empty())
    ESP_LOGCONFIG(TAG, "  Front end: ESP-SR AFE (FD, low cost), format %s, %s, feed chunk %d", this->afe_format_.c_str(),
                  this->afe_data_ ? "running" : "FAILED", this->afe_feed_chunk_);
#endif
  if (this->ref_slot_ >= 0 || this->ref_from_tx_) {
#ifdef USE_DUPLEX_I2S_AEC
    ESP_LOGCONFIG(TAG, "  Echo cancellation: reference %s %d, %s mode %d, nlp %d, chunk %d",
                  this->ref_from_tx_ ? "= what the speaker sent," : "slot", this->ref_slot_,
                  this->aec_ ? "ESP-SR AEC" : "FAILED", this->aec_mode_, this->aec_nlp_, this->aec_chunk_);
#else
    ESP_LOGCONFIG(TAG, "  Reference slot %d (no AEC compiled in)", this->ref_slot_);
#endif
  }
}

void DuplexMicrophone::capture(uint32_t seconds, const std::string &host, uint16_t port) {
  if (this->capturing_.load() || this->cap_buf_ != nullptr) {
    ESP_LOGW(TAG, "capture: one is already running");
    return;
  }
  seconds = std::min<uint32_t>(std::max<uint32_t>(seconds, 1), 30);
  this->cap_len_ = seconds * 16000;
  this->cap_buf_ = (int16_t *) heap_caps_malloc(this->cap_len_ * 3 * 2, MALLOC_CAP_SPIRAM);
  if (this->cap_buf_ == nullptr) {
    ESP_LOGE(TAG, "capture: no PSRAM for %u s", (unsigned) seconds);
    return;
  }
  this->cap_host_ = host;
  this->cap_port_ = port;
  this->cap_pos_.store(0);
#ifdef USE_DUPLEX_I2S_AFE
  this->cap_pos_out_.store(0);
#endif
  this->capturing_.store(true);
  ESP_LOGI(TAG, "capture: recording %u s of mic / reference / AEC output", (unsigned) seconds);
}

#ifdef USE_DUPLEX_I2S_AFE
void DuplexMicrophone::fetch_task_(void *arg) { static_cast<DuplexMicrophone *>(arg)->run_fetch_(); }

// The AFE's cleaned output: one channel, 16 kHz. Runs the data callbacks, as
// the RX task does in the other modes.
void DuplexMicrophone::run_fetch_() {
  std::vector<uint8_t> out;
  double sq = 0;
  uint32_t n_out = 0;
  for (;;) {
    afe_fetch_result_t *r = this->afe_iface_->fetch(this->afe_data_);
    if (r == nullptr || r->ret_value != 0 || r->data == nullptr || r->data_size <= 0)
      continue;
    const int16_t *s = r->data;
    const size_t n = r->data_size / 2;
    if (this->capturing_.load()) {
      uint32_t p = this->cap_pos_out_.load();
      for (size_t k = 0; k < n && p < this->cap_len_; k++, p++)
        this->cap_buf_[3 * p + 2] = s[k];
      this->cap_pos_out_.store(p);
      if (p >= this->cap_len_ && this->cap_pos_.load() >= this->cap_len_) {
        this->capturing_.store(false);
        xTaskCreate(DuplexMicrophone::send_task_, "duplex_cap", 4096, this, 2, nullptr);
      }
    }
    if (this->level_log_) {
      for (size_t k = 0; k < n; k++)
        sq += (double) s[k] * s[k];
      n_out += n;
      if (n_out >= 16000) {
        ESP_LOGD(TAG, "AFE out: %.1f dBFS", 10 * std::log10(sq / n_out / (32768.0 * 32768.0) + 1e-12));
        sq = 0;
        n_out = 0;
      }
    }
    if (!this->running_.load() || this->data_callbacks_.size() == 0)
      continue;
    const uint8_t *b = reinterpret_cast<const uint8_t *>(s);
    out.assign(b, b + n * 2);
    this->data_callbacks_.call(out);
  }
}
#endif

void DuplexMicrophone::send_task_(void *arg) {
  auto *self = static_cast<DuplexMicrophone *>(arg);
  struct addrinfo hints = {}, *res = nullptr;
  hints.ai_family = AF_INET;
  hints.ai_socktype = SOCK_STREAM;
  char port[8];
  snprintf(port, sizeof(port), "%u", (unsigned) self->cap_port_);
  int fd = -1;
  if (getaddrinfo(self->cap_host_.c_str(), port, &hints, &res) == 0 && res != nullptr) {
    fd = socket(res->ai_family, res->ai_socktype, 0);
    if (fd >= 0 && connect(fd, res->ai_addr, res->ai_addrlen) != 0) {
      close(fd);
      fd = -1;
    }
    freeaddrinfo(res);
  }
  if (fd < 0) {
    ESP_LOGE(TAG, "capture: cannot connect to %s:%u", self->cap_host_.c_str(), (unsigned) self->cap_port_);
  } else {
    const uint8_t *b = reinterpret_cast<const uint8_t *>(self->cap_buf_);
    size_t total = self->cap_len_ * 6, sent = 0;
    while (sent < total) {
      int n = send(fd, b + sent, std::min<size_t>(4096, total - sent), 0);
      if (n <= 0)
        break;
      sent += n;
    }
    close(fd);
    ESP_LOGI(TAG, "capture: sent %u of %u bytes to %s:%u", (unsigned) sent, (unsigned) total,
             self->cap_host_.c_str(), (unsigned) self->cap_port_);
  }
  heap_caps_free(self->cap_buf_);
  self->cap_buf_ = nullptr;
  vTaskDelete(nullptr);
}

// Counted, as i2s_audio's microphone counts its listeners: the wake word and
// the voice assistant each start and stop it independently.
void DuplexMicrophone::start() {
  if (!this->is_failed())
    this->listeners_.fetch_add(1);
}

void DuplexMicrophone::stop() {
  int n = this->listeners_.load();
  while (n > 0 && !this->listeners_.compare_exchange_weak(n, n - 1)) {
  }
}

void DuplexMicrophone::loop() {
  bool want = this->listeners_.load() > 0;
  this->running_.store(want);
  this->state_ = want ? microphone::STATE_RUNNING : microphone::STATE_STOPPED;
}

void DuplexMicrophone::rx_task_(void *arg) { static_cast<DuplexMicrophone *>(arg)->run_rx_(); }

void DuplexMicrophone::reader_task_(void *arg) {
  auto *self = static_cast<DuplexMicrophone *>(arg);
  while (!self->parent_->ready())
    vTaskDelay(pdMS_TO_TICKS(50));
  const size_t bytes = 240 * self->parent_->get_slots() * self->parent_->get_word_bytes();  // one DMA buffer
  std::vector<uint8_t> buf(bytes);
  for (;;) {
    size_t got = 0;
    if (i2s_channel_read(self->parent_->rx(), buf.data(), bytes, &got, pdMS_TO_TICKS(100)) == ESP_OK && got > 0) {
      // Never blocks: if processing ever falls 250 ms behind, the newest
      // audio is what gets dropped, and the next read carries on.
      self->rx_ring_->write_without_replacement(buf.data(), got, 0);
    }
  }
}

static float db(int64_t sq, uint32_t n) { return 10 * std::log10(sq / std::max<uint32_t>(n, 1) / (32768.0 * 32768.0) + 1e-12); }

void DuplexMicrophone::run_rx_() {
  const int slots = this->parent_->get_slots();
  const int mic_slot = std::min(this->slot_, slots - 1);
  const int ref_slot = this->ref_slot_ < slots ? this->ref_slot_ : -1;
  const bool have_ref = this->ref_from_tx_ || ref_slot >= 0;

  // TDM carries 32-bit words (see DuplexI2S::setup); stereo 16-bit.
  const size_t word = this->parent_->get_word_bytes();
  std::vector<int16_t> frame(RX_FRAMES * slots), mic48(RX_FRAMES), ref48(RX_FRAMES);
  std::vector<int32_t> frame32(slots == 4 ? RX_FRAMES * 4 : 0);
  std::vector<int16_t> mic16(RX_FRAMES / 3 + 2), ref16(RX_FRAMES / 3 + 2);
  std::vector<uint8_t> out;

#ifdef USE_DUPLEX_I2S_AEC
  const int chunk = this->aec_ ? this->aec_chunk_ : 0;
  int16_t *aec_in = nullptr, *aec_ref = nullptr, *aec_out = nullptr;
  if (chunk > 0) {
    aec_in = (int16_t *) heap_caps_aligned_alloc(16, chunk * 2, MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
    aec_ref = (int16_t *) heap_caps_aligned_alloc(16, chunk * 2, MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
    aec_out = (int16_t *) heap_caps_aligned_alloc(16, chunk * 2, MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
  }
  const bool use_aec = chunk > 0 && aec_in && aec_ref && aec_out && have_ref;
  int fill = 0;
#endif

#ifdef USE_DUPLEX_I2S_AFE
  const bool afe_on = this->afe_data_ != nullptr && slots == 4;
  const int afe_ch = (int) this->afe_format_.size();  // 3 = "MRM": slot 3 is never touched
  std::vector<int16_t> afe_slot16[4];
  for (auto &v : afe_slot16)
    v.resize(RX_FRAMES / 3 + 2);
  int16_t *afe_in = afe_on ? (int16_t *) heap_caps_aligned_alloc(16, this->afe_feed_chunk_ * afe_ch * 2, MALLOC_CAP_SPIRAM)
                           : nullptr;
  int afe_fill = 0;
#endif
  // Level log: every slot raw, plus what the wake word actually gets.
  // Integer sums: doubles are software-emulated on the ESP32-S3, and this loop
  // touches every sample of every slot (it cost ~0.4 s per second of audio).
  int64_t sq_slot[4] = {0, 0, 0, 0}, sq_out = 0;
  int64_t win_start_us = esp_timer_get_time(), feed_us = 0, read_us = 0;
  uint32_t n48 = 0, n_out = 0, clipped = 0;

  auto emit = [&](const int16_t *s, size_t n) {
    if (this->level_log_) {
      for (size_t i = 0; i < n; i++)
        sq_out += (int32_t) s[i] * s[i];
      n_out += n;
    }
    if (n == 0 || !this->running_.load() || this->data_callbacks_.size() == 0)
      return;
    const uint8_t *b = reinterpret_cast<const uint8_t *>(s);
    out.assign(b, b + n * 2);
    this->data_callbacks_.call(out);
  };

  for (;;) {
    size_t got = 0;
    // Always read, listeners or not: the filters' history stays continuous,
    // the AEC stays converged, and the RX DMA never overflows.
    int64_t r0 = esp_timer_get_time();
    void *dst = word == 4 ? (void *) frame32.data() : (void *) frame.data();
    got = this->rx_ring_->read(dst, RX_FRAMES * slots * word, pdMS_TO_TICKS(100));
    if (got == 0)
      continue;
    // Never split a frame: a partial one would shift every slot by one.
    const size_t fb = slots * word;
    if (got % fb)
      got += this->rx_ring_->read(static_cast<uint8_t *>(dst) + got, fb - got % fb, portMAX_DELAY);
    read_us += esp_timer_get_time() - r0;
    const size_t frames = got / (slots * word);
    if (word == 4)
      for (size_t i = 0; i < frames * slots; i++)
        frame[i] = (int16_t) (frame32[i] >> 16);
    for (size_t i = 0; i < frames; i++)
      mic48[i] = frame[i * slots + mic_slot];
    if (this->ref_from_tx_) {
      this->parent_->ref_ring().pop(ref48.data(), frames);
    } else if (ref_slot >= 0) {
      for (size_t i = 0; i < frames; i++)
        ref48[i] = frame[i * slots + ref_slot];
    }
    if (this->level_log_) {
      for (size_t i = 0; i < frames; i++) {
        for (int s = 0; s < slots && s < 4; s++) {
          int32_t v = frame[i * slots + s];
          sq_slot[s] += v * v;
        }
        if (mic48[i] >= 32700 || mic48[i] <= -32700)
          clipped++;
      }
      n48 += frames;
    }
    const size_t n = this->decim_.process(mic48.data(), frames, mic16.data());
    if (have_ref)
      this->ref_decim_.process(ref48.data(), frames, ref16.data());

#ifdef USE_DUPLEX_I2S_AFE
    if (afe_on) {
      // The slots the format names (in order), each to 16 kHz, interleaved.
      for (int s = 0; s < afe_ch; s++) {
        for (size_t i = 0; i < frames; i++)
          mic48[i] = frame[i * slots + s];
        this->slot_decim_[s].process(mic48.data(), frames, afe_slot16[s].data());
      }
      for (size_t i = 0; i < n; i++) {
        for (int s = 0; s < afe_ch; s++)
          afe_in[afe_fill * afe_ch + s] = afe_slot16[s][i];
        if (this->capturing_.load()) {
          uint32_t p = this->cap_pos_.load();
          if (p < this->cap_len_) {
            this->cap_buf_[3 * p] = afe_slot16[mic_slot][i];
            this->cap_buf_[3 * p + 1] = afe_slot16[ref_slot >= 0 ? ref_slot : 1][i];
            this->cap_pos_.store(p + 1);
          }
        }
        if (++afe_fill == this->afe_feed_chunk_) {
          int64_t t0 = esp_timer_get_time();
          this->afe_iface_->feed(this->afe_data_, afe_in);
          feed_us += esp_timer_get_time() - t0;
          afe_fill = 0;
        }
      }
    } else
#endif
#ifdef USE_DUPLEX_I2S_AEC
    if (use_aec) {
      for (size_t i = 0; i < n; i++) {
        aec_in[fill] = mic16[i];
        aec_ref[fill] = ref16[i];
        if (++fill == chunk) {
          if (this->aec_nlp_ < 0) {
            aec_linear_process(this->aec_, aec_in, aec_ref, aec_out);
          } else {
            aec_process(this->aec_, aec_in, aec_ref, aec_out);
          }
          if (this->capturing_.load()) {
            uint32_t p = this->cap_pos_.load();
            for (int k = 0; k < chunk && p < this->cap_len_; k++, p++) {
              this->cap_buf_[3 * p] = aec_in[k];
              this->cap_buf_[3 * p + 1] = aec_ref[k];
              this->cap_buf_[3 * p + 2] = aec_out[k];
            }
            this->cap_pos_.store(p);
            if (p >= this->cap_len_) {
              this->capturing_.store(false);
              xTaskCreate(DuplexMicrophone::send_task_, "duplex_cap", 4096, this, 2, nullptr);
            }
          }
          emit(aec_out, chunk);
          fill = 0;
        }
      }
    } else
#endif
    {
      emit(mic16.data(), n);
    }

    if (this->level_log_ && n48 >= 48000) {
      if (slots == 4) {
        ESP_LOGD(TAG, "levels dBFS: s0 %.1f s1 %.1f s2 %.1f s3 %.1f | out %.1f | mic clipped %u | dma free %u, largest %u",
                 db(sq_slot[0], n48), db(sq_slot[1], n48), db(sq_slot[2], n48), db(sq_slot[3], n48),
                 db(sq_out, n_out), (unsigned) clipped,
                 (unsigned) heap_caps_get_free_size(MALLOC_CAP_DMA | MALLOC_CAP_INTERNAL),
                 (unsigned) heap_caps_get_largest_free_block(MALLOC_CAP_DMA | MALLOC_CAP_INTERNAL));
        {
          int64_t el = esp_timer_get_time() - win_start_us;
          ESP_LOGD(TAG, "pace: %u frames in %u ms (%.0f/s); in i2s_read %u ms, in AFE feed %u ms", (unsigned) n48,
                   (unsigned) (el / 1000), n48 * 1e6 / el, (unsigned) (read_us / 1000), (unsigned) (feed_us / 1000));
          win_start_us = esp_timer_get_time();
          feed_us = read_us = 0;
        }
      } else {
        ESP_LOGD(TAG, "levels dBFS: L %.1f R %.1f | out %.1f | mic clipped %u", db(sq_slot[0], n48),
                 db(sq_slot[1], n48), db(sq_out, n_out), (unsigned) clipped);
      }
      for (auto &v : sq_slot)
        v = 0;
      sq_out = 0;
      n48 = n_out = clipped = 0;
    }
  }
}

}  // namespace esphome::duplex_i2s

#endif  // USE_ESP32
