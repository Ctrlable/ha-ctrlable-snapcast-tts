#include "rtp_mic.h"

#include "esphome/core/helpers.h"
#include "esphome/core/log.h"

#include <cerrno>
#include <fcntl.h>
#include <lwip/netdb.h>

namespace esphome::rtp_mic {

static const char *const TAG = "rtp_mic";
static const uint32_t OUT_RATE = 16000;
// One status line a minute while streaming: enough to see loss on Wi-Fi
// without drowning the log the voice pipeline is debugged from.
static const uint32_t REPORT_INTERVAL_MS = 60000;

void RtpMic::setup() {
  this->sock_ = lwip_socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
  if (this->sock_ < 0) {
    ESP_LOGE(TAG, "socket() failed: %d", errno);
    this->mark_failed();
    return;
  }
  // Non-blocking: the send happens on the mic task, and a stalled Wi-Fi queue
  // must cost a dropped packet, never a stalled wake word.
  const int flags = lwip_fcntl(this->sock_, F_GETFL, 0);
  lwip_fcntl(this->sock_, F_SETFL, flags | O_NONBLOCK);

  // Built once, here, and only reset afterwards: the mic task may be inside
  // on_audio_() at the moment loop() stops or restarts the stream.
  const uint32_t in_rate = this->source_->get_audio_stream_info().get_sample_rate();
  if (in_rate == 0 || in_rate % OUT_RATE != 0) {
    ESP_LOGE(TAG, "mic rate %u Hz is not a multiple of 16 kHz", (unsigned) in_rate);
    this->mark_failed();
    return;
  }
  this->decimator_ = make_unique<rtp_mic_core::Decimator>(in_rate / OUT_RATE);
  this->packetizer_ =
      make_unique<rtp_mic_core::Packetizer>(this->payload_type_, random_uint32(), OUT_RATE * this->packet_ms_ / 1000);

  this->source_->add_data_callback([this](const std::vector<uint8_t> &data) { this->on_audio_(data); });
  this->destination_dirty_ = !this->destination_.empty();
}

void RtpMic::set_destination(const std::string &host_port) {
  if (host_port == this->destination_)
    return;
  this->destination_ = host_port;
  this->destination_dirty_ = true;
}

void RtpMic::set_enabled(bool enabled) {
  if (enabled == this->enabled_)
    return;
  this->enabled_ = enabled;
  this->update_state_();
}

bool RtpMic::resolve_() {
  const auto colon = this->destination_.rfind(':');
  if (this->destination_.empty() || colon == std::string::npos) {
    std::lock_guard<std::mutex> guard(this->addr_lock_);
    this->addr_valid_ = false;
    return false;
  }
  const std::string host = this->destination_.substr(0, colon);
  const int port = atoi(this->destination_.c_str() + colon + 1);

  struct addrinfo hints {};
  hints.ai_family = AF_INET;
  hints.ai_socktype = SOCK_DGRAM;
  struct addrinfo *res = nullptr;
  // An IP literal resolves without a network round trip; a name blocks loop()
  // for one DNS lookup, once per change -- the manager sends IPs.
  if (lwip_getaddrinfo(host.c_str(), nullptr, &hints, &res) != 0 || res == nullptr) {
    ESP_LOGW(TAG, "cannot resolve %s", host.c_str());
    std::lock_guard<std::mutex> guard(this->addr_lock_);
    this->addr_valid_ = false;
    return false;
  }
  std::lock_guard<std::mutex> guard(this->addr_lock_);
  memcpy(&this->addr_, res->ai_addr, sizeof(this->addr_));
  this->addr_.sin_port = htons(port);
  this->addr_valid_ = true;
  lwip_freeaddrinfo(res);
  return true;
}

void RtpMic::update_state_() {
  bool have_addr;
  {
    std::lock_guard<std::mutex> guard(this->addr_lock_);
    have_addr = this->addr_valid_;
  }
  const bool want = this->enabled_ && have_addr && !this->is_failed();
  if (want == this->streaming_.load())
    return;

  if (want) {
    this->restart_packetizer_.store(true);
    this->streaming_.store(true);
    this->source_->start();
    ESP_LOGI(TAG, "streaming channel %u to %s", this->channel_.load(), this->destination_.c_str());
  } else {
    this->streaming_.store(false);
    this->source_->stop();
    ESP_LOGI(TAG, "stopped (sent %u, dropped %u)", (unsigned) this->packets_sent_.load(),
             (unsigned) this->packets_dropped_.load());
  }
}

void RtpMic::loop() {
  if (this->destination_dirty_) {
    this->destination_dirty_ = false;
    this->resolve_();
    // A new destination restarts the stream so the receiver sees the marker.
    this->restart_packetizer_.store(true);
    this->update_state_();
  }
  if (this->streaming_.load()) {
    const uint32_t now = millis();
    if (now - this->last_report_ms_ > REPORT_INTERVAL_MS) {
      this->last_report_ms_ = now;
      ESP_LOGD(TAG, "to %s: sent %u, dropped %u", this->destination_.c_str(), (unsigned) this->packets_sent_.load(),
               (unsigned) this->packets_dropped_.load());
    }
  }
}

// Runs on the I2S mic task.
void RtpMic::on_audio_(const std::vector<uint8_t> &data) {
  if (!this->streaming_.load() || this->packetizer_ == nullptr)
    return;
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
                "  Channel: %u\n"
                "  Payload type: %u (L16/16000/1)\n"
                "  Packet: %u ms\n"
                "  Destination: %s",
                this->channel_.load(), this->payload_type_, this->packet_ms_,
                this->destination_.empty() ? "(none; set at runtime)" : this->destination_.c_str());
}

}  // namespace esphome::rtp_mic
