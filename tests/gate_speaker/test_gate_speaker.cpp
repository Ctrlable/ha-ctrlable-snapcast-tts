// Host-side test for gate_speaker (esphome/components/gate_speaker).
//
//   c++ -std=c++17 -Wall -Wextra -I tests/gate_speaker/stubs -I esphome/components/gate_speaker \
//       tests/gate_speaker/test_gate_speaker.cpp esphome/components/gate_speaker/gate_speaker.cpp -o /tmp/g && /tmp/g
//
// Add -DOLD_WIRING to run the same answers through the media player's OLD
// target, the shared-bus I2S speaker itself: that is the build that wedged the
// master bath AtomS3R on 2026-10-12, and it must fail here.
//
// What it pins down:
//   - Snapcast mode, mic holding the shared I2S bus: an answer still finishes,
//     in about its own length, and the I2S speaker is never started (it would
//     be refused: "Parent bus is busy", once a second, forever);
//   - the swallowed answer lasts as long as the audio -- arm_stop_word waits
//     on the announcement ending, so an instant drop would disarm "stop" after
//     a moment;
//   - stop() ends it at once; pause does not count as played;
//   - Regular mode (passthrough) really plays: bytes, stream info, volume and
//     state reach the I2S speaker;
//   - switching modes mid-answer stops the real speaker.
#include "gate_speaker.h"

#include <cstdio>
#include <vector>

#include <esp_timer.h>
#include <freertos/task.h>

using namespace esphome;
using gate_speaker::GateSpeaker;

static int failures = 0;
#define CHECK(cond, why)                                       \
  do {                                                         \
    if (!(cond)) {                                             \
      std::printf("FAIL %s:%d %s\n", __FILE__, __LINE__, why); \
      ++failures;                                              \
    }                                                          \
  } while (0)

// --- fake clock: 1 FreeRTOS tick = 1 ms ---
static int64_t now_us = 1000000;
int64_t esp_timer_get_time() { return now_us; }
void vTaskDelay(TickType_t ticks) { now_us += (int64_t) ticks * 1000; }

// The AtomS3R's i2s_speaker as far as the media pipeline can tell: it starts
// only if the bus is free, and drains at the stream's real rate once running.
class FakeI2S : public speaker::Speaker {
 public:
  bool bus_held_by_mic{false};
  int starts{0};
  size_t bytes{0};
  int64_t run_from_us{0};

  void start() override {
    ++this->starts;
    this->state_ = this->bus_held_by_mic ? speaker::STATE_STARTING : speaker::STATE_RUNNING;
    if (this->state_ == speaker::STATE_RUNNING)
      this->run_from_us = now_us;
  }
  void stop() override { this->state_ = speaker::STATE_STOPPED; this->bytes = 0; }
  void finish() override { this->finishing_ = true; }
  size_t play(const uint8_t *, size_t length) override { return this->play(nullptr, length, 0); }
  size_t play(const uint8_t *, size_t length, TickType_t ticks) override {
    if (this->state_ != speaker::STATE_RUNNING && this->state_ != speaker::STATE_STARTING)
      this->start();
    if (this->state_ != speaker::STATE_RUNNING) {
      vTaskDelay(ticks);  // what the real one does while it cannot get the bus
      return 0;
    }
    this->bytes += length;
    return length;
  }
  int64_t played() const {
    const int64_t bps = this->audio_stream_info_.frames_to_bytes(this->audio_stream_info_.get_sample_rate());
    return (now_us - this->run_from_us) * bps / 1000000;
  }
  bool has_buffered_data() const override {
    return this->state_ == speaker::STATE_RUNNING && (int64_t) this->bytes > this->played();
  }
  void tick() {
    if (this->finishing_ && !this->has_buffered_data()) {
      this->finishing_ = false;
      this->state_ = speaker::STATE_STOPPED;
    }
  }
  float last_volume() const { return this->volume_; }

 protected:
  bool finishing_{false};
};

static const audio::AudioStreamInfo TTS(16, 1, 48000);  // the announcement pipeline's format
static const size_t BPS = 96000;                         // bytes per second of it

// Plays `seconds` of audio the way AudioPipeline does -- 4 KB writes, 25 ms
// ticks_to_wait, then finish() and wait for the speaker to stop -- and returns
// how long the announcement lasted, or -1 if it never ended within `limit_s`.
static double play_answer(speaker::Speaker &spk, FakeI2S &i2s, double seconds, double limit_s, GateSpeaker *gate) {
  spk.set_audio_stream_info(TTS);
  const int64_t t0 = now_us;
  size_t left = (size_t) (seconds * BPS);
  std::vector<uint8_t> chunk(4096);
  while (left > 0) {
    const size_t n = spk.play(chunk.data(), std::min(left, chunk.size()), 25);
    left -= n;
    if (n == 0)
      vTaskDelay(1);
    i2s.tick();
    if (gate) gate->loop();
    if (now_us - t0 > (int64_t) (limit_s * 1e6))
      return -1;
  }
  spk.finish();
  for (;;) {
    i2s.tick();
    if (gate) gate->loop();
    if (spk.is_stopped())
      return (now_us - t0) / 1e6;
    vTaskDelay(10);
    if (now_us - t0 > (int64_t) (limit_s * 1e6))
      return -1;
  }
}


