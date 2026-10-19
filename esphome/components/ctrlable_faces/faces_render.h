#pragma once
// Ctrlable satellite screen faces: Orb, Halo, Dot & Bar.
//
// Plain C++ on purpose -- no ESPHome headers -- so tests/ctrlable_faces renders
// every style and step to images on a desktop and they can be checked against
// the design preview before anything is flashed.
//
// Everything is a disc, an arc, a line or a rectangle in flat colour. Opacity
// is pre-blended onto the black ground (colour * alpha), which is exact for the
// order these are drawn in: outer, dimmer shapes first, inner brighter ones on
// top. Thick lines and arcs are stamped with small discs, so a 360x360 frame is
// a few thousand cheap fills.
#include <algorithm>
#include <cmath>
#include <cstdint>

namespace cfaces {

struct RGB {
  uint8_t r, g, b;
};

class Canvas {
 public:
  virtual ~Canvas() = default;
  virtual int width() const = 0;
  virtual int height() const = 0;
  virtual void fill(RGB c) = 0;
  virtual void disc(int x, int y, int r, RGB c) = 0;
  virtual void rect(int x, int y, int w, int h, RGB c) = 0;
  // Centred on (x, y). May do nothing when no font was given.
  virtual void text(int x, int y, const char *s, RGB c) = 0;
};

// Draws into a caller-owned RGB565 frame, one memory fill per row of a shape.
// ESPHome's own filled_circle goes through a virtual draw_pixel_at per pixel
// and redraws rows; on the 360x360 panel that held the main loop 62-81 ms a
// frame (2026-10-17). Filling rows here and handing the driver one finished
// frame costs a few ms plus the bus transfer.
// big_endian: store each pixel high byte first, as the SPI panels expect.
class Frame565 : public Canvas {
 public:
  Frame565(uint16_t *px, int w, int h, bool big_endian) : px_(px), w_(w), h_(h), be_(big_endian) {}
  int width() const override { return w_; }
  int height() const override { return h_; }
  uint16_t pack(RGB c) const {
    uint16_t v = (uint16_t) (((c.r & 0xF8) << 8) | ((c.g & 0xFC) << 3) | (c.b >> 3));
    return be_ ? (uint16_t) ((v >> 8) | (v << 8)) : v;
  }
  void fill(RGB c) override { std::fill(px_, px_ + w_ * h_, pack(c)); }
  void span(int y, int x0, int x1, uint16_t v) {
    if (y < 0 || y >= h_) return;
    if (x0 < 0) x0 = 0;
    if (x1 >= w_) x1 = w_ - 1;
    if (x0 > x1) return;
    std::fill(px_ + y * w_ + x0, px_ + y * w_ + x1 + 1, v);
  }
  // The same pixel set as the reference test canvas: dx*dx + dy*dy <= r*r.
  void disc(int x, int y, int r, RGB c) override {
    uint16_t v = pack(c);
    for (int dy = -r; dy <= r; dy++) {
      int half = (int) std::sqrt((float) (r * r - dy * dy));
      while ((half + 1) * (half + 1) + dy * dy <= r * r) half++;
      while (half > 0 && half * half + dy * dy > r * r) half--;
      span(y + dy, x - half, x + half, v);
    }
  }
  void rect(int x, int y, int w, int h, RGB c) override {
    uint16_t v = pack(c);
    for (int j = y; j < y + h; j++) span(j, x, x + w - 1, v);
  }
  void put(int x, int y, RGB c) {
    if (x >= 0 && y >= 0 && x < w_ && y < h_) px_[y * w_ + x] = pack(c);
  }
  // Text is the caller's: it is drawn by the display's own font code after the
  // frame is pushed. Remember what was asked for.
  void text(int x, int y, const char *s, RGB c) override {
    text_x = x;
    text_y = y;
    text_s = s;
    text_c = c;
  }
  const char *text_s{nullptr};
  int text_x{0}, text_y{0};
  RGB text_c{0, 0, 0};

