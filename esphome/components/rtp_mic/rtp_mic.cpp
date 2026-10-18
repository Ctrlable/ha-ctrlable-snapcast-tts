#include "rtp_mic.h"

#include "esphome/core/hal.h"
#include "esphome/core/helpers.h"
#include "esphome/core/log.h"

#include <cerrno>
#include <cstdio>
#include <fcntl.h>

namespace esphome::rtp_mic {

static const char *const TAG = "rtp_mic";
static const uint32_t OUT_RATE = 16000;
// One status line a minute while streaming: enough to see loss on Wi-Fi
// without drowning the log the voice pipeline is debugged from.
static const uint32_t REPORT_INTERVAL_MS = 60000;
// A request line is ~60 bytes. Anything this long is not the manager.
static const size_t MAX_LINE = 256;
// Most reference samples pushed per wake-up (30 ms): bounds the work per
// wake-up, and the catch-up after a stall.
static const size_t REF_CHUNK = 480;
// Below the mic task (23) and Wi-Fi, above the mixer (10) that drains it.
static const UBaseType_t REF_TASK_PRIORITY = 12;
static const uint32_t REF_TASK_STACK = 4096;

static void set_nonblocking(int fd) {
  const int flags = lwip_fcntl(fd, F_GETFL, 0);
  lwip_fcntl(fd, F_SETFL, flags | O_NONBLOCK);
}

void RtpMic::setup() {
  this->sock_ = lwip_socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
  if (this->sock_ < 0) {
    ESP_LOGE(TAG, "socket() failed: %d", errno);
    this->mark_failed();
    return;
  }
  // Non-blocking: the send happens on the mic task, and a stalled Wi-Fi queue
  // must cost a dropped packet, never a stalled wake word.
  set_nonblocking(this->sock_);

  // Built once, here, and only reset afterwards: the mic task may be inside
  // on_audio_() at the moment loop() stops or restarts the stream.
  const uint32_t in_rate = this->source_->get_audio_stream_info().get_sample_rate();
  if (in_rate == 0 || in_rate % OUT_RATE != 0) {
    ESP_LOGE(TAG, "mic rate %u Hz is not a multiple of 16 kHz", (unsigned) in_rate);
    this->mark_failed();
    return;
  }
  this->ssrc_ = random_uint32();
  this->decimator_ = make_unique<rtp_mic_core::Decimator>(in_rate / OUT_RATE);
  this->packetizer_ =
      make_unique<rtp_mic_core::Packetizer>(this->payload_type_, this->ssrc_, OUT_RATE * this->packet_ms_ / 1000);

  this->source_->add_data_callback([this](const std::vector<uint8_t> &data) { this->on_audio_(data); });
  this->open_control_();
#ifdef USE_RTP_MIC_REFERENCE
  // 16 KB of ring, allocated once; nothing else is held while no reference runs.
  this->ref_jb_ = make_unique<rtp_mic_core::RefJitterBuffer>();
  this->ref_pkt_.resize(1500);
  this->ref16_.resize(REF_CHUNK);
  this->ref48_.resize(3 * REF_CHUNK);
#endif
  this->apply_reference_();
}

void RtpMic::open_control_() {
  this->listen_fd_ = lwip_socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
  if (this->listen_fd_ < 0) {
    ESP_LOGE(TAG, "control socket() failed: %d", errno);
    this->mark_failed();
    return;
  }
  int one = 1;
  lwip_setsockopt(this->listen_fd_, SOL_SOCKET, SO_REUSEADDR, &one, sizeof(one));
  struct sockaddr_in addr {};
  addr.sin_family = AF_INET;
  addr.sin_addr.s_addr = htonl(INADDR_ANY);
  addr.sin_port = htons(this->control_port_);
  if (lwip_bind(this->listen_fd_, reinterpret_cast<struct sockaddr *>(&addr), sizeof(addr)) != 0 ||
      lwip_listen(this->listen_fd_, 2) != 0) {
    ESP_LOGE(TAG, "cannot listen on tcp/%u: %d", this->control_port_, errno);
    lwip_close(this->listen_fd_);
    this->listen_fd_ = -1;
    this->mark_failed();
    return;
  }
  // Polled from loop(): no task of its own, so it can never race the mic task
  // for anything but the address, which is behind addr_lock_.
  set_nonblocking(this->listen_fd_);
}

void RtpMic::set_service_enabled(bool enabled) {
  if (enabled == this->sessions_.enabled())
    return;
  if (enabled) {
    this->sessions_.set_enabled(true);
    ESP_LOGI(TAG, "service on: accepting sessions on tcp/%u", this->control_port_);
    return;
  }
  this->sessions_.disable();
  // Hang up on everyone: an off switch that left a stream running until the
  // manager noticed would not be an off switch.
  for (auto &c : this->conns_)
    this->close_(c);
  this->apply_session_();
  ESP_LOGI(TAG, "service off: no sessions");
}

std::string RtpMic::get_client() const {
  const auto &s = this->sessions_.current();
  if (!s.active)
    return "";
  return s.ip_text + ":" + std::to_string(s.port) + " (" + rtp_mic_core::beam_name(s.beam) +
         (this->ref_active_.load() ? ", echo ref)" : ")");
}

void RtpMic::set_answers_local(bool local) {
  if (local == this->answers_local_)
    return;
  this->answers_local_ = local;
  ESP_LOGI(TAG, "answers play %s: echo reference %s", local ? "on this device" : "in the room",
           local ? "not needed" : "wanted");
  this->apply_reference_();
}

// Opens or closes the reference leg to match the session and the answer mode.
// Main loop only. Without a `reference:` block this just keeps saying "off".
void RtpMic::apply_reference_() {
#ifdef USE_RTP_MIC_REFERENCE
  const bool wired = this->ref_speaker_ != nullptr && this->ref_mixer_ != nullptr && !this->is_failed();
  this->sessions_.set_ref_port(wired && !this->answers_local_ ? this->ref_port_ : 0);
  const auto &s = this->sessions_.current();
  const bool want = wired && s.active && s.ref_offered && !this->answers_local_;
  if (want) {
    if (this->ref_active_.load() && s.ip == this->ref_peer_ && s.ref_delay_ms == this->ref_delay_ms_)
      return;
    std::lock_guard<std::mutex> guard(this->ref_lock_);
    if (this->ref_sock_ < 0) {
      this->ref_sock_ = lwip_socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
      struct sockaddr_in addr {};
      addr.sin_family = AF_INET;
      addr.sin_addr.s_addr = htonl(INADDR_ANY);
      addr.sin_port = htons(this->ref_port_);
      if (this->ref_sock_ < 0 ||
          lwip_bind(this->ref_sock_, reinterpret_cast<struct sockaddr *>(&addr), sizeof(addr)) != 0) {
        ESP_LOGE(TAG, "cannot take the echo reference on udp/%u: %d", this->ref_port_, errno);
        if (this->ref_sock_ >= 0)
          lwip_close(this->ref_sock_);
        this->ref_sock_ = -1;
        return;
      }
      set_nonblocking(this->ref_sock_);
    }
    this->ref_peer_ = s.ip;
    this->ref_delay_ms_ = s.ref_delay_ms;
    this->ref_jb_->set_delay(16u * s.ref_delay_ms);
    this->ref_up_.reset();
    this->ref_foreign_ = 0;
    if (this->ref_task_handle_ == nullptr &&
        xTaskCreate(RtpMic::ref_task_, "rtp_ref", REF_TASK_STACK, this, REF_TASK_PRIORITY,
                    &this->ref_task_handle_) != pdPASS) {
      this->ref_task_handle_ = nullptr;
      ESP_LOGE(TAG, "cannot start the echo reference task");
      return;
    }
    if (!this->ref_active_.load()) {
      // Mono 16-bit at the mixer's rate; the mixer widens it to the I2S format.
      // Its volume is the shared output's, i.e. the DAC: muted at 0, while the
      // XVF still receives the reference at full scale on I2S.
      audio::AudioStreamInfo info(16, 1, 48000);
      this->ref_speaker_->set_audio_stream_info(info);
      this->ref_speaker_->start();
      this->ref_active_.store(true);
    }
    ESP_LOGI(TAG, "echo reference from %s on udp/%u, %u ms behind arrival", s.ip_text.c_str(), this->ref_port_,
             (unsigned) s.ref_delay_ms);
    return;
  }
  if (!this->ref_active_.load() && this->ref_sock_ < 0)
    return;
  {
    std::lock_guard<std::mutex> guard(this->ref_lock_);
    this->ref_active_.store(false);
    if (this->ref_sock_ >= 0)
      lwip_close(this->ref_sock_);
    this->ref_sock_ = -1;
  }
  this->ref_speaker_->stop();
  ESP_LOGI(TAG, "echo reference stopped (%u packets, %u late samples, %u re-anchors, %u trims)",
           (unsigned) this->ref_jb_->packets(), (unsigned) this->ref_jb_->late_samples(),
           (unsigned) this->ref_jb_->resyncs(), (unsigned) this->ref_jb_->trims());
#endif
}

#ifdef USE_RTP_MIC_REFERENCE
// Woken by the mic task once per mic read (~5 ms). The mic and the speaker
// share the XVF3800's I2S clock, so that wake-up paces the reference to what
// the XVF actually consumes -- no timer of its own. Blocks forever while no
// reference runs.
void RtpMic::ref_task_(void *arg) {
  auto *self = static_cast<RtpMic *>(arg);
  for (;;) {
    ulTaskNotifyTake(pdTRUE, portMAX_DELAY);
    self->feed_reference_();
  }
}
#endif

// Reference task only.
void RtpMic::feed_reference_() {
#ifdef USE_RTP_MIC_REFERENCE
  if (!this->ref_active_.load())
    return;
  std::lock_guard<std::mutex> guard(this->ref_lock_);
  if (this->ref_sock_ < 0)
    return;
  for (;;) {
    struct sockaddr_in from {};
    socklen_t from_len = sizeof(from);
    const int n = lwip_recvfrom(this->ref_sock_, this->ref_pkt_.data(), this->ref_pkt_.size(), 0,
                                reinterpret_cast<struct sockaddr *>(&from), &from_len);
    if (n <= 0)
      break;
    if (from.sin_addr.s_addr != this->ref_peer_) {
      ++this->ref_foreign_;  // only the session's own server may drive the canceller
      continue;
    }
    rtp_mic_core::RtpView v;
    if (rtp_mic_core::parse_rtp(this->ref_pkt_.data(), n, v))
      this->ref_jb_->put(v.ts, v.marker, v.payload, v.payload_len / 2);
  }
  // Top the output pipeline up to the queue target: what the I2S consumed
  // since last time, so the reference runs at exactly the XVF's clock.
  const uint32_t queued = this->ref_mixer_->get_frames_in_pipeline();
  const uint32_t target = 48u * this->ref_queue_ms_;
  if (queued >= target)
    return;
  size_t n16 = (target - queued + 2) / 3;
  if (n16 > REF_CHUNK)
    n16 = REF_CHUNK;
  this->ref_jb_->take(this->ref16_.data(), n16);
  this->ref_up_.process(this->ref16_.data(), n16, this->ref48_.data());
  this->ref_speaker_->play(reinterpret_cast<const uint8_t *>(this->ref48_.data()), 6 * n16, 0);
#endif
}

void RtpMic::accept_() {
  struct sockaddr_in peer {};
  socklen_t len = sizeof(peer);
  const int fd = lwip_accept(this->listen_fd_, reinterpret_cast<struct sockaddr *>(&peer), &len);
  if (fd < 0)
    return;
  Conn *slot = nullptr;
  for (auto &c : this->conns_) {
    if (c.fd < 0) {
      slot = &c;
      break;
    }
  }
  if (slot == nullptr) {
    lwip_close(fd);
    return;
  }
  set_nonblocking(fd);
  slot->fd = fd;
  slot->ip = peer.sin_addr.s_addr;
  char text[16];
  const uint32_t h = ntohl(peer.sin_addr.s_addr);
  snprintf(text, sizeof(text), "%u.%u.%u.%u", (unsigned) (h >> 24), (unsigned) ((h >> 16) & 0xff),
           (unsigned) ((h >> 8) & 0xff), (unsigned) (h & 0xff));
  slot->ip_text = text;
  slot->buf.clear();
  ESP_LOGD(TAG, "control connection from %s", text);
}

// True when this connection owned the session, i.e. the stream must stop.
bool RtpMic::close_(Conn &c) {
  if (c.fd < 0)
    return false;
  lwip_close(c.fd);
  const bool dropped = this->sessions_.on_disconnect(c.fd);
  if (dropped)
    ESP_LOGI(TAG, "%s hung up; stream stopped", c.ip_text.c_str());
  c.fd = -1;
  c.buf.clear();
  return dropped;
}

void RtpMic::serve_(Conn &c) {
  char chunk[64];
  bool changed = false;
  for (;;) {
    const int n = lwip_recv(c.fd, chunk, sizeof(chunk), 0);
    if (n == 0 || (n < 0 && errno != EAGAIN && errno != EWOULDBLOCK)) {
      if (this->close_(c) || changed)
        this->apply_session_();
      return;
    }
    if (n < 0)
      break;
    c.buf.append(chunk, n);
    if (c.buf.size() > MAX_LINE && c.buf.find('\n') == std::string::npos) {
      if (this->close_(c) || changed)
        this->apply_session_();
      return;
    }
    size_t nl;
    while ((nl = c.buf.find('\n')) != std::string::npos) {
      const std::string line = c.buf.substr(0, nl);
      c.buf.erase(0, nl + 1);
      const auto reply =
          this->sessions_.handle_line(c.fd, c.ip, c.ip_text, line, millis(), this->ssrc_, this->payload_type_);
      if (!reply.line.empty()) {
        const std::string out = reply.line + "\n";
        lwip_send(c.fd, out.data(), out.size(), 0);
      }
      if (!reply.led.empty())
        this->led_trigger_.trigger(reply.led);
      if (reply.line.rfind("ERR", 0) == 0)
        ESP_LOGW(TAG, "%s: '%s' -> %s", c.ip_text.c_str(), line.c_str(), reply.line.c_str());
      changed |= reply.changed;
      if (reply.close) {
        if (this->close_(c) || changed)
          this->apply_session_();
        return;
      }
    }
  }
  if (changed)
    this->apply_session_();
}

// Points the UDP stream at whatever the session table now says.
void RtpMic::apply_session_() {
  const auto &s = this->sessions_.current();
  const bool want = s.active && !this->is_failed();
  if (want) {
    {
      std::lock_guard<std::mutex> guard(this->addr_lock_);
      this->addr_ = {};
      this->addr_.sin_family = AF_INET;
      this->addr_.sin_addr.s_addr = s.ip;
      this->addr_.sin_port = htons(s.port);
      this->addr_valid_ = true;
    }
    this->channel_.store(static_cast<uint8_t>(s.beam));
    // A new target restarts the stream so the receiver sees the marker.
    this->restart_packetizer_.store(true);
    if (!this->streaming_.load()) {
      this->stack_reported_ = false;
      this->streaming_.store(true);
      this->source_->start();
    }
    ESP_LOGI(TAG, "streaming %s beam to %s:%u", rtp_mic_core::beam_name(s.beam), s.ip_text.c_str(), s.port);
    this->apply_reference_();
    return;
  }
  {
    std::lock_guard<std::mutex> guard(this->addr_lock_);
    this->addr_valid_ = false;
  }
  this->apply_reference_();
  if (this->streaming_.load()) {
    this->streaming_.store(false);
    this->source_->stop();
    ESP_LOGI(TAG, "stopped (sent %u, dropped %u)", (unsigned) this->packets_sent_.load(),
             (unsigned) this->packets_dropped_.load());
  }
}

void RtpMic::loop() {
  if (this->listen_fd_ < 0)
    return;
  this->accept_();
  for (auto &c : this->conns_) {
    if (c.fd >= 0)
      this->serve_(c);
  }
  if (this->sessions_.tick(millis())) {
    ESP_LOGW(TAG, "no keepalive for %u s; stream stopped", (unsigned) (rtp_mic_core::SessionTable::IDLE_TIMEOUT_MS / 1000));
    this->apply_session_();
  }
  if (this->streaming_.load()) {
    const uint32_t now = millis();
    if (now - this->last_report_ms_ > REPORT_INTERVAL_MS) {
      this->last_report_ms_ = now;
      ESP_LOGD(TAG, "to %s: sent %u, dropped %u", this->get_client().c_str(), (unsigned) this->packets_sent_.load(),
               (unsigned) this->packets_dropped_.load());
      // Stack headroom once per stream at INFO (the 2026-10-10 overflow was the
      // mic task's), and a warning whenever it runs low.
      const uint32_t mic_free = this->mic_stack_free_.load();
      uint32_t ref_free = 0;
#ifdef USE_RTP_MIC_REFERENCE
      if (this->ref_task_handle_ != nullptr)
        ref_free = uxTaskGetStackHighWaterMark(this->ref_task_handle_);
#endif
      if (!this->stack_reported_ || (mic_free > 0 && mic_free < 512) || (ref_free > 0 && ref_free < 512)) {
        this->stack_reported_ = true;
        ESP_LOGI(TAG, "stack free: mic task %u, reference task %u bytes", (unsigned) mic_free, (unsigned) ref_free);
      }
#ifdef USE_RTP_MIC_REFERENCE
      // Read without the lock: counters for a log line, a torn read is harmless.
      if (this->ref_active_.load()) {
        ESP_LOGD(TAG, "echo ref: %u packets, %u late samples, %u re-anchors, %u trims, drift %d samples, %u foreign",
                 (unsigned) this->ref_jb_->packets(), (unsigned) this->ref_jb_->late_samples(),
                 (unsigned) this->ref_jb_->resyncs(), (unsigned) this->ref_jb_->trims(),
                 (int) this->ref_jb_->drift_samples(), (unsigned) this->ref_foreign_);
      }
#endif
    }
  }
}

// Runs on the I2S mic task.
void RtpMic::on_audio_(const std::vector<uint8_t> &data) {
  if (!this->streaming_.load() || this->packetizer_ == nullptr)
    return;
#ifdef USE_RTP_MIC_REFERENCE
  if (this->ref_active_.load() && this->ref_task_handle_ != nullptr)
    xTaskNotifyGive(this->ref_task_handle_);
#endif
  // Sampled, not scanned every read: the crash this watches for is slow to come.
  static uint32_t reads = 0;
  if (++reads % 1000 == 1)
    this->mic_stack_free_.store(uxTaskGetStackHighWaterMark(nullptr));
  const auto info = this->source_->get_audio_stream_info();
  const uint8_t channels = info.get_channels();
  uint8_t channel = this->channel_.load();
  if (channel >= channels)
    channel = 0;

  const size_t frames = data.size() / (4u * channels);
  if (this->picked_.size() < frames) {
    this->picked_.resize(frames);
    this->decimated_.resize(frames);
  }
  rtp_mic_core::pick_channel_s32_to_s16(data.data(), data.size(), channels, channel, this->picked_.data());
  const size_t n = this->decimator_->process(this->picked_.data(), frames, this->decimated_.data());

  if (this->restart_packetizer_.exchange(false))
    this->packetizer_->restart();

  struct sockaddr_in to;
  {
    std::lock_guard<std::mutex> guard(this->addr_lock_);
    if (!this->addr_valid_)
      return;
    to = this->addr_;
  }
  this->packetizer_->push(this->decimated_.data(), n, [this, &to](const uint8_t *buf, size_t len) {
    if (lwip_sendto(this->sock_, buf, len, 0, reinterpret_cast<struct sockaddr *>(&to), sizeof(to)) < 0) {
      this->packets_dropped_.fetch_add(1);
    } else {
      this->packets_sent_.fetch_add(1);
    }
  });
}

void RtpMic::dump_config() {
  ESP_LOGCONFIG(TAG,
                "RTP mic:\n"
                "  Control: tcp/%u (the manager connects here; the stream goes back to it)\n"
                "  Payload type: %u (L16/16000/1)\n"
                "  Packet: %u ms\n"
                "  Idle timeout: %u s",
                this->control_port_, this->payload_type_, this->packet_ms_,
                (unsigned) (rtp_mic_core::SessionTable::IDLE_TIMEOUT_MS / 1000));
#ifdef USE_RTP_MIC_REFERENCE
  ESP_LOGCONFIG(TAG,
                "  Echo reference: udp/%u when offered and answers play in the room\n"
                "  Reference queue: %u ms",
                this->ref_port_, (unsigned) this->ref_queue_ms_);
#endif
}

}  // namespace esphome::rtp_mic
