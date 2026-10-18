import esphome.codegen as cg
from esphome.components import audio, speaker
import esphome.config_validation as cv
from esphome.const import CONF_BUFFER_DURATION, CONF_ID

from . import CONF_DUPLEX_I2S_ID, DuplexI2S, duplex_i2s_ns

DEPENDENCIES = ["duplex_i2s"]

DuplexSpeaker = duplex_i2s_ns.class_("DuplexSpeaker", cg.Component, speaker.Speaker)


def _limits(config):
    audio.set_stream_limits(
        min_bits_per_sample=16,
        max_bits_per_sample=16,
        min_channels=1,
        max_channels=2,
        min_sample_rate=48000,
        max_sample_rate=48000,
    )(config)
    return config


CONFIG_SCHEMA = cv.All(
    speaker.SPEAKER_SCHEMA.extend(
        {
            cv.GenerateID(): cv.declare_id(DuplexSpeaker),
            cv.GenerateID(CONF_DUPLEX_I2S_ID): cv.use_id(DuplexI2S),
            cv.Optional(
                CONF_BUFFER_DURATION, default="500ms"
            ): cv.positive_time_period_milliseconds,
        }
    ).extend(cv.COMPONENT_SCHEMA),
    _limits,
)


async def to_code(config):
    var = cg.new_Pvariable(config[CONF_ID])
    await cg.register_component(var, config)
    await speaker.register_speaker(var, config)
    await cg.register_parented(var, config[CONF_DUPLEX_I2S_ID])
    cg.add(var.set_buffer_duration(config[CONF_BUFFER_DURATION]))
