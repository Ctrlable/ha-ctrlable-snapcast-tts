#pragma once
// ESPHome wrapper around faces_render.h: keeps the step and style, follows the
// live mic level, and redraws the display only as often as the face moves.
#include <cmath>
#include <functional>
#include <vector>

#include "esphome/components/display/display.h"
#include "esphome/core/component.h"
#include "esphome/core/hal.h"
#include "esphome/core/helpers.h"
#include "esphome/core/log.h"
#include "faces_render.h"

namespace esphome {
namespace ctrlable_faces {

using ::cfaces::RGB;
using ::cfaces::Step;
using ::cfaces::Style;

class CtrlableFaces : public Component {
 public:
  void set_display(display::Display *d) { this->display_ = d; }
  void set_round(bool r) { this->round_ = r; }
  void set_logo_left(bool l) { this->logo_left_ = l; }
  void set_font(display::BaseFont *f) { this->font_ = f; }

  float get_setup_priority() const override { return setup_priority::PROCESSOR; }

  // 0 starting, 1 idle, 2 awake, 3 listening, 4 thinking, 5 replying,
  // 6 muted, 7 error, 8 no Wi-Fi, 9 no server.
  void set_step(int s) {
    if (s < 0 || s > 9) s = 1;
    if (s == (int) this->step_) return;
    static const char *const NAMES[] = {"starting", "idle", "awake", "listening", "thinking",
                                        "replying", "muted", "error", "no wifi", "no server"};
    ESP_LOGD("ctrlable_faces", "face: %s", NAMES[s]);
    this->step_ = (Step) s;
    this->step_start_ = millis();
    this->dirty_ = true;
  }
  int get_step() const { return (int) this->step_; }

  // 0 Orb, 1 Halo, 2 Dot & Bar.
  void set_style(int s) {
    if (s < 0 || s > 2) s = 0;
    this->style_ = (Style) s;
    this->dirty_ = true;
  }
  // From the style select's option text.
  void set_style_name(const std::string &n) { this->set_style(n == "Halo" ? 1 : (n == "Dot & Bar" ? 2 : 0)); }

  // False while the backlight is off: nothing on screen is worth the bus time.
  void set_active(bool a) {
    if (a && !this->active_) this->dirty_ = true;
    this->active_ = a;
  }

  // Microphone on_data: 16-bit little-endian samples. Fast up, slow down, so
  // the listening face follows speech without flicker.
  void feed_audio(const std::vector<uint8_t> &data) {
    size_t n = data.size() / 2;
    if (n == 0) return;
    double sum = 0;
    const int16_t *s = reinterpret_cast<const int16_t *>(data.data());
    for (size_t i = 0; i < n; i++) sum += (double) s[i] * s[i];
    float rms = std::sqrt(sum / n) / 32768.0f;
    float db = rms > 1e-6f ? 20 * std::log10(rms) : -120;
    float lvl = (db + this->floor_db_) / 40.0f;  // floor_db_ dB -> 0, 40 dB louder -> 1
    lvl = lvl < 0 ? 0 : (lvl > 1 ? 1 : lvl);
    this->level_ = lvl > this->level_ ? lvl : this->level_ * 0.85f + lvl * 0.15f;
    this->level_ms_ = millis();
  }
  void set_floor_db(float f) { this->floor_db_ = -f; }  // e.g. -58 for a quiet room

  void loop() override {
    if (this->display_ == nullptr || !this->active_) return;
    uint32_t now = millis();
    float t = (now - this->step_start_) / 1000.0f;
    uint32_t iv = ::cfaces::frame_interval_ms(this->step_, t);
    if (this->dirty_ || (iv != 0 && now - this->last_frame_ >= iv)) {
      this->dirty_ = false;
      this->last_frame_ = now;
      this->display_->update();
    }
  }

