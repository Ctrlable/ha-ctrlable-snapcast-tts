"""rtp_mic: stream one channel of a microphone as RTP L16/16000/mono.

Why this exists: a Wi-Fi satellite has no cable back to the server, so the
server's echo canceller -- the only one that knows what every room is playing,
Snapcast answers included -- never hears this mic. Sending the beam to PipeWire
over RTP fixes that without changing how the device itself answers.

The device does not know the server. Where to send is a runtime setting
(`set_destination("10.1.8.174:46000")`), driven by the hardware manager
through ordinary ESPHome entities, so no IP is ever compiled in.

It listens alongside micro_wake_word and voice_assistant through its own
MicrophoneSource; the I2S driver counts listeners, so starting or stopping the
stream never starts or stops the mic under them.
"""

import esphome.codegen as cg
from esphome.components import microphone
import esphome.config_validation as cv
from esphome.const import CONF_CHANNEL, CONF_ID, CONF_MICROPHONE

DEPENDENCIES = ["microphone", "network"]
CODEOWNERS = []

CONF_DESTINATION = "destination"
CONF_PAYLOAD_TYPE = "payload_type"
CONF_PACKET_MS = "packet_ms"

rtp_mic_ns = cg.esphome_ns.namespace("rtp_mic")
RtpMic = rtp_mic_ns.class_("RtpMic", cg.Component)


def _validate_destination(value):
    value = cv.string_strict(value)
    if value == "":
        return value
    host, sep, port = value.rpartition(":")
    if not sep or not host:
        raise cv.Invalid("destination must be host:port, e.g. 10.1.8.174:46000")
    cv.port(int(port))
    return value


CONFIG_SCHEMA = cv.Schema(
    {
        cv.GenerateID(): cv.declare_id(RtpMic),
        # Both XVF3800 output slots are taken in, so the beam can be switched at
        # runtime without a reflash; 32-bit keeps the XVF's own scaling intact.
        cv.Optional(CONF_MICROPHONE, default={}): microphone.microphone_source_schema(
            min_bits_per_sample=32,
            max_bits_per_sample=32,
            min_channels=1,
            max_channels=2,
        ),
        # Index into the source's channels list above, not the raw I2S slot.
        cv.Optional(CONF_CHANNEL, default=0): cv.int_range(0, 1),
        cv.Optional(CONF_DESTINATION, default=""): _validate_destination,
        cv.Optional(CONF_PAYLOAD_TYPE, default=96): cv.int_range(96, 127),
        # 20 ms is the RTP audio norm and what PipeWire's receiver expects to
        # size its jitter buffer around; shorter wastes Wi-Fi airtime on headers.
        cv.Optional(CONF_PACKET_MS, default=20): cv.int_range(5, 40),
    }
).extend(cv.COMPONENT_SCHEMA)


FINAL_VALIDATE_SCHEMA = cv.Schema(
    {
        cv.Required(CONF_MICROPHONE): microphone.final_validate_microphone_source_schema(
            "rtp_mic"
        ),
    },
    extra=cv.ALLOW_EXTRA,
)


async def to_code(config):
    var = cg.new_Pvariable(config[CONF_ID])
    await cg.register_component(var, config)
    mic_source = await microphone.microphone_source_to_code(config[CONF_MICROPHONE])
    cg.add(var.set_microphone_source(mic_source))
    cg.add(var.set_channel(config[CONF_CHANNEL]))
    cg.add(var.set_payload_type(config[CONF_PAYLOAD_TYPE]))
    cg.add(var.set_packet_ms(config[CONF_PACKET_MS]))
    if config[CONF_DESTINATION]:
        cg.add(var.set_destination(config[CONF_DESTINATION]))