 private:
  uint16_t *px_;
  int w_, h_;
  bool be_;
};

enum Style { ORB = 0, HALO = 1, DOT_BAR = 2 };
enum Step {
  STARTING = 0,
  IDLE = 1,
  AWAKE = 2,
  LISTENING = 3,
  THINKING = 4,
  REPLYING = 5,
  MUTED = 6,
  ERROR = 7,
  NO_WIFI = 8,
  NO_SERVER = 9,
};

// Milliseconds between frames for a step; 0 = draw once, nothing moves.
// Resting steps move slowly and get few frames: the audio comes first.
inline uint32_t frame_interval_ms(Step s, float t) {
  switch (s) {
    case AWAKE:
    case LISTENING:
    case THINKING:
    case REPLYING:
      return 60;
    case ERROR:
      return t < 1.4f ? 60 : 0;  // blink twice, then hold
    case MUTED:
      return 0;
    default:
      return 120;
  }
}

namespace detail {

constexpr float PI = 3.14159265f;
constexpr RGB CYAN{0x00, 0xb3, 0xcd};
constexpr RGB AMBER{0xf2, 0xa3, 0x3a};
constexpr RGB GREY{0x5d, 0x6b, 0x72};
constexpr RGB WHITE{0xe9, 0xf6, 0xf8};

inline RGB dim(RGB c, float a) {
  if (a < 0) a = 0;
  if (a > 1) a = 1;
  return RGB{(uint8_t) (c.r * a), (uint8_t) (c.g * a), (uint8_t) (c.b * a)};
}
inline float clamp01(float x) { return x < 0 ? 0 : (x > 1 ? 1 : x); }
inline float ease(float x) {
  x = clamp01(x);
  return 1 - (1 - x) * (1 - x) * (1 - x);
}
// What a voice and an answer look like over time, for when no live level is
// given. Matches the preview page.
inline float voice(float t) {
  float s = std::sin(t * 2 * PI * 4.1f);
  float syl = s > 0 ? s * s : 0;
  float v = syl * (0.55f + 0.45f * std::sin(t * 2.3f + 1)) + 0.08f * std::sin(t * 37);
  return clamp01(v);
}
inline float speech(float t) {
  float s = std::sin(t * 2 * PI * 3.3f + 0.6f);
  float syl = s > 0 ? std::pow(s, 1.5f) : 0;
  return clamp01(syl * (0.6f + 0.4f * std::sin(t * 1.7f)));
}
inline bool blink(float t) { return t >= 1.25f || ((int) (t / 0.3f)) % 2 == 0; }

class Pen {
 public:
  Pen(Canvas &c) : c_(c) {}
  void disc(float x, float y, float r, RGB col, float a) {
    if (r < 0.5f || a <= 0.01f) return;
    c_.disc((int) std::lround(x), (int) std::lround(y), (int) std::lround(r), dim(col, a));
  }
  // A stroke of width w along an arc (radians, 0 = 3 o'clock, clockwise).
  // dash/gap in pixels; 0 = solid.
  void arc(float cx, float cy, float r, float w, RGB col, float a, float from = 0, float to = 2 * PI,
           float dash = 0, float gap = 0) {
    if (a <= 0.01f || r <= 0) return;
    RGB c = dim(col, a);
    int rad = std::max(1, (int) std::lround(w / 2));
    float step = std::max(1.0f, w * 0.45f) / r;
    float len = 0, period = dash + gap;
    for (float th = from; th <= to + 1e-4f; th += step, len += step * r) {
      if (dash > 0 && std::fmod(len, period) > dash) continue;
      c_.disc((int) std::lround(cx + std::cos(th) * r), (int) std::lround(cy + std::sin(th) * r), rad, c);
    }
  }
  void line(float x1, float y1, float x2, float y2, float w, RGB col, float a, float dash = 0, float gap = 0) {
    if (a <= 0.01f) return;
    RGB c = dim(col, a);
    int rad = std::max(1, (int) std::lround(w / 2));
    float dx = x2 - x1, dy = y2 - y1, d = std::sqrt(dx * dx + dy * dy);
    float step = std::max(1.0f, w * 0.45f), period = dash + gap;
    for (float s = 0; s <= d + 1e-3f; s += step) {
      if (dash > 0 && std::fmod(s, period) > dash) continue;
      float k = d > 0 ? s / d : 0;
      c_.disc((int) std::lround(x1 + dx * k), (int) std::lround(y1 + dy * k), rad, c);
    }
  }
  void rect(float x, float y, float w, float h, RGB col, float a) {
    if (a <= 0.01f) return;
    c_.rect((int) std::lround(x), (int) std::lround(y), (int) std::lround(w), (int) std::lround(h), dim(col, a));
  }
  // The Ctrlable mark, from ctrlable_logo.svg (38.84 x 37.42), centred, h tall.
  void logo(float x, float y, float h, float a) {
    float s = h / 37.42f, ox = x - 38.84f * s / 2, oy = y - h / 2, t = 3.7f * s;
    rect(ox, oy, 38.84f * s, t, CYAN, a);
    rect(ox, oy + 33.72f * s, 38.84f * s, t, CYAN, a);
    rect(ox, oy, t, 37.42f * s, CYAN, a);
    rect(ox + 35.14f * s, oy, t, 37.42f * s, CYAN, a);
    rect(ox + 19.87f * s, oy + 16.86f * s, 16.74f * s, 3.7f * s, CYAN, a);
    disc(ox + 19.42f * s, oy + 18.71f * s, 4.46f * s, CYAN, a);
  }

