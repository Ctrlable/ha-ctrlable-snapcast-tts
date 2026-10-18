import esphome.codegen as cg
from esphome.components import audio, esp32, microphone
import esphome.config_validation as cv
from esphome.const import CONF_ID

from . import CONF_DUPLEX_I2S_ID, DuplexI2S, duplex_i2s_ns

DEPENDENCIES = ["duplex_i2s"]

CONF_SLOT = "slot"
CONF_LEVEL_LOG = "level_log"
CONF_REFERENCE_SLOT = "reference_slot"
CONF_ECHO_CANCELLATION = "echo_cancellation"
CONF_SPEAKER = "speaker"
CONF_AEC_MODE = "aec_mode"
CONF_AEC_NLP = "aec_nlp"
CONF_FRONT_END = "front_end"
AEC_NLP = {"off": -1, "normal": 0, "aggressive": 1, "very_aggressive": 2}
AEC_MODES = {"sr_low_cost": 0, "sr_high_perf": 1, "voip_low_cost": 3, "voip_high_perf": 4,
             "fd_low_cost": 5, "fd_high_perf": 6}

DuplexMicrophone = duplex_i2s_ns.class_(
    "DuplexMicrophone", cg.Component, microphone.Microphone
)


def _limits(config):
    # What it hands out: 16 kHz mono 16-bit, whatever the bus runs at.
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
            cv.GenerateID(): cv.declare_id(DuplexMicrophone),
            cv.GenerateID(CONF_DUPLEX_I2S_ID): cv.use_id(DuplexI2S),
            # Which slot carries the mic: left/right in stereo, 0-3 in TDM.
            # The ES7210 puts MIC1 on the left slot and MIC2 on the right.
            cv.Optional(CONF_SLOT, default="right"): cv.Any(
                cv.one_of("left", "right", lower=True), cv.int_range(0, 3)
            ),
            # The slot carrying a copy of what the speaker plays (ES7210 MIC3 on
            # boards that loop the DAC back into it).
            # Or "speaker": the samples this component's speaker sent, which
            # needs no wiring at all.
            cv.Optional(CONF_REFERENCE_SLOT): cv.Any(cv.one_of(CONF_SPEAKER, lower=True), cv.int_range(0, 3)),
            # ESP-SR's AEC: removes the device's own playback from the mic
            # using the reference slot. Needs reference_slot.
            cv.Optional(CONF_ECHO_CANCELLATION, default=False): cv.boolean,
            # sr_low_cost is what n-IA-hane's audio stack suggests for wake words.
            cv.Optional(CONF_AEC_MODE, default="sr_low_cost"): cv.one_of(*AEC_MODES, lower=True),
            # Residual-echo suppression. "off" = linear filter only.
            cv.Optional(CONF_AEC_NLP, default="normal"): cv.one_of(*AEC_NLP, lower=True),
            # ESP-SR's full front end over every TDM slot (both mics + the
            # reference): one letter per slot, M mic / R reference / N unused.
            # Replaces echo_cancellation.
            cv.Optional(CONF_FRONT_END): cv.All(cv.string, cv.Length(min=3, max=4)),
            # Diagnostic: RMS / peak / clipping once a second in the log.
            cv.Optional(CONF_LEVEL_LOG, default=False): cv.boolean,
        }
    ).extend(cv.COMPONENT_SCHEMA),
    _limits,
)


async def to_code(config):
    var = cg.new_Pvariable(config[CONF_ID])
    await cg.register_component(var, config)
    await microphone.register_microphone(var, config)
    await cg.register_parented(var, config[CONF_DUPLEX_I2S_ID])
    slot = config[CONF_SLOT]
    cg.add(var.set_slot({"left": 0, "right": 1}.get(slot, slot)))
    ref = config.get(CONF_REFERENCE_SLOT)
    if ref == CONF_SPEAKER:
        cg.add(var.set_reference_from_speaker(True))
    elif ref is not None:
        cg.add(var.set_reference_slot(ref))
    if fe := config.get(CONF_FRONT_END):
        cg.add_define("USE_DUPLEX_I2S_AFE")
        cg.add(var.set_afe(fe.upper()))
        esp32.add_idf_component(name="espressif/esp-sr", ref="2.5.5")
    if config[CONF_ECHO_CANCELLATION]:
        if CONF_REFERENCE_SLOT not in config:
            raise cv.Invalid("echo_cancellation needs reference_slot")
        cg.add_define("USE_DUPLEX_I2S_AEC")
        cg.add(var.set_aec_mode(AEC_MODES[config[CONF_AEC_MODE]]))
        cg.add(var.set_aec_nlp(AEC_NLP[config[CONF_AEC_NLP]]))
        esp32.add_idf_component(name="espressif/esp-sr", ref="2.5.5")
    cg.add(var.set_level_log(config[CONF_LEVEL_LOG]))
