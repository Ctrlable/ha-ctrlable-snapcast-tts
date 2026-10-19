// c++ -std=c++17 -I esphome/components tests/aec_mic/test_gap_watch.cpp -o /tmp/t && /tmp/t
#include <cstdio>
#include <cstdlib>

#include "aec_mic/gap_watch.h"

using esphome::aec_mic::GapWatch;

static int fails = 0;
#define CHECK(c, ...)            \
  do {                           \
    if (!(c)) {                  \
      printf("FAIL: " __VA_ARGS__); \
      printf("\n");              \
      fails++;                   \
    }                            \
  } while (0)

// 32 ms blocks (512 samples) for `ms`, with arrival jitter up to `jitter` samples.
static int64_t run(GapWatch &g, int64_t &t, int64_t &src, int ms, int jitter, int64_t drop_at = -1, int64_t drop = 0) {
  int64_t lost = 0;
  const int64_t end = t + ms * 16;
  while (t < end) {
    src += 512;
    t += 512;
    if (drop_at >= 0 && t >= drop_at && drop > 0) {
      t += drop;  // the clock moves on, the samples never come
      drop = 0;
    }
    const int64_t late = jitter ? (std::rand() % jitter) : 0;
    lost += g.feed(t + late, 512);
  }
  return lost;
}

int main() {
  std::srand(1);
  {
    GapWatch g;
    int64_t t = 0, src = 0;
    int64_t lost = run(g, t, src, 60000, 800);  // 60 s, callbacks up to 50 ms late
    CHECK(lost == 0, "steady stream with jitter reported %lld lost samples", (long long) lost);
  }
  {
    GapWatch g;
    int64_t t = 0, src = 0;
    int64_t lost = run(g, t, src, 30000, 800, 8 * 16000, 300 * 16);  // 300 ms vanish at 8 s
    CHECK(lost >= 290 * 16 && lost <= 310 * 16, "300 ms gap reported as %lld ms", (long long) (lost / 16));
  }
  {
    GapWatch g;
    int64_t t = 0, src = 0;
    int64_t lost = run(g, t, src, 10000, 0, 2 * 16000, 20 * 16);  // 20 ms: below threshold
    CHECK(lost == 0, "20 ms gap should be ignored, got %lld", (long long) lost);
  }
  printf(fails ? "FAILED (%d)\n" : "OK\n", fails);
  return fails ? 1 : 0;
}
