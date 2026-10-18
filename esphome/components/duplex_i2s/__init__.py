"""duplex_i2s: one I2S port running the speaker and the microphone AT THE SAME TIME.

Why this exists: ESPHome's i2s_audio lets either the microphone or the speaker
hold a bus, never both. On boards where the DAC and the ADC share one bus --
the Waveshare ESP32-S3-Touch-LCD-1.85C's ES8311 + ES7210, the AtomS3R Echo --
the mic is therefore OFF whenever the device makes a sound, so the stop word
cannot hear anything during an answer (2026-10-17: "I said a bunch of times
stop but no luck"). The chips have separate data lines (DOUT to the DAC, DIN
from the ADC) and are happy full duplex; only the software was not.

This opens the port once, as a TX + RX pair on one clock, and never closes it:
  * the speaker writes into a ring buffer that a task feeds to TX, with
    silence when there is nothing to play -- so it also starts instantly;
  * the microphone task reads RX continuously, keeps one slot and decimates
    48 kHz -> 16 kHz for the wake word / voice assistant (duplex_dsp.h).

Both directions share the sample rate (48 kHz: the ES8311's FLAC answers stay
full quality). Replaces the board's i2s_audio bus, speaker and microphone.
"""

from esphome import pins
import esphome.codegen as cg
import esphome.config_validation as cv
from esphome.const import CONF_ID, CONF_SAMPLE_RATE

CODEOWNERS = []
DEPENDENCIES = ["esp32"]
AUTO_LOAD = ["audio", "ring_buffer"]
MULTI_CONF = False

CONF_DUPLEX_I2S_ID = "duplex_i2s_id"
CONF_MCLK_PIN = "mclk_pin"
CONF_BCLK_PIN = "bclk_pin"
CONF_LRCLK_PIN = "lrclk_pin"
CONF_DOUT_PIN = "dout_pin"
CONF_DIN_PIN = "din_pin"
CONF_PORT = "port"
CONF_SLOTS = "slots"
CONF_TDM_FORMAT = "tdm_format"
CONF_SLOT_WIDTH = "slot_width"
TDM_FORMATS = {"philips": 0, "msb": 1, "pcm_short": 2, "pcm_long": 3}

duplex_i2s_ns = cg.esphome_ns.namespace("duplex_i2s")
DuplexI2S = duplex_i2s_ns.class_("DuplexI2S", cg.Component)

CONFIG_SCHEMA = cv.Schema(
    {
        cv.GenerateID(): cv.declare_id(DuplexI2S),
        cv.Required(CONF_MCLK_PIN): pins.internal_gpio_output_pin_number,
        cv.Required(CONF_BCLK_PIN): pins.internal_gpio_output_pin_number,
        cv.Required(CONF_LRCLK_PIN): pins.internal_gpio_output_pin_number,
        cv.Required(CONF_DOUT_PIN): pins.internal_gpio_output_pin_number,
        cv.Required(CONF_DIN_PIN): pins.internal_gpio_input_pin_number,
        # 48 kHz only for now: the mic side decimates by exactly 3 to 16 kHz.
        cv.Optional(CONF_SAMPLE_RATE, default=48000): cv.one_of(48000, int=True),
        cv.Optional(CONF_PORT, default=0): cv.int_range(0, 1),
        # 2: standard stereo. 4: TDM (the ES7210's four channels on one line;
        # set its SDP register to TDM to match -- see the satellite yaml).
        cv.Optional(CONF_SLOTS, default=2): cv.one_of(2, 4, int=True),
        # TDM only. The ES7210 sends four channels only in its DSP/PCM modes,
        # packed in 16-bit slots right after the frame sync (measured).
        cv.Optional(CONF_TDM_FORMAT, default="pcm_short"): cv.one_of(*TDM_FORMATS, lower=True),
        cv.Optional(CONF_SLOT_WIDTH, default=16): cv.one_of(16, 32, int=True),
    }
).extend(cv.COMPONENT_SCHEMA)


async def to_code(config):
    var = cg.new_Pvariable(config[CONF_ID])
    await cg.register_component(var, config)
    cg.add(
        var.set_pins(
            config[CONF_MCLK_PIN],
            config[CONF_BCLK_PIN],
            config[CONF_LRCLK_PIN],
            config[CONF_DOUT_PIN],
            config[CONF_DIN_PIN],
        )
    )
    cg.add(var.set_sample_rate(config[CONF_SAMPLE_RATE]))
    cg.add(var.set_port(config[CONF_PORT]))
    cg.add(var.set_slots(config[CONF_SLOTS]))
    cg.add(var.set_tdm_format(TDM_FORMATS[config[CONF_TDM_FORMAT]]))
    cg.add(var.set_slot_width(config[CONF_SLOT_WIDTH]))
