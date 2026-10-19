#include "aec_mic.h"

#ifdef USE_ESP32

#include <algorithm>
#include <cstring>
#include <vector>

#include <esp_heap_caps.h>
#include <esp_timer.h>
#include <lwip/netdb.h>
#include <lwip/sockets.h>

#include "esphome/core/hal.h"
#include "esphome/core/log.h"

namespace esphome::aec_mic {

static const char *const TAG = "aec_mic";

void AecMic::setup() {
  if (this->source_ == nullptr) {
    this->mark_failed();
    return;
  }
  this->audio_stream_info_ = audio::AudioStreamInfo(16, 1, 16000);
  if (!this->ref_from_text_.empty())
    this->ref_from_ = inet_addr(this->ref_from_text_.c_str());

  this->aec_ = aec_create(16000, this->filter_length_, 1, (aec_mode_t) this->aec_mode_);
  if (this->aec_ == nullptr) {
    ESP_LOGE(TAG, "aec_create failed");
    this->mark_failed();
    return;
  }
  // Talking over the room (a wake word over music) must not be taken for
  // echo; the aggressive default suppressed spoken words on the 1.85C.
  aec_set_nlp_level(this->aec_, AEC_NLP_LEVEL_NORMAL);
  this->chunk_ = aec_get_chunksize(this->aec_);
  this->jb_.set_recenter(true);  // the delay counts from the mean arrival (rtp_core.h)

  this->mic_ring_ = ring_buffer::RingBuffer::create(16000 * 2 / 2);  // 500 ms of 16 kHz mono
  if (this->mic_ring_ == nullptr) {
    this->mark_failed();
    return;
  }
  // The source mic's task only copies; everything else happens on ours.
  this->source_->add_data_callback([this](const std::vector<uint8_t> &data) {
    if (this->mic_ring_->write_without_replacement(data.data(), data.size(), 0) < data.size())
      this->realign_.store(true);  // overflow: mic samples lost
    this->watch_gaps_(data.size() / 2);
  });
  if (xTaskCreate(AecMic::task_, "aec_mic", 8192, this, 18, &this->task_handle_) != pdPASS) {
    ESP_LOGE(TAG, "task failed to start");
    this->mark_failed();
  }
}

void AecMic::dump_config() {
  ESP_LOGCONFIG(TAG,
                "AEC microphone (room reference):\n"
                "  Reference: RTP L16/16000/1 on udp/%u from %s\n"
                "  Delay: %u ms, filter length %d, mode %d, chunk %d",
                this->ref_port_, this->ref_from_text_.empty() ? "anyone" : this->ref_from_text_.c_str(),
                (unsigned) this->delay_ms_.load(), this->filter_length_, this->aec_mode_, this->chunk_);
}

// Counted, and passed on 1:1: the source mic counts its own listeners.
void AecMic::start() {
  if (this->is_failed())
    return;
  this->listeners_.fetch_add(1);
  this->source_->start();
}

// The source is shared (micro_wake_word AND voice_assistant listen through
// here): stop it only when the LAST listener leaves. Stopping on every stop()
// left the wake word deaf after the first answer (2026-10-18).
void AecMic::stop() {
  int n = this->listeners_.load();
  while (n > 0 && !this->listeners_.compare_exchange_weak(n, n - 1)) {
  }
  if (n == 1)
    this->source_->stop();
}

void AecMic::loop() {
  // Someone else sharing the source (rtp_mic, a yaml action) may stop it under
  // our listeners: start it again rather than go deaf.
  if (this->listeners_.load() > 0 && this->source_->is_stopped())
    this->source_->start();
  bool want = this->listeners_.load() > 0 && this->source_->is_running();
  this->running_.store(want);
  this->state_ = want ? microphone::STATE_RUNNING
                      : (this->listeners_.load() > 0 ? microphone::STATE_STARTING : microphone::STATE_STOPPED);
}

void AecMic::open_socket_() {
  this->sock_ = lwip_socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
  if (this->sock_ < 0)
    return;
  struct sockaddr_in addr {};
  addr.sin_family = AF_INET;
  addr.sin_port = htons(this->ref_port_);
  addr.sin_addr.s_addr = htonl(INADDR_ANY);
  if (lwip_bind(this->sock_, reinterpret_cast<struct sockaddr *>(&addr), sizeof(addr)) != 0) {
    lwip_close(this->sock_);
    this->sock_ = -1;
    return;
  }
  int flags = lwip_fcntl(this->sock_, F_GETFL, 0);
  lwip_fcntl(this->sock_, F_SETFL, flags | O_NONBLOCK);
  ESP_LOGI(TAG, "listening for the room reference on udp/%u", this->ref_port_);
}

void AecMic::read_reference_() {
  if (this->sock_ < 0)
    return;
  for (;;) {
    struct sockaddr_in from {};
    socklen_t from_len = sizeof(from);
    const int n = lwip_recvfrom(this->sock_, this->pkt_, sizeof(this->pkt_), 0,
                                reinterpret_cast<struct sockaddr *>(&from), &from_len);
    if (n <= 0)
      break;
    if (this->ref_from_ != 0 && from.sin_addr.s_addr != this->ref_from_) {
      this->ref_foreign_.fetch_add(1);
      continue;
    }
    rtp_mic_core::RtpView v;
    if (rtp_mic_core::parse_rtp(this->pkt_, n, v)) {
      this->jb_.put(v.ts, v.marker, v.payload, v.payload_len / 2);
      this->ref_packets_.fetch_add(1);
    }
  }
}

// The reference buffer only advances as mic audio is processed, so mic audio
// that never arrives (the source paused, ~300 ms at once in the 2026-10-18
// capture) leaves the reference that far behind the echo for good: re-anchor
// it on the newest packet, as at start-up. Cheap: runs on the source's task.
void AecMic::watch_gaps_(size_t samples) {
  const int64_t lost = this->gaps_.feed(esp_timer_get_time() * 16 / 1000, samples);
  if (lost > 0) {
    this->gap_ms_.fetch_add((uint32_t) (lost / 16));
    this->realign_.store(true);
  }
}

void AecMic::task_(void *arg) { static_cast<AecMic *>(arg)->run_(); }

void AecMic::run_() {
  while (!network::is_connected())
    vTaskDelay(pdMS_TO_TICKS(200));
  this->open_socket_();

  const int chunk = this->chunk_;
  auto *in = (int16_t *) heap_caps_aligned_alloc(16, chunk * 2, MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
  auto *ref = (int16_t *) heap_caps_aligned_alloc(16, chunk * 2, MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
  auto *out = (int16_t *) heap_caps_aligned_alloc(16, chunk * 2, MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
  std::vector<uint8_t> emit;
  uint32_t last_report = millis(), last_packets = 0;
  int64_t aec_us = 0;
  uint32_t aec_chunks = 0;

  for (;;) {
    // Re-anchor the reference whenever the delay setting changes.
    const uint32_t d = this->delay_ms_.load();
    if (d != this->applied_delay_ms_) {
      this->applied_delay_ms_ = d;
      this->jb_.set_delay(d * 16);
      ESP_LOGI(TAG, "reference delay %u ms", (unsigned) d);
    }

    // Process the NEWEST mic audio: a backlog here delays the mic against the
    // reference, and an echo older than the reference cannot be cancelled
    // (2026-10-18 capture: echo 413 ms ahead of a reference set to 100 ms).
    if (this->realign_.exchange(false)) {
      this->jb_.reset();  // the next packet anchors it again, `delay` ahead
      this->realigns_++;
    }
    const size_t avail = this->mic_ring_->available();
    if (avail > (size_t) chunk * 2 * 4) {  // > 128 ms behind
      this->realign_.store(true);  // the mic jumps ahead: so must the reference
      size_t drop = avail - (size_t) chunk * 2;
      drop -= drop % 2;
      this->dropped_samples_ += drop / 2;
      while (drop > 0) {
        size_t n = std::min<size_t>(drop, (size_t) chunk * 2);
        size_t r = this->mic_ring_->read(in, n, 0);
        if (r == 0)
          break;
        drop -= r;
      }
    }

    // One chunk of mic (blocks until the source has produced it).
    size_t got = 0;
    while (got < (size_t) chunk * 2) {
      this->read_reference_();
      size_t r = this->mic_ring_->read(reinterpret_cast<uint8_t *>(in) + got, chunk * 2 - got, pdMS_TO_TICKS(20));
      got += r;
      if (r == 0 && !this->running_.load() && got == 0)
        break;  // idle: nothing to process, keep the socket drained
    }
    this->read_reference_();
    if (got < (size_t) chunk * 2) {
      // Not running: keep the reference clock moving so it stays aligned.
      if (got == 0) {
        int16_t sink[320];
        this->jb_.take(sink, 320);
      }
      continue;
    }

    this->jb_.take(ref, chunk);  // silence where no reference arrived in time
    // The bus copy sits ~24 dB under the echo the mic hears, and the
    // canceller treats a quiet far end as silence: bring it up to level.
    const int32_t g = this->ref_gain_q8_.load();
    if (g != 256) {
      for (int k = 0; k < chunk; k++) {
        int32_t v = ((int32_t) ref[k] * g) >> 8;
        ref[k] = (int16_t) (v > 32767 ? 32767 : (v < -32768 ? -32768 : v));
      }
    }
    const int64_t t_aec = esp_timer_get_time();
    aec_process(this->aec_, in, ref, out);
    aec_us += esp_timer_get_time() - t_aec;
    aec_chunks++;

    if (this->capturing_.load()) {
      uint32_t p = this->cap_pos_.load();
      for (int k = 0; k < chunk && p < this->cap_len_; k++, p++) {
        this->cap_buf_[3 * p] = in[k];
        this->cap_buf_[3 * p + 1] = ref[k];
        this->cap_buf_[3 * p + 2] = out[k];
      }
      this->cap_pos_.store(p);
      if (p >= this->cap_len_) {
        this->capturing_.store(false);
        xTaskCreate(AecMic::send_task_, "aec_cap", 4096, this, 2, nullptr);
      }
    }

    if (this->running_.load() && this->data_callbacks_.size() > 0) {
      const uint8_t *b = reinterpret_cast<const uint8_t *>(out);
      emit.assign(b, b + chunk * 2);
      this->data_callbacks_.call(emit);
    }

    if (millis() - last_report > 60000) {
      const uint32_t p = this->ref_packets_.load();
      ESP_LOGD(TAG, "reference: %u packets in the last minute%s; mic backlog dropped %u ms, mic gaps %u ms, realigned %u x, recentered %u x, late %u ms",
               (unsigned) (p - last_packets), this->ref_foreign_.load() ? " (some from foreign hosts, dropped)" : "",
               (unsigned) (this->dropped_samples_ / 16), (unsigned) this->gap_ms_.exchange(0), (unsigned) this->realigns_,
               (unsigned) this->jb_.recenters(), (unsigned) (this->jb_.late_samples() / 16));
      if (aec_chunks > 0)
        ESP_LOGD(TAG, "canceller: %u us per %d-sample chunk = %.0f%% of one core", (unsigned) (aec_us / aec_chunks),
                 chunk, 100.0 * (double) aec_us / aec_chunks / (chunk * 1e6 / 16000));
      aec_us = 0;
      aec_chunks = 0;
      this->dropped_samples_ = 0;
      this->realigns_ = 0;
      last_packets = p;
      last_report = millis();
    }
  }
}

void AecMic::capture(uint32_t seconds, const std::string &host, uint16_t port) {
  if (this->capturing_.load() || this->cap_buf_ != nullptr) {
    ESP_LOGW(TAG, "capture: one is already running");
    return;
  }
  seconds = std::min<uint32_t>(std::max<uint32_t>(seconds, 1), 30);
  this->cap_len_ = seconds * 16000;
  this->cap_buf_ = (int16_t *) heap_caps_malloc(this->cap_len_ * 6, MALLOC_CAP_SPIRAM);
  if (this->cap_buf_ == nullptr) {
    ESP_LOGE(TAG, "capture: no PSRAM for %u s", (unsigned) seconds);
    return;
  }
  this->cap_host_ = host;
  this->cap_port_ = port;
  this->cap_pos_.store(0);
  this->capturing_.store(true);
  ESP_LOGI(TAG, "capture: recording %u s of mic / reference / AEC output", (unsigned) seconds);
}

void AecMic::send_task_(void *arg) {
  auto *self = static_cast<AecMic *>(arg);
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
    ESP_LOGI(TAG, "capture: sent %u of %u bytes", (unsigned) sent, (unsigned) total);
  }
  heap_caps_free(self->cap_buf_);
  self->cap_buf_ = nullptr;
  vTaskDelete(nullptr);
}

}  // namespace esphome::aec_mic

#endif  // USE_ESP32