int main() {
  // 1. Snapcast mode: the mic holds the bus for "stop", an answer arrives.
  {
    FakeI2S i2s;
    i2s.bus_held_by_mic = true;
#ifdef OLD_WIRING
    speaker::Speaker &target = i2s;  // announcement_pipeline: speaker: i2s_speaker
    GateSpeaker *gate = nullptr;
#else
    static GateSpeaker g;
    g.set_output(&i2s);
    g.setup();
    g.set_passthrough(false);
    speaker::Speaker &target = g;
    GateSpeaker *gate = &g;
#endif
    const double took = play_answer(target, i2s, 6.0, 30.0, gate);
    std::printf("snapcast answer of 6.0 s ended after %.2f s\n", took);
    CHECK(took > 0, "an answer must finish while the mic holds the bus (old build: wedged for 22 h)");
    CHECK(took >= 5.8 && took <= 6.4, "the swallowed answer lasts as long as the audio (arm_stop_word waits on it)");
    CHECK(i2s.starts == 0, "the shared-bus speaker is never started in Snapcast mode");
  }
#ifndef OLD_WIRING
  // 2. stop() mid-answer ends it at once; a pause is not counted as played.
  {
    FakeI2S i2s;
    GateSpeaker g;
    g.set_output(&i2s);
    g.setup();
    g.set_audio_stream_info(TTS);
    uint8_t buf[4096] = {};
    size_t got = g.play(buf, sizeof buf, 0);
    CHECK(g.is_running() && got > 0, "swallow mode runs on first play");
    g.stop();
    CHECK(g.is_stopped() && !g.has_buffered_data(), "stop() is immediate");

    g.play(buf, sizeof buf, 0);
    size_t fed = 0;
    for (int i = 0; i < 100; ++i) { fed += g.play(buf, sizeof buf, 0); vTaskDelay(10); }  // ~1 s
    g.set_pause_state(true);
    CHECK(g.play(buf, sizeof buf, 0) == 0, "paused: nothing is accepted");
    vTaskDelay(3000);
    g.set_pause_state(false);
    g.finish();
    const int64_t t0 = now_us;
    while (!g.is_stopped() && now_us - t0 < 5000000) { g.loop(); vTaskDelay(10); }
    CHECK(now_us - t0 < 400000, "after a 3 s pause only the ~200 ms lead remains to drain, not 3 s more nor 0");
    CHECK(now_us - t0 > 50000, "...and the lead does drain in real time");
    (void) fed;
  }
  // 3. Regular mode: the answer really plays, at the user's volume.
  {
    FakeI2S i2s;
    GateSpeaker g;
    g.set_output(&i2s);
    g.setup();
    g.set_volume(0.7f);
    g.set_passthrough(true);
    CHECK(i2s.last_volume() > 0.69f && i2s.last_volume() < 0.71f, "entering Regular hands the slider to the DAC");
    const double took = play_answer(g, i2s, 2.0, 10.0, &g);
    std::printf("regular answer of 2.0 s ended after %.2f s, %zu bytes reached the speaker\n", took, i2s.bytes);
    CHECK(i2s.starts == 1, "the real speaker is started once");
    CHECK(took >= 1.9 && took <= 2.3, "and plays for the length of the answer");
    g.set_volume(0.3f);
    CHECK(i2s.last_volume() < 0.31f, "volume changes reach the DAC in Regular");
  }
  // 4. Leaving Regular mid-answer silences the real speaker.
  {
    FakeI2S i2s;
    GateSpeaker g;
    g.set_output(&i2s);
    g.setup();
    g.set_volume(0.5f);
    g.set_passthrough(true);
    g.set_audio_stream_info(TTS);
    uint8_t buf[4096] = {};
    g.play(buf, sizeof buf, 0);
    g.loop();
    CHECK(i2s.is_running() && g.is_running(), "Regular: the gate mirrors the running speaker");
    g.set_passthrough(false);
    CHECK(i2s.is_stopped(), "switching to Snapcast stops the real speaker");
    g.set_volume(0.9f);
    CHECK(i2s.last_volume() > 0.49f && i2s.last_volume() < 0.51f, "the slider does not reach the DAC outside Regular");
    CHECK(i2s.starts == 1, "and swallow mode never restarts it");
  }
#endif
  std::printf(failures ? "FAIL (%d)\n" : "ok\n", failures);
  return failures ? 1 : 0;
}
