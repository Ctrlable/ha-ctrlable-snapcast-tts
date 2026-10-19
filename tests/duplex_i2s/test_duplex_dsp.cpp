// Host test for duplex_i2s's mic-side DSP (48 kHz bus -> 16 kHz wake word).
//
//   c++ -std=c++17 -O2 -Wall -Wextra -I esphome/components \
//       tests/duplex_i2s/test_duplex_dsp.cpp -o /tmp/dd && /tmp/dd
//
// The same checks also run against a naive "keep every third sample"
// decimator and MUST fail there: that is the version this filter replaces in
// spirit, and a check that cannot tell the two apart proves nothing.

#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <vector>

#include "duplex_i2s/duplex_dsp.h"

using namespace esphome::duplex_i2s;

static int failures = 0;
#define CHECK(cond, ...)                \
  do {                                  \
    if (!(cond)) {                      \
      std::printf("  FAIL: " __VA_ARGS__); \
      std::printf("\n");                \
      failures++;                       \
    }                                   \
  } while (0)

struct Naive {
  int phase{0};
  size_t process(const int16_t *in, size_t n, int16_t *out) {
    size_t k = 0;
    for (size_t i = 0; i < n; i++)
      if (++phase == 3) {
        phase = 0;
        out[k++] = in[i];
      }
    return k;
  }
};

static std::vector<int16_t> sine(double hz, double amp, size_t n) {
  std::vector<int16_t> v(n);
  for (size_t i = 0; i < n; i++)
    v[i] = (int16_t) std::lround(amp * std::sin(2 * M_PI * hz * i / 48000.0));
  return v;
}

// RMS of the output after the filter has settled (skip the first 100 samples).
template<typename D> static double out_rms(D &d, const std::vector<int16_t> &in) {
  std::vector<int16_t> out(in.size() / 3 + 2);
  size_t n = d.process(in.data(), in.size(), out.data());
  double s = 0;
  size_t c = 0;
  for (size_t i = 100; i < n; i++, c++)
    s += (double) out[i] * out[i];
  return std::sqrt(s / c);
}

template<typename D> static int run_filter_checks(const char *name) {
  int before = failures;
  std::printf("%s\n", name);
  const double amp = 10000, in_rms = amp / std::sqrt(2.0);

  {  // speech band passes untouched
    D d;
    double g = 20 * std::log10(out_rms(d, sine(1000, amp, 48000)) / in_rms);
    CHECK(std::fabs(g) < 0.5, "1 kHz gain %.2f dB, want ~0", g);
  }
  {  // 12 kHz must not fold back to 4 kHz -- the reason a filter exists at all
    D d;
    double g = 20 * std::log10(out_rms(d, sine(12000, amp, 48000)) / in_rms + 1e-12);
    CHECK(g < -60, "12 kHz leaks at %.1f dB (folds onto 4 kHz), want < -60", g);
  }
  {  // 9 kHz -> would land on 7 kHz
    D d;
    double g = 20 * std::log10(out_rms(d, sine(9000, amp, 48000)) / in_rms + 1e-12);
    CHECK(g < -50, "9 kHz leaks at %.1f dB, want < -50", g);
  }
  return failures - before;
}

