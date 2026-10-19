// Renders every style x step x screen of components/ctrlable_faces to PPM files
// and checks what each frame must contain. The PPMs are assembled into a
// contact sheet by contact_sheet.py, to compare with the design preview.
//
//   c++ -std=c++17 -O2 -I esphome/components tests/ctrlable_faces/render_test.cpp -o /tmp/faces && /tmp/faces OUTDIR
#include "ctrlable_faces/faces_render.h"

#include <cstdio>
#include <string>
#include <vector>

using namespace cfaces;

struct Buf : Canvas {
  int w, h;
  std::vector<RGB> px;
  long fills = 0;
  std::vector<std::string> texts;
  Buf(int w, int h) : w(w), h(h), px(w * h) {}
  int width() const override { return w; }
  int height() const override { return h; }
  void fill(RGB c) override { std::fill(px.begin(), px.end(), c); }
  long off_side = 0;  // lit pixels that fell off the left or right edge
  void put(int x, int y, RGB c) {
    if (x >= 0 && y >= 0 && x < w && y < h) px[y * w + x] = c;
    else if ((x < 0 || x >= w) && c.r + c.g + c.b > 40) off_side++;
  }
  void disc(int x, int y, int r, RGB c) override {  // same scanline shape as ESPHome's filled_circle
    fills++;
    for (int dy = -r; dy <= r; dy++)
      for (int dx = -r; dx <= r; dx++)
        if (dx * dx + dy * dy <= r * r) put(x + dx, y + dy, c);
  }
  void rect(int x, int y, int rw, int rh, RGB c) override {
    fills++;
    for (int j = y; j < y + rh; j++)
      for (int i = x; i < x + rw; i++) put(i, j, c);
  }
  void text(int x, int y, const char *s, RGB c) override {
    texts.push_back(s);
    for (int i = -40; i <= 40; i++) put(x + i, y, c);  // a visible stand-in for the line of text
  }
  int lit(int x0, int y0, int x1, int y1, int min = 40) const {
    int n = 0;
    for (int y = y0; y < y1; y++)
      for (int x = x0; x < x1; x++) {
        const RGB &p = px[y * w + x];
        if (p.r + p.g + p.b > min) n++;
      }
    return n;
  }
  bool amber() const {
    for (auto &p : px) if (p.r > 150 && p.g > 90 && p.b < 90) return true;
    return false;
  }
  void save(const std::string &path) const {
    FILE *f = fopen(path.c_str(), "wb");
    fprintf(f, "P6\n%d %d\n255\n", w, h);
    for (auto &p : px) fwrite(&p, 3, 1, f);
    fclose(f);
  }
};

static int fails = 0;
static void check(bool ok, const std::string &why) {
  if (!ok) { fails++; printf("FAIL %s\n", why.c_str()); }
}

