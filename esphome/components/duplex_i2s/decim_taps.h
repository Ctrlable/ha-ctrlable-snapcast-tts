#pragma once
// Generated: 96-tap Blackman-windowed sinc, cutoff 6.8 kHz at 48 kHz, Q15,
// DC gain exactly 1.0 (taps sum to 32768). Stopband ~-74 dB from ~8.2 kHz,
// so the 48 -> 16 kHz decimation folds nothing audible onto the wake word.
#include <cstdint>
namespace esphome::duplex_i2s {
static constexpr int DECIM_TAPS = 96;
static const int16_t DECIM_COEFFS[DECIM_TAPS] = {
    0, 0, 0, 1, 1, 0, -3, -5, -4, 3, 12, 14,
    4, -16, -30, -22, 10, 48, 56, 17, -52, -100, -76, 24,
    136, 164, 58, -131, -262, -205, 44, 326, 408, 163, -289, -622,
    -517, 64, 768, 1033, 481, -710, -1766, -1700, 76, 3273, 6734, 8977,
    8975, 6734, 3273, 76, -1700, -1766, -710, 481, 1033, 768, 64, -517,
    -622, -289, 163, 408, 326, 44, -205, -262, -131, 58, 164, 136,
    24, -76, -100, -52, 17, 56, 48, 10, -22, -30, -16, 4,
    14, 12, 3, -4, -5, -3, 0, 1, 1, 0, 0, 0};
}  // namespace esphome::duplex_i2s
