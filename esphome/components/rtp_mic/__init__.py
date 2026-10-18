"""rtp_mic: stream one channel of a microphone as RTP L16/16000/mono.

Why this exists: a Wi-Fi satellite has no cable back to the server, so the
server's echo canceller -- the only one that knows what every room is playing,
Snapcast answers included -- never hears this mic. Sending the beam to PipeWire
over RTP fixes that without changing how the device itself answers.

The device does not know the server. It runs a small control service
(tcp/6055, see rtp_session.h): the hardware manager connects, asks for the
stream, and the RTP goes back to the address the request came from for as
long as that connection stays open. No IP is compiled in or typed in.

It listens alongside micro_wake_word and voice_assistant through its own
MicrophoneSource; the I2S driver counts listeners, so starting or stopping the
stream never starts or stops the mic under them.

`reference:` (optional) takes the room's mix BACK from the server and plays it
into the XVF3800's echo-reference input through one more mixer source, with
the DAC muted, so the XVF's own canceller can remove the Snapcast audio. It is
only opened while a server offers it AND the device's answers play in the room;
in local mode the XVF hears its own output and nothing runs.
"""

from esphome import automation
import esphome.codegen as cg
from esphome.components import microphone
import esphome.config_validation as cv
from esphome.const import CONF_ID, CONF_MICROPHONE

DEPENDENCIES = ["microphone", "network"]
CODEOWNERS = []

CONF_CONTROL_PORT = "control_port"
CONF_TOKEN = "token"
CONF_PAYLOAD_TYPE = "payload_type"
CONF_PACKET_MS = "packet_ms"
CONF_REFERENCE = "reference"
CONF_SOURCE_SPEAKER = "source_speaker"
CONF_MIXER = "mixer"
CONF_PORT = "port"
CONF_QUEUE_MS = "queue_ms"
CONF_ON_LED = "on_led"

rtp_mic_ns = cg.esphome_ns.namespace("rtp_mic")
RtpMic = rtp_mic_ns.class_("RtpMic", cg.Component)
mixer_speaker_ns = cg.esphome_ns.namespace("mixer_speaker")
MixerSpeaker = mixer_speaker_ns.class_("MixerSpeaker", cg.Component)
Speaker = cg.esphome_ns.namespace("speaker").class_("Speaker")

REFERENCE_SCHEMA = cv.Schema(
    {
        # A source speaker of `mixer`, used for nothing else. Its audio reaches
        # the XVF on I2S at full scale; what the room hears is the DAC, which
        # this device keeps muted while answers go to Snapcast.
        cv.Required(CONF_SOURCE_SPEAKER): cv.use_id(Speaker),
        cv.Required(CONF_MIXER): cv.use_id(MixerSpeaker),
        cv.Optional(CONF_PORT, default=6056): cv.port,
        # Output pipeline depth the reference is kept topped up to. Lower is
        # less delay; too low and an I2S underrun inserts silence, which moves
        # the alignment until the server's next re-anchor.
        cv.Optional(CONF_QUEUE_MS, default=20): cv.int_range(10, 80),
    }
)


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
        # Where the manager connects to ask for the stream. 6053 is the native
        # API and 6054 is commonly taken by voice satellites, hence 6055.
        cv.Optional(CONF_CONTROL_PORT, default=6055): cv.port,
        # Empty = any host on the LAN may ask (see the package for the tradeoff).
        cv.Optional(CONF_TOKEN, default=""): cv.All(cv.string_strict, cv.Length(max=64)),
        cv.Optional(CONF_PAYLOAD_TYPE, default=96): cv.int_range(96, 127),
        # 20 ms is the RTP audio norm and what PipeWire's receiver expects to
        # size its jitter buffer around; shorter wastes Wi-Fi airtime on headers.
        cv.Optional(CONF_PACKET_MS, default=20): cv.int_range(5, 40),
        cv.Optional(CONF_REFERENCE): REFERENCE_SCHEMA,
        # Mic-only mode: the server drives the LED ring. `x` is the state word.
        cv.Optional(CONF_ON_LED): automation.validate_automation(single=True),
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
    cg.add(var.set_payload_type(config[CONF_PAYLOAD_TYPE]))
    cg.add(var.set_packet_ms(config[CONF_PACKET_MS]))
    cg.add(var.set_control_port(config[CONF_CONTROL_PORT]))
    if config[CONF_TOKEN]:
        cg.add(var.set_token(config[CONF_TOKEN]))
    if conf := config.get(CONF_ON_LED):
        await automation.build_automation(var.get_led_trigger(), [(cg.std_string, "x")], conf)
    if ref := config.get(CONF_REFERENCE):
        cg.add_define("USE_RTP_MIC_REFERENCE")
        source = await cg.get_variable(ref[CONF_SOURCE_SPEAKER])
        mixer = await cg.get_variable(ref[CONF_MIXER])
        cg.add(var.set_reference(source, mixer, ref[CONF_PORT], ref[CONF_QUEUE_MS]))
