import esphome.codegen as cg
from esphome.components import audio, esp32, microphone
import esphome.config_validation as cv
from esphome.const import CONF_ID

CODEOWNERS = []
DEPENDENCIES = ["rtp_mic"]
AUTO_LOAD = ["ring_buffer"]

CONF_SOURCE = "source"
CONF_REFERENCE_PORT = "reference_port"
CONF_REFERENCE_FROM = "reference_from"
CONF_REFERENCE_DELAY = "reference_delay"
CONF_FILTER_LENGTH = "filter_length"
CONF_AEC_MODE = "aec_mode"
AEC_MODES = {"sr_low_cost": 0, "sr_high_perf": 1, "fd_low_cost": 5, "fd_high_perf": 6}

aec_mic_ns = cg.esphome_ns.namespace("aec_mic")
AecMic = aec_mic_ns.class_("AecMic", cg.Component, microphone.Microphone)


def _limits(config):
    audio.set_stream_limits(
        min_bits_per_sample=16,
        max_bits_per_sample=16,
        min_channels=1,
        max_channels=1,
        min_sample_rate=16000,
        max_sample_rate=16000,
    )(config)
    return config


CONFIG_SCHEMA = cv.All(
    microphone.MICROPHONE_SCHEMA.extend(
        {
            cv.GenerateID(): cv.declare_id(AecMic),
            # 16 kHz, mono, 16-bit -- what the source mic must deliver.
            cv.Required(CONF_SOURCE): cv.use_id(microphone.Microphone),
            cv.Optional(CONF_REFERENCE_PORT, default=6057): cv.port,
            # Only this host may feed the canceller (the audio server).
            cv.Optional(CONF_REFERENCE_FROM): cv.ipv4address,
            # How long a reference sample is held after it arrives, so it meets
            # its own echo at the mic. Tunable at run time (set_delay_ms).
            cv.Optional(
                CONF_REFERENCE_DELAY, default="100ms"
            ): cv.positive_time_period_milliseconds,
            # ESP-SR filter length: tail it can cancel, and the slack it leaves
            # for the delay being off. 4 is Espressif's default; 8 doubles both.
            cv.Optional(CONF_FILTER_LENGTH, default=8): cv.int_range(1, 16),
            cv.Optional(CONF_AEC_MODE, default="sr_high_perf"): cv.one_of(*AEC_MODES, lower=True),
        }
    ).extend(cv.COMPONENT_SCHEMA),
    _limits,
)


async def to_code(config):
    var = cg.new_Pvariable(config[CONF_ID])
    await cg.register_component(var, config)
    await microphone.register_microphone(var, config)
    src = await cg.get_variable(config[CONF_SOURCE])
    cg.add(var.set_source(src))
    cg.add(var.set_reference_port(config[CONF_REFERENCE_PORT]))
    if CONF_REFERENCE_FROM in config:
        cg.add(var.set_reference_from(str(config[CONF_REFERENCE_FROM])))
    cg.add(var.set_delay_ms(config[CONF_REFERENCE_DELAY].total_milliseconds))
    cg.add(var.set_filter_length(config[CONF_FILTER_LENGTH]))
    cg.add(var.set_aec_mode(AEC_MODES[config[CONF_AEC_MODE]]))
    esp32.add_idf_component(name="espressif/esp-sr", ref="2.5.5")