int main() {
  run_filter_checks<Decimator3>("Decimator3");

  {  // DC passes at exactly unity once settled
    Decimator3 d;
    d.reset();
    std::vector<int16_t> in(3000, 1000), out(1001);
    size_t n = d.process(in.data(), in.size(), out.data());
    CHECK(n == 1000, "3000 in -> %zu out, want 1000", n);
    CHECK(out[n - 1] == 1000, "DC 1000 -> %d", out[n - 1]);
  }
  {  // any block split gives the same samples as one long block
    std::vector<int16_t> in(48000);
    srand(7);
    for (auto &s : in)
      s = (int16_t) ((rand() % 40000) - 20000);
    Decimator3 whole;
    std::vector<int16_t> a(16001);
    size_t na = whole.process(in.data(), in.size(), a.data());

    Decimator3 parts;
    std::vector<int16_t> b;
    size_t i = 0;
    while (i < in.size()) {
      size_t len = std::min<size_t>(1 + rand() % 997, in.size() - i);  // odd sizes, not multiples of 3
      int16_t tmp[400];
      size_t k = parts.process(&in[i], len, tmp);
      b.insert(b.end(), tmp, tmp + k);
      i += len;
    }
    CHECK(na == b.size(), "split produced %zu samples, whole %zu", b.size(), na);
    bool same = na == b.size();
    for (size_t j = 0; same && j < na; j++)
      same = a[j] == b[j];
    CHECK(same, "split output differs from whole-block output");
  }
  {  // full-scale input must clip, not wrap
    Decimator3 d;
    std::vector<int16_t> in(3000), out(1001);
    for (size_t j = 0; j < in.size(); j++)
      in[j] = (j / 24) % 2 ? 32767 : -32768;  // 1 kHz square, overshoots after filtering
    size_t n = d.process(in.data(), in.size(), out.data());
    bool wrapped = false;
    for (size_t j = 101; j < n; j++)
      if ((out[j] > 0) != (out[j - 1] > 0) && std::abs(out[j] - out[j - 1]) > 40000)
        wrapped = true;
    CHECK(!wrapped, "overshoot wrapped around instead of clipping");
  }
  {  // slot handling
    int16_t st[6] = {1, -1, 2, -2, 3, -3}, m[3], back[6];
    pick_slot(st, 3, 1, m);
    CHECK(m[0] == -1 && m[1] == -2 && m[2] == -3, "pick_slot(1) wrong");
    pick_slot(st, 3, 0, m);
    CHECK(m[0] == 1 && m[2] == 3, "pick_slot(0) wrong");
    mono_to_stereo(m, 3, back);
    CHECK(back[0] == 1 && back[1] == 1 && back[4] == 3 && back[5] == 3, "mono_to_stereo wrong");
  }

  int real_failures = failures;
  // PlaybackGate: the canceller runs while the speaker plays and for the echo
  // tail after it, never while the device is quiet.
  {
    using esphome::duplex_i2s::PlaybackGate;
    PlaybackGate g(64, 9600);  // 600 ms at 16 kHz
    std::vector<int16_t> quiet(512, 0), dither(512, 0), loud(512, 0);
    for (size_t i = 0; i < 512; i++) {
      dither[i] = (int16_t) ((i % 7) * 9 - 27);  // |x| <= 27: DAC noise, not sound
      loud[i] = (int16_t) (3000 * std::sin(i * 0.2));
    }
    CHECK(!g.feed(quiet.data(), 512), "gate on before anything played");
    CHECK(!g.feed(dither.data(), 512), "gate opened on dither");
    CHECK(g.feed(loud.data(), 512), "gate closed while the speaker plays");
    int open_chunks = 0;
    for (int k = 0; k < 40; k++)
      open_chunks += g.feed(quiet.data(), 512) ? 1 : 0;
    // 9600 / 512 = 18.75: open for the first 18 quiet chunks, then closed.
    CHECK(open_chunks == 18, "echo tail held %d chunks, want 18 (600 ms)", open_chunks);
    CHECK(!g.feed(quiet.data(), 512), "gate stuck open after the tail");
    CHECK(g.feed(loud.data(), 512), "gate did not reopen on the next sound");
  }

  // The checks must be able to catch an unfiltered decimator.
  int naive_failures = run_filter_checks<Naive>("Naive (must FAIL)");
  failures = real_failures;
  CHECK(naive_failures > 0, "the filter checks passed a decimator with no filter -- they prove nothing");

  if (failures) {
    std::printf("\nFAILED (%d)\n", failures);
    return 1;
  }
  std::printf("\nOK\n");
  return 0;
}
