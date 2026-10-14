import esphome.codegen as cg
from esphome.components import speaker
import esphome.config_validation as cv
from esphome.const import CONF_ID, CONF_OUTPUT

AUTO_LOAD = ["audio"]

CONF_PASSTHROUGH = "passthrough"

gate_speaker_ns = cg.esphome_ns.namespace("gate_speaker")
GateSpeaker = gate_speaker_ns.class_("GateSpeaker", cg.Component, speaker.Speaker)

CONFIG_SCHEMA = speaker.SPEAKER_SCHEMA.extend(
    {
        cv.GenerateID(): cv.declare_id(GateSpeaker),
        cv.Required(CONF_OUTPUT): cv.use_id(speaker.Speaker),
        # Until the device's mode says otherwise: silent.
        cv.Optional(CONF_PASSTHROUGH, default=False): cv.boolean,
    }
).extend(cv.COMPONENT_SCHEMA)


async def to_code(config):
    var = cg.new_Pvariable(config[CONF_ID])
    await cg.register_component(var, config)
    await speaker.register_speaker(var, config)
    out = await cg.get_variable(config[CONF_OUTPUT])
    cg.add(var.set_output(out))
    cg.add(var.set_passthrough(config[CONF_PASSTHROUGH]))