int main(int argc, char **argv) {
  std::string out = argc > 1 ? argv[1] : ".";
  const char *styles[] = {"orb", "halo", "dot"};
  const char *steps[] = {"starting", "idle", "awake", "listening", "thinking", "replying", "muted", "error", "nowifi", "noserver"};
  long worst = 0;
  for (int round = 1; round >= 0; round--) {
    int W = round ? 360 : 320, H = round ? 360 : 240;
    for (int s = 0; s < 3; s++) {
      std::vector<std::vector<RGB>> frames;
      for (int st = 0; st < 10; st++) {
        Buf b(W, H);
        float t = st == AWAKE ? 0.5f : (st == ERROR ? 0.1f : 1.3f);
        render(b, (Style) s, (Step) st, t, st == LISTENING ? 0.7f : -1, round, !round);
        std::string name = std::string(styles[s]) + "_" + steps[st] + (round ? "_round" : "_rect");
        b.save(out + "/" + name + ".ppm");
        worst = std::max(worst, b.fills);
        check(b.lit(0, 0, W, H) > 200, name + ": next to nothing drawn");
        // the mark is always on screen: halo puts it in the middle, the others at the bottom
        int ly = s == HALO ? (int) (H / 2 - 30) : H - 50;
        int lx0 = (!round && s != HALO) ? 0 : W / 2 - 40, lx1 = (!round && s != HALO) ? 60 : W / 2 + 40;
        check(b.lit(lx0, ly, lx1, std::min(H, ly + 60)) > 30, name + ": the Ctrlable mark is missing");
        bool problem = st == MUTED || st == ERROR || st == NO_WIFI || st == NO_SERVER;
        check(problem == !b.texts.empty(), name + ": a label only on the four problem steps");
        check((st == ERROR) == b.amber(), name + ": amber is for errors only");
        frames.push_back(b.px);
      }
      // every step looks different from every other within a style
      for (int a = 0; a < 10; a++)
        for (int c = a + 1; c < 10; c++) {
          long diff = 0;
          for (size_t i = 0; i < frames[a].size(); i++) {
            const RGB &x = frames[a][i], &y = frames[c][i];
            diff += (x.r != y.r || x.g != y.g || x.b != y.b);
          }
          check(diff > 300, std::string(styles[s]) + ": " + steps[a] + " and " + steps[c] + " look the same");
        }
    }
  }
  // the listening face follows the level it is given
  for (int s = 0; s < 3; s++) {
    Buf q(360, 360), l(360, 360);
    render(q, (Style) s, LISTENING, 1.3f, 0.0f, true);
    render(l, (Style) s, LISTENING, 1.3f, 1.0f, true);
    check(l.lit(0, 0, 360, 360) > q.lit(0, 0, 360, 360), std::string(styles[s]) + ": listening does not grow with the voice");
  }
  // The fast frame canvas the firmware uses draws exactly what the reference
  // canvas draws, for every style, step and screen.
  for (int round = 1; round >= 0; round--) {
    int W = round ? 360 : 320, H = round ? 360 : 240;
    std::vector<uint16_t> fb(W * H);
    for (int s = 0; s < 3; s++)
      for (int st = 0; st < 10; st++) {
        Buf ref(W, H);
        Frame565 fast(fb.data(), W, H, true);
        render(ref, (Style) s, (Step) st, 1.3f, 0.6f, round, !round);
        render(fast, (Style) s, (Step) st, 1.3f, 0.6f, round, !round);
        long bad = 0;
        for (int i = 0; i < W * H; i++) bad += fb[i] != fast.pack(ref.px[i]);
        // the reference draws a stand-in line for text; the frame leaves text to the font code
        long allowed = ref.texts.empty() ? 0 : 81;
        check(bad <= allowed, std::string("Frame565 differs from the reference: ") + styles[s] + " " + steps[st] +
                                  (round ? " round" : " rect") + " (" + std::to_string(bad) + " px)");
      }
  }
  // Rotated 90 degrees the CoreS3 is 240 wide and 320 tall. Every face must fit
  // the width as well as it fits the 320x240 landscape it was drawn for: no
  // more spills off the sides than there, and the label must stay on screen.
  for (int s = 0; s < 3; s++)
    for (int st = 0; st < 10; st++)
      for (float t : {0.3f, 1.3f, 2.6f}) {
        Buf land(320, 240), port(240, 320);
        render(land, (Style) s, (Step) st, t, st == LISTENING ? 1.0f : -1, false, true);
        render(port, (Style) s, (Step) st, t, st == LISTENING ? 1.0f : -1, false, true);
        check(port.off_side <= land.off_side + 50,
              std::string("portrait spills off the sides: ") + styles[s] + " " + steps[st] + " (" +
                  std::to_string(port.off_side) + " px vs " + std::to_string(land.off_side) + " in landscape)");
      }
  check(frame_interval_ms(MUTED, 5) == 0 && frame_interval_ms(ERROR, 3) == 0, "static steps must not keep redrawing");
  check(frame_interval_ms(IDLE, 1) >= 100, "the resting face must not redraw fast");
  printf("worst frame: %ld fills\n", worst);
  printf(fails ? "FAIL (%d)\n" : "ok\n", fails);
  return fails ? 1 : 0;
}