  // Call from the display lambda: `id(faces).draw(it);`, or with an overlay
  // for anything else that belongs on screen (the CoreS3's mute icon):
  //   id(faces).draw(it, [&](display::Display &d) { d.print(...); });
  //
  // Everything -- the face, its label, the overlay -- is composed in our own
  // RGB565 frame in PSRAM and sent to the panel in ONE transfer (see
  // Frame565). Drawing the label and icon through the panel's buffer after the
  // frame was tried first and flashed: two transfers per frame, and the panel
  // sends the bounding box of everything drawn into its buffer, uncleared
  // memory between label and icon included (2026-10-17, CoreS3 "debris").
  // Without PSRAM for the frame it falls back to drawing through the display.
  void draw(display::Display &it) { this->draw(it, nullptr); }
  void draw(display::Display &it, const std::function<void(display::Display &)> &overlay) {
    float t = (millis() - this->step_start_) / 1000.0f;
    bool live = millis() - this->level_ms_ < 400;
    float lvl = live ? this->level_ : -1.0f;
    int w = it.get_width(), h = it.get_height();
    if (this->frame_ == nullptr && !this->frame_failed_) {
      RAMAllocator<uint16_t> alloc(RAMAllocator<uint16_t>::ALLOC_EXTERNAL);
      this->frame_ = alloc.allocate(w * h);
      this->frame_failed_ = this->frame_ == nullptr;
      if (this->frame_failed_) ESP_LOGW("ctrlable_faces", "no PSRAM for a %dx%d frame; drawing the slow way", w, h);
    }
    if (this->frame_ == nullptr) {
      Adapter a(it, this->font_);
      ::cfaces::render(a, this->style_, this->step_, t, lvl, this->round_, this->logo_left_);
      if (overlay) overlay(it);
      return;
    }
    ::cfaces::Frame565 f(this->frame_, w, h, this->big_endian_);
    ::cfaces::render(f, this->style_, this->step_, t, lvl, this->round_, this->logo_left_);
    FrameDisplay fd(f, w, h);
    if (f.text_s != nullptr && this->font_ != nullptr)
      fd.print(f.text_x, f.text_y, this->font_, Color(f.text_c.r, f.text_c.g, f.text_c.b), display::TextAlign::CENTER,
               f.text_s, Color::BLACK);
    if (overlay) overlay(fd);
    it.draw_pixels_at(0, 0, w, h, reinterpret_cast<const uint8_t *>(this->frame_), display::COLOR_ORDER_RGB,
                      display::COLOR_BITNESS_565, this->big_endian_, 0, 0, 0);
  }
  void set_big_endian(bool b) { this->big_endian_ = b; }

 protected:
  // A display whose pixels land in our frame, so ESPHome's own font code (and
  // anything else a lambda draws) composes into the one frame we send.
  class FrameDisplay : public display::Display {
   public:
    FrameDisplay(::cfaces::Frame565 &f, int w, int h) : f_(f), w_(w), h_(h) {}
    void draw_pixel_at(int x, int y, Color c) override { f_.put(x, y, RGB{c.r, c.g, c.b}); }
    display::DisplayType get_display_type() override { return display::DISPLAY_TYPE_COLOR; }
    void update() override {}

   protected:
    int get_width_internal() override { return w_; }
    int get_height_internal() override { return h_; }

   private:
    ::cfaces::Frame565 &f_;
    int w_, h_;
  };

  class Adapter : public ::cfaces::Canvas {
   public:
    Adapter(display::Display &it, display::BaseFont *f) : it_(it), font_(f) {}
    int width() const override { return it_.get_width(); }
    int height() const override { return it_.get_height(); }
    void fill(RGB c) override { it_.fill(Color(c.r, c.g, c.b)); }
    void disc(int x, int y, int r, RGB c) override { it_.filled_circle(x, y, r, Color(c.r, c.g, c.b)); }
    void rect(int x, int y, int w, int h, RGB c) override {
      if (w > 0 && h > 0) it_.filled_rectangle(x, y, w, h, Color(c.r, c.g, c.b));
    }
    void text(int x, int y, const char *s, RGB c) override {
      if (font_ != nullptr) it_.print(x, y, font_, Color(c.r, c.g, c.b), display::TextAlign::CENTER, s);
    }

   private:
    display::Display &it_;
    display::BaseFont *font_;
  };

  display::Display *display_{nullptr};
  display::BaseFont *font_{nullptr};
  bool round_{false};
  bool logo_left_{false};
  bool active_{true};
  bool dirty_{true};
  Style style_{::cfaces::ORB};
  Step step_{::cfaces::STARTING};
  uint32_t step_start_{0};
  uint32_t last_frame_{0};
  float level_{0};
  uint32_t level_ms_{0};
  float floor_db_{58};
  uint16_t *frame_{nullptr};
  bool frame_failed_{false};
  bool big_endian_{true};
};

}  // namespace ctrlable_faces
}  // namespace esphome