 private:
  Canvas &c_;
};

inline void orb(Pen &p, float cx, float cy, float u, Step st, float t, float L) {
  float R = 34 * u;
  switch (st) {
    case STARTING: {
      p.disc(cx, cy, R * 0.35f, CYAN, 0.35f);
      float a = t * 3.2f, len = 0.6f + 0.5f * std::sin(t * 2.1f);
      p.arc(cx, cy, R * 1.6f, 3 * u, CYAN, 0.9f, a, a + len * PI);
      break;
    }
    case IDLE: {
      float b = std::sin(t * 2 * PI / 4.2f), r = R * (0.82f + 0.06f * b);
      p.disc(cx, cy, r * 1.55f, CYAN, 0.06f);
      p.disc(cx, cy, r * 1.25f, CYAN, 0.12f);
      p.disc(cx, cy, r, CYAN, 0.5f + 0.15f * b);
      break;
    }
    case AWAKE: {
      float k = ease(t / 0.35f), r = R * (0.82f + 0.5f * k), e = clamp01(t / 0.75f);
      p.arc(cx, cy, R * (1.2f + 2.4f * e), 4 * u, CYAN, 0.7f * (1 - e));
      p.disc(cx, cy, r * 1.4f, CYAN, 0.12f);
      p.disc(cx, cy, r, CYAN, 0.7f + 0.3f * k);
      break;
    }
    case LISTENING: {
      float r = R * (1.25f + 0.35f * L);
      p.disc(cx, cy, r * (1.75f + 0.55f * L), CYAN, 0.08f);
      p.disc(cx, cy, r * (1.35f + 0.25f * L), CYAN, 0.2f);
      p.disc(cx, cy, r, CYAN, 1);
      break;
    }
    case THINKING: {
      for (int i = 0; i < 3; i++)
        for (int j = 5; j >= 0; j--) {
          float a = t * 2.7f + i * 2 * PI / 3 - j * 0.16f;
          p.disc(cx + std::cos(a) * 78 * u, cy + std::sin(a) * 78 * u, (7 - j) * u, CYAN, 1 - j * 0.16f);
        }
      p.disc(cx, cy, R * 0.62f, CYAN, 0.85f);
      break;
    }
    case REPLYING: {
      float e = speech(t), r = R * (1.1f + 0.42f * e);
      for (int k = 0; k < 3; k++) {
        float q = std::fmod(t + k * 0.6f, 1.8f) / 1.8f;
        p.arc(cx, cy, R * (1.2f + 1.7f * q), 3 * u, CYAN, 0.45f * (1 - q));
      }
      p.disc(cx, cy, r * 1.3f, CYAN, 0.15f);
      p.disc(cx, cy, r, CYAN, 0.95f);
      break;
    }
    case MUTED:
      p.arc(cx, cy, R * 1.15f, 4 * u, GREY, 0.9f);
      p.line(cx - R * 0.8f, cy + R * 0.8f, cx + R * 0.8f, cy - R * 0.8f, 4 * u, GREY, 0.9f);
      break;
    case ERROR: {
      float on = t < 1.25f ? (blink(t) ? 1 : 0.2f) : 0.6f;
      p.disc(cx, cy, R * 1.25f, AMBER, 0.14f * on);
      p.disc(cx, cy, R * 0.95f, AMBER, on);
      break;
    }
    case NO_WIFI: {
      float off = t * 0.6f;
      p.arc(cx, cy, R * 1.45f, 3.5f * u, GREY, 0.9f, off, off + 2 * PI, 10 * u, 12 * u);
      p.disc(cx, cy, R * 0.3f, GREY, 0.8f);
      break;
    }
    case NO_SERVER: {
      float g = 0.35f + 0.18f * std::sin(t * 1.8f), top = -PI / 2;
      p.arc(cx, cy, R * 1.45f, 3.5f * u, GREY, 0.9f, top + g, top + 2 * PI - g);
      p.disc(cx, cy, R * 0.3f, GREY, 0.8f);
      break;
    }
  }
}

inline void halo(Pen &p, float cx, float cy, float u, bool round, Step st, float t, float L) {
  float R = round ? 150 * u : 104 * u, top = -PI / 2;
  switch (st) {
    case STARTING: {
      float a = t * 3.4f;
      p.arc(cx, cy, R, 2 * u, CYAN, 0.15f);
      p.arc(cx, cy, R, 4 * u, CYAN, 0.9f, a, a + 0.9f);
      break;
    }
    case IDLE: {
      float a = top + t * 2 * PI / 14;
      p.arc(cx, cy, R, 2 * u, CYAN, 0.22f);
      for (int j = 3; j >= 0; j--) p.arc(cx, cy, R, 3 * u, CYAN, 0.45f - j * 0.1f, a - 0.25f * (j + 1), a + 0.25f * (j + 1));
      break;
    }
    case AWAKE: {
      float k = ease(t / 0.4f), w = (2 + 6 * (1 - clamp01((t - 0.4f) / 0.5f))) * u;
      p.arc(cx, cy, R, 2 * u, CYAN, 0.2f);
      p.arc(cx, cy, R, w, CYAN, 1, top - PI * k, top + PI * k);
      break;
    }
    case LISTENING:
      p.arc(cx, cy, R - 14 * u, 2 * u, CYAN, 0.12f + 0.3f * L);
      p.arc(cx, cy, R, (4 + 7 * L) * u, CYAN, 0.55f + 0.45f * L);
      break;
    case THINKING: {
      float a = t * 2 * PI / 1.15f;
      p.arc(cx, cy, R, 2 * u, CYAN, 0.18f);
      for (int j = 5; j >= 0; j--) p.arc(cx, cy, R, 5 * u, CYAN, 1 - j * 0.16f, a - 0.22f * (j + 1), a - 0.22f * j);
      break;
    }
    case REPLYING: {
      float e = speech(t);
      p.arc(cx, cy, R + 12 * u * e, 2 * u, CYAN, 0.25f * e);
      p.arc(cx, cy, R + 3 * u * e, (3 + 6 * e) * u, CYAN, 0.5f + 0.5f * e);
      break;
    }
    case MUTED:
      p.arc(cx, cy, R, 2 * u, GREY, 0.7f);
      p.line(cx - 34 * u, cy + 34 * u, cx + 34 * u, cy - 34 * u, 4 * u, GREY, 0.9f);
      break;
    case ERROR: {
      float on = t < 1.25f ? (blink(t) ? 1 : 0.2f) : 0.55f;
      p.arc(cx, cy, R, (on > 0.9f ? 6 : 3) * u, AMBER, on);
      break;
    }
    case NO_WIFI: {
      float off = t * 0.5f;
      p.arc(cx, cy, R, 3 * u, GREY, 0.85f, off, off + 2 * PI, 14 * u, 16 * u);
      break;
    }
    case NO_SERVER: {
      float g = 0.3f + 0.12f * std::sin(t * 1.8f);
      p.arc(cx, cy, R, 3 * u, GREY, 0.85f, top + g, top + 2 * PI - g);
      break;
    }
  }
}

inline void meter(Pen &p, float x0, float x1, float cy, float u, float t, float level, RGB col, float spread) {
  const int n = 19;
  for (int i = 0; i < n; i++) {
    float x = x0 + 30 * u + i * (x1 - x0 - 30 * u) / (n - 1);
    float fall = 1 - std::fabs(i - (n - 1) / 2.0f) / ((n - 1) / 2.0f) * spread;
    float jit = 0.55f + 0.45f * std::sin(t * 9 + i * 1.7f);
    float h = std::max(3 * u, level * 46 * u * fall * jit);
    p.line(x, cy - h, x, cy + h, 5 * u, col, 0.95f);
  }
}

inline void dotbar(Pen &p, float cx, float cy, float u, Step st, float t, float L) {
  float half = 92 * u, x0 = cx - half, x1 = cx + half, D = 13 * u;
  switch (st) {
    case STARTING: {
      float q = std::fmod(t, 1.6f) / 1.6f;
      p.line(x0, cy, x1, cy, 4 * u, CYAN, 0.2f);
      p.line(x0, cy, x0 + (x1 - x0) * q, cy, 4 * u, CYAN, 0.8f);
      p.disc(x0 + (x1 - x0) * q, cy, D * 0.8f, CYAN, 1);
      break;
    }
    case IDLE: {
      float b = std::sin(t * 2 * PI / 4.2f);
      p.line(x0 + 30 * u, cy, x1, cy, 4 * u, CYAN, 0.25f);
      p.disc(x0, cy, D * (0.9f + 0.08f * b), CYAN, 0.55f + 0.15f * b);
      break;
    }
    case AWAKE: {
      float k = ease(t / 0.35f), pop = 1 + 0.45f * std::sin(clamp01(t / 0.3f) * PI);
      p.line(x0 + 20 * u, cy, x0 + 20 * u + (x1 - x0 - 20 * u) * k, cy, 5 * u, CYAN, 0.9f);
      p.disc(x0, cy, D * pop * 1.6f, CYAN, 0.15f);
      p.disc(x0, cy, D * pop, CYAN, 1);
      break;
    }
    case LISTENING:
      meter(p, x0, x1, cy, u, t, 0.25f + 0.75f * L, CYAN, 0.7f);
      p.disc(x0 - 10 * u, cy, D * (1.5f + 0.4f * L), CYAN, 0.15f);
      p.disc(x0 - 10 * u, cy, D, CYAN, 1);
      break;
    case THINKING: {
      p.line(x0, cy, x1, cy, 4 * u, CYAN, 0.25f);
      for (int j = 3; j >= 0; j--) {
        float q = 0.5f - 0.5f * std::cos((t - j * 0.06f) * 2 * PI / 1.4f);
        p.disc(x0 + (x1 - x0) * q, cy, D * (1 - j * 0.12f), CYAN, j == 0 ? 1 : 0.35f - j * 0.07f);
      }
      break;
    }
    case REPLYING: {
      float e = speech(t);
      meter(p, x0, x1, cy, u, t, 0.2f + 0.8f * e, WHITE, 0.55f);
      p.disc(x1 + 10 * u, cy, D * (1.4f + 0.5f * e), CYAN, 0.18f);
      p.disc(x1 + 10 * u, cy, D, CYAN, 1);
      break;
    }
    case MUTED:
      p.line(x0 + 30 * u, cy, cx - 12 * u, cy, 4 * u, GREY, 0.8f);
      p.line(cx + 12 * u, cy, x1, cy, 4 * u, GREY, 0.8f);
      p.line(cx - 8 * u, cy + 14 * u, cx + 8 * u, cy - 14 * u, 4 * u, GREY, 0.9f);
      p.arc(x0, cy, D, 3 * u, GREY, 0.9f);
      break;
    case ERROR: {
      float on = t < 1.25f ? (blink(t) ? 1 : 0.2f) : 0.6f;
      p.line(x0 + 30 * u, cy, x1, cy, 4 * u, AMBER, 0.25f);
      p.disc(x0, cy, D * 1.1f, AMBER, on);
      break;
    }
    case NO_WIFI: {
      p.line(x0, cy, x1, cy, 4 * u, GREY, 0.8f, 10 * u, 12 * u);
      float q = 0.5f - 0.5f * std::cos(t * 2 * PI / 5);
      p.disc(x0 + (x1 - x0) * q, cy, D * 0.85f, GREY, 0.95f);
      break;
    }
    case NO_SERVER: {
      float g = (10 + 6 * std::sin(t * 1.8f)) * u;
      p.line(x0 + 30 * u, cy, cx - g, cy, 4 * u, GREY, 0.8f);
      p.line(cx + g, cy, x1, cy, 4 * u, GREY, 0.8f);
      p.disc(x0, cy, D * 0.9f, GREY, 0.9f);
      break;
    }
  }
}

inline const char *label_for(Step s) {
  switch (s) {
    case MUTED:
      return "Microphone off";
    case ERROR:
      return "Something went wrong";
    case NO_WIFI:
      return "No Wi-Fi";
    case NO_SERVER:
      return "Can\xe2\x80\x99t reach Ctrlable Pro";
    default:
      return nullptr;
  }
}

}  // namespace detail

// One frame. t = seconds since this step began. level = live mic level 0..1,
// or a negative number to use the built-in voice motion. logo_left moves the
// mark to the bottom-left corner on a rectangular screen (the CoreS3 keeps its
// mute button bottom-right).
inline void render(Canvas &c, Style style, Step step, float t, float level, bool round, bool logo_left = false) {
  using namespace detail;
  Pen p(c);
  c.fill(RGB{0, 0, 0});
  float w = c.width(), h = c.height();
  // Scale by the side that limits: a 320x240 screen is 0.89 either way; turned
  // to 240x320 (screen rotation) the width limits and the face shrinks to fit.
  float u = round ? w / 360 : std::min(w / 360, h / 270);
  float cx = w / 2, cy = round ? h / 2 - 6 * u : h / 2 - 10 * u;
  float L = level < 0 ? voice(t) : clamp01(level);
  switch (style) {
    case HALO:
      halo(p, cx, cy, u, round, step, t, L);
      break;
    case DOT_BAR:
      dotbar(p, cx, cy, u, step, t, L);
      break;
    default:
      orb(p, cx, cy, u, step, t, L);
  }
  // Under the shape; inside the ring for Halo on a short screen, where the
  // ring's bottom reaches the line a label would otherwise sit on.
  float ly = round ? cy + 108 * u : (style == HALO ? cy + 56 * u : h - 40 * u);
  if (const char *lab = label_for(step)) c.text((int) cx, (int) ly, lab, dim(WHITE, 0.72f));
  bool problem = step == NO_WIFI || step == NO_SERVER || step == MUTED;
  if (style == HALO)
    p.logo(cx, cy, 40 * u, problem ? 0.35f : 0.85f);
  else if (round)
    p.logo(cx, h - 34 * u, 20 * u, 0.6f);
  else if (logo_left)
    p.logo(26 * u, h - 22 * u, 17 * u, 0.6f);
  else
    p.logo(w - 26 * u, h - 22 * u, 17 * u, 0.6f);
}

}  // namespace cfaces
