"""Ctrlable satellite screen faces: Orb, Halo, Dot & Bar.

    ctrlable_faces:
      id: faces
      display_id: lcd          # redrawn by this component while a face moves
      round: true              # 360x360 round panel; false for 320x240
      logo_left: false         # rectangular screens: mark bottom-left
      font: face_label_font    # optional, for the four problem-state labels

The display's own lambda draws with `id(faces).draw(it);`. Steps and style are
set from YAML lambdas: `id(faces).set_step(3)`, `id(faces).set_style(1)`.
"""
import esphome.codegen as cg
import esphome.config_validation as cv
from esphome.components import display, font
from esphome.const import CONF_ID

CODEOWNERS = ["@Ctrlable"]
DEPENDENCIES = ["display"]

ctrlable_faces_ns = cg.esphome_ns.namespace("ctrlable_faces")
CtrlableFaces = ctrlable_faces_ns.class_("CtrlableFaces", cg.Component)

CONF_DISPLAY_ID = "display_id"
CONF_ROUND = "round"
CONF_LOGO_LEFT = "logo_left"
CONF_FONT = "font"

CONFIG_SCHEMA = cv.Schema(
    {
        cv.GenerateID(): cv.declare_id(CtrlableFaces),
        cv.Required(CONF_DISPLAY_ID): cv.use_id(display.Display),
        cv.Optional(CONF_ROUND, default=False): cv.boolean,
        cv.Optional(CONF_LOGO_LEFT, default=False): cv.boolean,
        # 2 = half the frame rate; 0 = static, redrawn only when the face
        # changes (see CtrlableFaces::set_frame_scale).
        cv.Optional("frame_scale", default=1): cv.int_range(0, 8),
        cv.Optional(CONF_FONT): cv.use_id(font.Font),
    }
).extend(cv.COMPONENT_SCHEMA)


async def to_code(config):
    var = cg.new_Pvariable(config[CONF_ID])
    await cg.register_component(var, config)
    cg.add(var.set_display(await cg.get_variable(config[CONF_DISPLAY_ID])))
    cg.add(var.set_round(config[CONF_ROUND]))
    cg.add(var.set_logo_left(config[CONF_LOGO_LEFT]))
    cg.add(var.set_frame_scale(config["frame_scale"]))
    if CONF_FONT in config:
        cg.add(var.set_font(await cg.get_variable(config[CONF_FONT])))
