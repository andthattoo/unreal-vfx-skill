"""Unreal-side VFX job runner. Runs INSIDE the editor's Python (never standalone).

Launch through scripts/ue_run.sh, which sets VFX_JOB to a job JSON file, enables the
NiagaraToolsets + ToolsetRegistry plugins for that run only, and starts a headless full editor
(-ExecutePythonScript). A plain commandlet (-run=pythonscript) also runs jobs but cannot check
systems: the stack-issue tool needs Slate and asserts without it.

A job is one JSON object; every key is optional and they run in this order:

  "textures":  [ {"src": "C:/abs/file.png", "dest": "/Game/VFX/Textures", "name": "T_VFX_Smoke",
                  "kind": "sprite" | "tile"} ]
  "materials": { "dest": "/Game/VFX/Materials", "rebuild_masters": false,
                 "instances": [ {"name": "MI_VFX_Smoke", "master": "sprite_alpha", "params": {...}} ] }
  "systems":   [ <system recipe>, ... ]            see references/niagara-scripting.md
  "dump":      [ {"system": "/Game/VFX/Systems/NS_X", "out": "C:/abs/out.json"} ]
  "modules":   "C:/abs/modules.txt"                 list every library module / dynamic input
  "schema":    [ {"asset": "/Niagara/Modules/...", "out": "C:/abs/schema.json"} ]
  "check":     [ "/Game/VFX/Systems/NS_X" ]         compile state + stack issues (built systems are
                                                    checked automatically)

The result is written next to the job file as <job>.result.json: {"ok", "errors", "warnings", "done"}.
"""
import json
import os
import sys
import traceback

import unreal

SYS = "NiagaraToolsets.NiagaraToolset_System"
INFO = "NiagaraToolsets.NiagaraToolset_Info"
EMITTER_TEMPLATES = "/Niagara/DefaultAssets/Templates/Emitters/"
SYSTEM_TEMPLATE = "/Niagara/DefaultAssets/DefaultSystem.DefaultSystem"

REPORT = {"ok": True, "errors": [], "warnings": [], "done": []}


_TRACE = None


def trace(msg):
    """Append to <job>.trace.txt. A hard editor crash leaves no result file; the last line here names the call."""
    if _TRACE:
        _TRACE.write(msg + "\n")
        _TRACE.flush()


def err(msg):
    REPORT["ok"] = False
    REPORT["errors"].append(msg)
    unreal.log_error("[vfx] " + msg)


def warn(msg):
    REPORT["warnings"].append(msg)
    unreal.log_warning("[vfx] " + msg)


def done(msg):
    REPORT["done"].append(msg)
    unreal.log("[vfx] " + msg)


JOB_DIR = ""


def local(path):
    """Disk paths in a job may be relative to the job file's folder."""
    if os.path.isabs(path):
        return path
    return os.path.normpath(os.path.join(JOB_DIR, path)).replace(os.sep, "/")


def obj_path(path):
    """'/Game/A/B' -> '/Game/A/B.B'. Paths that already name the object pass through."""
    path = path.strip()
    leaf = path.rsplit("/", 1)[-1]
    return path if "." in leaf else path + "." + leaf


def pkg_path(path):
    return path.split(".")[0]


# --------------------------------------------------------------------------- tool calls

def tool(name, payload, toolset=SYS, what=""):
    """Call one ToolsetRegistry tool. Returns its returnValue, or None after recording an error."""
    if not hasattr(unreal, "ToolsetRegistry"):
        raise RuntimeError("ToolsetRegistry is not loaded: launch with "
                           "-EnablePlugins=NiagaraToolsets,ToolsetRegistry (ue_run.sh does this)")
    trace("%s %s" % (name, what))
    res = unreal.ToolsetRegistry.execute_tool(toolset, name, json.dumps(payload))
    if res.error:
        err("%s %s: %s" % (name, what, res.error))
        return None
    if not res.is_complete:
        err("%s %s: did not complete (async tools need a ticking editor, not a commandlet)" % (name, what))
        return None
    raw = res.get_value_as_json_string()
    if not raw:
        return {}
    val = json.loads(raw)
    if isinstance(val, str):          # results arrive JSON-encoded twice
        val = json.loads(val) if val else {}
    if isinstance(val, dict) and "returnValue" in val:
        return val["returnValue"]
    return val if val is not None else {}


def ref(system, emitter="", script="", module="", renderer=-1, inputs=None):
    return {"system": {"refPath": system}, "emitterName": emitter, "scriptName": script,
            "moduleName": module, "rendererIndex": renderer, "inputNameStack": inputs or []}


# --------------------------------------------------------------------------- textures

def import_textures(items):
    tools = unreal.AssetToolsHelpers.get_asset_tools()
    for it in items:
        src = local(it["src"])
        if not os.path.isfile(src):
            err("texture source not found: " + src)
            continue
        dest = it.get("dest", "/Game/VFX/Textures")
        name = it.get("name") or "T_VFX_" + os.path.splitext(os.path.basename(src))[0]
        task = unreal.AssetImportTask()
        task.set_editor_property("filename", src)
        task.set_editor_property("destination_path", dest)
        task.set_editor_property("destination_name", name)
        task.set_editor_property("automated", True)
        task.set_editor_property("replace_existing", True)
        task.set_editor_property("save", False)
        tools.import_asset_tasks([task])
        tex = unreal.load_asset(dest + "/" + name)
        if not tex:
            err("texture import failed: " + src)
            continue
        tile = it.get("kind", "sprite") == "tile"
        addr = unreal.TextureAddress.TA_WRAP if tile else unreal.TextureAddress.TA_CLAMP
        # Everything stays TC_Default + sRGB so any texture can be swapped into any material
        # instance: a grayscale/linear texture in a Color sampler is a material compile error.
        tex.set_editor_property("compression_settings", unreal.TextureCompressionSettings.TC_DEFAULT)
        tex.set_editor_property("srgb", True)
        tex.set_editor_property("lod_group", unreal.TextureGroup.TEXTUREGROUP_EFFECTS)
        tex.set_editor_property("address_x", addr)
        tex.set_editor_property("address_y", addr)
        unreal.EditorAssetLibrary.save_asset(dest + "/" + name, only_if_is_dirty=False)
        done("texture %s/%s (%s)" % (dest, name, "tile" if tile else "sprite"))


# --------------------------------------------------------------------------- materials

MASTERS = {
    "sprite_add": ("M_VFX_Sprite_Add", "sprite", True),
    "sprite_alpha": ("M_VFX_Sprite_Alpha", "sprite", False),
    "panner_add": ("M_VFX_Panner_Add", "panner", True),
    "panner_alpha": ("M_VFX_Panner_Alpha", "panner", False),
}


class Graph:
    """Small helper around MaterialEditingLibrary so the graphs below read top to bottom."""

    def __init__(self, material):
        self.m = material
        self.mel = unreal.MaterialEditingLibrary
        self.x = -1600
        self.y = 0

    def node(self, cls, **props):
        self.y += 140
        if self.y > 900:
            self.y = 0
            self.x += 260
        n = self.mel.create_material_expression(self.m, cls, self.x, self.y)
        for k, v in props.items():
            n.set_editor_property(k, v)
        return n

    def link(self, a, out, b, inp):
        if not self.mel.connect_material_expressions(a, out, b, inp):
            err("material %s: could not connect %s.%s -> %s.%s" %
                (self.m.get_name(), a.get_name(), out, b.get_name(), inp))

    def scalar(self, name, default, group="VFX"):
        return self.node(unreal.MaterialExpressionScalarParameter, parameter_name=name,
                         default_value=default, group=group)

    def op(self, cls, a, b, a_out="", b_out=""):
        n = self.node(cls)
        self.link(a, a_out, n, "A")
        self.link(b, b_out, n, "B")
        return n

    def mul(self, a, b, a_out="", b_out=""):
        return self.op(unreal.MaterialExpressionMultiply, a, b, a_out, b_out)

    def out(self, node, prop, out=""):
        if not self.mel.connect_material_property(node, out, prop):
            err("material %s: could not connect output %s" % (self.m.get_name(), prop))


def _erode_and_finish(g, value, value_out, rgb, additive):
    """Shared tail of both masters: erosion, particle alpha, soft particles, outputs.

    value: node whose output `value_out` is the 0..1 shape (texture alpha or mask).
    rgb:   node carrying the tinted colour.
    """
    dyn = g.node(unreal.MaterialExpressionDynamicParameter,
                 param_names=["Erode", "Boost", "Spare1", "Spare2"],
                 default_value=unreal.LinearColor(0.0, 1.0, 0.0, 0.0))
    pc = g.node(unreal.MaterialExpressionParticleColor)
    soft = g.scalar("ErodeSoftness", 0.2)
    # alpha = value * saturate((value - Erode) / Softness) * particle alpha
    sub = g.node(unreal.MaterialExpressionSubtract)
    g.link(value, value_out, sub, "A")
    g.link(dyn, "R", sub, "B")      # Erode = dynamic parameter index 0, x
    div = g.node(unreal.MaterialExpressionDivide)
    g.link(sub, "", div, "A")
    g.link(soft, "", div, "B")
    sat = g.node(unreal.MaterialExpressionSaturate)
    g.link(div, "", sat, "")
    alpha = g.mul(value, sat, value_out, "")
    alpha = g.mul(alpha, pc, "", "A")
    # Soft particles: fade where the quad cuts into opaque geometry.
    fade = g.node(unreal.MaterialExpressionDepthFade)
    g.link(alpha, "", fade, "Opacity")
    g.link(g.scalar("SoftDistance", 40.0), "", fade, "FadeDistance")
    sw = g.node(unreal.MaterialExpressionStaticSwitchParameter, parameter_name="SoftParticles",
                default_value=True, group="VFX")
    g.link(fade, "", sw, "True")
    g.link(alpha, "", sw, "False")
    # colour = rgb * particle colour * EmissiveBoost * per-particle Boost
    col = g.mul(rgb, pc, "", "")
    col = g.mul(col, g.scalar("EmissiveBoost", 1.0), "", "")
    col = g.mul(col, dyn, "", "G")      # Boost = dynamic parameter index 0, y
    if additive:
        col = g.mul(col, sw, "", "")     # additive ignores opacity, so bake alpha into colour
    g.out(col, unreal.MaterialProperty.MP_EMISSIVE_COLOR)
    g.out(sw, unreal.MaterialProperty.MP_OPACITY)


def _build_sprite(g, additive):
    # SubUV sampler: frame comes from the particle's SubImageIndex, blended when the renderer
    # has bSubImageBlend on. With a 1x1 grid it is a plain texture sample.
    tex = g.node(unreal.MaterialExpressionTextureSampleParameterSubUV, parameter_name="Texture",
                 blend=True, group="VFX")
    _erode_and_finish(g, tex, "A", tex, additive)


def _build_panner(g, additive):
    def layer(prefix, tex_param, tile, speed):
        uv = g.node(unreal.MaterialExpressionTextureCoordinate)
        tiling = g.node(unreal.MaterialExpressionAppendVector)
        g.link(g.scalar(prefix + "TileU", tile[0]), "", tiling, "A")
        g.link(g.scalar(prefix + "TileV", tile[1]), "", tiling, "B")
        scaled = g.mul(uv, tiling)
        spd = g.node(unreal.MaterialExpressionAppendVector)
        g.link(g.scalar(prefix + "SpeedU", speed[0]), "", spd, "A")
        g.link(g.scalar(prefix + "SpeedV", speed[1]), "", spd, "B")
        pan = g.node(unreal.MaterialExpressionPanner)
        g.link(scaled, "", pan, "Coordinate")
        g.link(spd, "", pan, "Speed")
        t = g.node(unreal.MaterialExpressionTextureSampleParameter2D, parameter_name=tex_param, group="VFX")
        g.link(pan, "", t, "UVs")
        return t

    main = layer("Main", "Texture", (1.0, 1.0), (0.0, -0.5))
    noise = layer("Noise", "Noise", (2.0, 1.0), (0.1, -0.9))
    mask = g.node(unreal.MaterialExpressionTextureSampleParameter2D, parameter_name="Mask", group="VFX")
    white = unreal.load_asset("/Engine/EngineResources/WhiteSquareTexture")
    if white:
        mask.set_editor_property("texture", white)
        noise.set_editor_property("texture", white)
    # main value: red channel by default, alpha when TextureUseAlpha = 1
    pick = g.node(unreal.MaterialExpressionLinearInterpolate)
    g.link(main, "R", pick, "A")
    g.link(main, "A", pick, "B")
    g.link(g.scalar("TextureUseAlpha", 0.0), "", pick, "Alpha")
    # noise modulation: lerp(1, noise * 2, NoiseAmount)
    one = g.node(unreal.MaterialExpressionConstant, r=1.0)
    two = g.node(unreal.MaterialExpressionConstant, r=2.0)
    n2 = g.mul(noise, two, "R", "")
    mod = g.node(unreal.MaterialExpressionLinearInterpolate)
    g.link(one, "", mod, "A")
    g.link(n2, "", mod, "B")
    g.link(g.scalar("NoiseAmount", 1.0), "", mod, "Alpha")
    value = g.mul(pick, mod)
    value = g.mul(value, mask, "", "R")
    value = _saturate(g, value)
    white_rgb = g.node(unreal.MaterialExpressionConstant3Vector, constant=unreal.LinearColor(1, 1, 1, 1))
    _erode_and_finish(g, value, "", white_rgb, additive)


def _saturate(g, node):
    s = g.node(unreal.MaterialExpressionSaturate)
    g.link(node, "", s, "")
    return s


def ensure_masters(dest, rebuild=False):
    tools = unreal.AssetToolsHelpers.get_asset_tools()
    eal = unreal.EditorAssetLibrary
    for key, (name, kind, additive) in MASTERS.items():
        path = dest + "/" + name
        if eal.does_asset_exist(path):
            if not rebuild:
                continue
            mat = unreal.load_asset(path)
            unreal.MaterialEditingLibrary.delete_all_material_expressions(mat)
        else:
            mat = tools.create_asset(name, dest, unreal.Material, unreal.MaterialFactoryNew())
        if not mat:
            err("could not create master material " + path)
            continue
        mat.set_editor_property("blend_mode", unreal.BlendMode.BLEND_ADDITIVE if additive
                                else unreal.BlendMode.BLEND_TRANSLUCENT)
        mat.set_editor_property("shading_model", unreal.MaterialShadingModel.MSM_UNLIT)
        mat.set_editor_property("two_sided", True)
        for flag in ("used_with_niagara_sprites", "used_with_niagara_mesh_particles",
                     "used_with_niagara_ribbons"):
            mat.set_editor_property(flag, True)
        g = Graph(mat)
        (_build_sprite if kind == "sprite" else _build_panner)(g, additive)
        unreal.MaterialEditingLibrary.layout_material_expressions(mat)
        unreal.MaterialEditingLibrary.recompile_material(mat)
        eal.save_asset(path, only_if_is_dirty=False)
        done("master material " + path)


def make_instances(dest, instances):
    tools = unreal.AssetToolsHelpers.get_asset_tools()
    mel = unreal.MaterialEditingLibrary
    eal = unreal.EditorAssetLibrary
    for it in instances:
        name = it["name"]
        idest = it.get("dest", dest)
        master = it.get("master", "sprite_alpha")
        parent_path = (dest + "/" + MASTERS[master][0]) if master in MASTERS else master
        parent = unreal.load_asset(parent_path)
        if not parent:
            err("instance %s: parent material not found: %s" % (name, parent_path))
            continue
        path = idest + "/" + name
        mi = unreal.load_asset(path) if eal.does_asset_exist(path) else \
            tools.create_asset(name, idest, unreal.MaterialInstanceConstant,
                               unreal.MaterialInstanceConstantFactoryNew())
        if not mi:
            err("could not create material instance " + path)
            continue
        mel.set_material_instance_parent(mi, parent)
        # The setters return False even when they work, so check names up front instead.
        known = {"bool": [str(n) for n in mel.get_static_switch_parameter_names(parent)],
                 "scalar": [str(n) for n in mel.get_scalar_parameter_names(parent)],
                 "texture": [str(n) for n in mel.get_texture_parameter_names(parent)],
                 "vector": [str(n) for n in mel.get_vector_parameter_names(parent)]}
        for pname, val in (it.get("params") or {}).items():
            kind = ("bool" if isinstance(val, bool) else "scalar" if isinstance(val, (int, float))
                    else "texture" if isinstance(val, str) else "vector")
            if pname not in known[kind]:
                err("instance %s: parent has no %s parameter %r. It has: %s"
                    % (name, kind, pname, ", ".join(known[kind]) or "none"))
                continue
            if kind == "bool":
                mel.set_material_instance_static_switch_parameter_value(mi, pname, val)
            elif kind == "scalar":
                mel.set_material_instance_scalar_parameter_value(mi, pname, float(val))
            elif kind == "texture":
                tex = unreal.load_asset(val)
                if not tex:
                    err("instance %s: texture not found: %s" % (name, val))
                    continue
                mel.set_material_instance_texture_parameter_value(mi, pname, tex)
            else:
                c = list(val) + [1.0] * (4 - len(val))
                mel.set_material_instance_vector_parameter_value(mi, pname, unreal.LinearColor(*c[:4]))
        mel.update_material_instance(mi)
        eal.save_asset(path, only_if_is_dirty=False)
        done("material instance " + path)


# --------------------------------------------------------------------------- niagara values

STRUCTS = {
    "float": "/Script/Niagara.NiagaraFloat", "int": "/Script/Niagara.NiagaraInt32",
    "bool": "/Script/Niagara.NiagaraBool", "position": "/Script/Niagara.NiagaraPosition",
    "vec2": "/Script/CoreUObject.Vector2f", "vec3": "/Script/CoreUObject.Vector3f",
    "vec4": "/Script/CoreUObject.Vector4f", "color": "/Script/CoreUObject.LinearColor",
    "quat": "/Script/CoreUObject.Quat4f",
}
# User variables use the double-precision core structs.
USER_STRUCTS = dict(STRUCTS, vec2="/Script/CoreUObject.Vector2D", vec3="/Script/CoreUObject.Vector",
                    vec4="/Script/CoreUObject.Vector4", quat="/Script/CoreUObject.Quat")
E = "/Script/NiagaraEditor.NiagaraExt_StackInputData_"
# key -> (dynamic input asset, curve property names inside its curve data interface)
CURVES = {
    "curve": ("/Niagara/DynamicInputs/ValueFromCurve/FloatFromCurve.FloatFromCurve", ["Curve"]),
    "curve2": ("/Niagara/DynamicInputs/ValueFromCurve/Vector2DFromCurve.Vector2DFromCurve", ["XCurve", "YCurve"]),
    "curve3": ("/Niagara/DynamicInputs/ValueFromCurve/VectorFromCurve.VectorFromCurve",
               ["XCurve", "YCurve", "ZCurve"]),
    "color_curve": ("/Niagara/DynamicInputs/ValueFromCurve/ColorFromCurve.ColorFromCurve",
                    ["RedCurve", "GreenCurve", "BlueCurve", "AlphaCurve"]),
}
RANGES = {
    "NiagaraFloat": "/Niagara/DynamicInputs/UniformRange/V2/RandomRangeFloat.RandomRangeFloat",
    "NiagaraInt32": "/Niagara/DynamicInputs/UniformRange/V2/RandomRangeInt.RandomRangeInt",
    "Vector2f": "/Niagara/DynamicInputs/UniformRange/V2/RandomRangeVector2D.RandomRangeVector2D",
    "Vector3f": "/Niagara/DynamicInputs/UniformRange/V2/RandomRangeVector.RandomRangeVector",
    "Vector4f": "/Niagara/DynamicInputs/UniformRange/V2/RandomRangeVector4.RandomRangeVector4",
    "LinearColor": "/Niagara/DynamicInputs/UniformRange/V3/RandomRangeLinearColor.RandomRangeLinearColor",
}


def inst(struct, value):
    return {"struct": {"refPath": struct}, "value": value}


def kind_of(type_ref):
    """Normalise a type refPath to one of the STRUCTS keys, 'enum', or None."""
    if not type_ref:
        return None
    leaf = type_ref.rsplit(".", 1)[-1]
    table = {"NiagaraFloat": "float", "NiagaraInt32": "int", "NiagaraBool": "bool",
             "NiagaraPosition": "position", "Vector2f": "vec2", "Vector2D": "vec2",
             "Vector3f": "vec3", "Vector": "vec3", "Vector4f": "vec4", "Vector4": "vec4",
             "LinearColor": "color", "Quat4f": "quat", "Quat": "quat"}
    if leaf in table:
        return table[leaf]
    if type_ref.startswith("/Script/"):
        return None
    return "enum"


def plain(kind, v, structs=STRUCTS):
    """Encode a plain Python value as the given kind."""
    if kind == "float":
        return inst(structs["float"], {"value": float(v)})
    if kind == "int":
        return inst(structs["int"], {"value": int(v)})
    if kind == "bool":
        return inst(structs["bool"], {"value": 1 if v else 0})
    seq = list(v.values()) if isinstance(v, dict) else list(v)
    if kind == "vec2":
        return inst(structs["vec2"], {"x": seq[0], "y": seq[1]})
    if kind in ("vec3", "position"):
        return inst(structs[kind], {"x": seq[0], "y": seq[1], "z": seq[2]})
    if kind == "vec4":
        return inst(structs["vec4"], {"x": seq[0], "y": seq[1], "z": seq[2], "w": seq[3]})
    if kind == "quat":
        return inst(structs["quat"], {"x": seq[0], "y": seq[1], "z": seq[2], "w": seq[3]})
    if kind == "color":
        seq = seq + [1.0] * (4 - len(seq))
        return inst(structs["color"], {"r": seq[0], "g": seq[1], "b": seq[2], "a": seq[3]})
    raise ValueError("cannot encode %r as %s" % (v, kind))


def guess_kind(v):
    if isinstance(v, bool):
        return "bool"
    if isinstance(v, int):
        return "int"
    if isinstance(v, float):
        return "float"
    if isinstance(v, (list, tuple)):
        return {2: "vec2", 3: "vec3", 4: "vec4"}.get(len(v))
    return None


_ENUMS = {}


def enum_value(enum_ref, display):
    if enum_ref not in _ENUMS:
        raw = tool("UEnum_Info", {"enum": {"refPath": enum_ref}}, INFO, enum_ref)
        info = json.loads(raw) if isinstance(raw, str) else (raw or {})
        _ENUMS[enum_ref] = info
    info = _ENUMS[enum_ref]
    want = display.replace(" ", "").lower()
    for v in info.get("Values", []):
        if v.get("DisplayName", "").replace(" ", "").lower() == want or v.get("Name", "").lower() == want:
            return inst(E + "Enum", {"enum": {"refPath": enum_ref},
                                     "enumName": "%s::%s" % (info.get("Name"), v["Name"]),
                                     "displayName": v["DisplayName"]})
    raise ValueError("enum %s has no value %r; options: %s" %
                     (enum_ref, display, [v.get("DisplayName") for v in info.get("Values", [])]))


def curve_keys(points, interp):
    mode = {"linear": "RCIM_Linear", "cubic": "RCIM_Cubic", "constant": "RCIM_Constant"}[interp]
    return {"keys": [{"interpMode": mode, "tangentMode": "RCTM_Auto", "tangentWeightMode": "RCTWM_WeightedNone",
                      "time": float(t), "value": float(v), "arriveTangent": 0, "arriveTangentWeight": 0,
                      "leaveTangent": 0, "leaveTangentWeight": 0} for t, v in points]}


class Stack:
    """Sets inputs on one emitter (or the system stack when emitter == '')."""

    def __init__(self, system, emitter):
        self.system = system
        self.emitter = emitter
        self._topo = {}

    def where(self, script, module, names):
        return "%s/%s/%s/%s" % (self.emitter or "<system>", script, module, ".".join(names))

    def input_type(self, script, module, names):
        if len(names) == 1:
            key = (script, module)
            if key not in self._topo:
                t = tool("GetModuleTopology", {"moduleRef": ref(self.system, self.emitter, script, module)},
                         what=self.where(script, module, []))
                self._topo[key] = {i["name"]: i["type"]["classStructOrEnum"]["refPath"]
                                   for i in (t or {}).get("inputs", [])}
            return self._topo[key].get(names[0])
        chain = tool("GetDynamicInputChain",
                     {"stackInputRef": ref(self.system, self.emitter, script, module, inputs=names[:-1])},
                     what=self.where(script, module, names[:-1]))
        for child in ((chain or {}).get("value") or {}).get("inputs", []):
            cv = child.get("value", {})
            if cv.get("name") == names[-1]:
                return cv["type"]["classStructOrEnum"]["refPath"]
        return None

    def curve_child(self, script, module, names):
        """Name of the curve data-interface input under a *FromCurve dynamic input."""
        chain = tool("GetDynamicInputChain",
                     {"stackInputRef": ref(self.system, self.emitter, script, module, inputs=names)},
                     what=self.where(script, module, names))
        for child in ((chain or {}).get("value") or {}).get("inputs", []):
            cv = child.get("value", {})
            if "Curve" in cv.get("type", {}).get("classStructOrEnum", {}).get("refPath", ""):
                return cv.get("name")
        return None

    def send(self, script, module, names, data):
        r = tool("SetStackInputData",
                 {"stackInputRef": ref(self.system, self.emitter, script, module, inputs=names), "inputData": data},
                 what=self.where(script, module, names))
        if r is None and len(names) == 1:
            modes = [k for k, t in self._topo.get((script, module), {}).items() if kind_of(t) == "enum"]
            if modes:
                warn("%s: if the input is hidden, one of these mode inputs gates it (set it first): %s"
                     % (self.where(script, module, names), ", ".join(modes)))
        return r is not None

    def set(self, script, module, names, v):
        where = self.where(script, module, names)
        try:
            type_ref = self.input_type(script, module, names)
            if type_ref is None and len(names) == 1:
                # Forgive case and stray spaces: some engine inputs end in a space ("Sprite Renderer ").
                loose = {k.strip().lower(): k for k in self._topo.get((script, module), {})}
                actual = loose.get(names[0].strip().lower())
                if actual:
                    names = [actual]
                    type_ref = self._topo[(script, module)][actual]
            if type_ref is None:
                known = sorted(self._topo.get((script, module), {})) if len(names) == 1 else []
                err("%s: no such input%s" % (where, (". Inputs: " + ", ".join(known)) if known else ""))
                return
            kind = kind_of(type_ref)
            if isinstance(v, dict) and "raw" in v:
                self.send(script, module, names, v["raw"])
            elif isinstance(v, dict) and "link" in v:
                if v["link"].startswith("User.") and v["link"][5:] not in USER_NAMES:
                    # Niagara accepts a link to a parameter that does not exist and silently uses a default.
                    err("%s: links to %s but the recipe declares no such user parameter (declared: %s)"
                        % (where, v["link"], ", ".join(sorted(USER_NAMES)) or "none"))
                    return
                self.send(script, module, names, inst(E + "Linked", {"linkedVariable": {
                    "name": v["link"], "type": {"classStructOrEnum": {"refPath": type_ref}}}}))
            elif isinstance(v, dict) and "hlsl" in v:
                self.send(script, module, names, inst(E + "HlslExpression", {"hlslExpression": v["hlsl"]}))
            elif isinstance(v, dict) and "dynamic" in v:
                if self.send(script, module, names,
                             inst(E + "DynamicInput", {"dynamicInputAsset": {"refPath": obj_path(v["dynamic"])}})):
                    for child, cv in (v.get("inputs") or {}).items():
                        self.set(script, module, names + [child], cv)
            elif isinstance(v, dict) and "range" in v:
                leaf = type_ref.rsplit(".", 1)[-1]
                leaf = {"Vector": "Vector3f", "Vector2D": "Vector2f", "Vector4": "Vector4f"}.get(leaf, leaf)
                if leaf not in RANGES:
                    err("%s: no random-range input for type %s" % (where, type_ref))
                    return
                lo, hi = v["range"]
                self.set(script, module, names, {"dynamic": RANGES[leaf], "inputs": {"Minimum": lo, "Maximum": hi}})
            elif isinstance(v, dict) and "renderer" in v:
                # Renderer-info data interface (SubUVAnimation, mesh index...): point it at a renderer.
                path = renderer_object_path(self.system, self.emitter, int(v["renderer"]))
                if not path:
                    err("%s: emitter has no renderer %s" % (where, v["renderer"]))
                    return
                prop = {"DataInterfaceSpriteRendererInfo": "SpriteRenderer",
                        "DataInterfaceMeshRendererInfo": "MeshRenderer"}.get(
                            type_ref.rsplit(".", 1)[-1].replace("Niagara", ""), "SpriteRenderer")
                self.send(script, module, names, inst(E + "DataInterface", {"propertyValues": json.dumps({prop: path})}))
            elif isinstance(v, dict) and any(k in v for k in CURVES):
                key = next(k for k in CURVES if k in v)
                asset, channels = CURVES[key]
                pts = v[key]
                interp = v.get("interp", "linear")
                if len(channels) == 1:
                    props = {channels[0]: curve_keys(pts, interp)}
                else:
                    props = {ch: curve_keys([(p[0], p[1][i]) for p in pts], interp)
                             for i, ch in enumerate(channels)}
                if "DataInterface" in type_ref:
                    # Some modules (ScaleSpriteSize, ScaleColor curve modes) take the curve directly.
                    self.send(script, module, names, inst(E + "DataInterface", {"propertyValues": json.dumps(props)}))
                elif self.send(script, module, names, inst(E + "DynamicInput", {"dynamicInputAsset": {"refPath": asset}})):
                    di_input = self.curve_child(script, module, names)
                    if not di_input:
                        err("%s: curve input has no curve data interface child" % where)
                        return
                    self.send(script, module, names + [di_input],
                              inst(E + "DataInterface", {"propertyValues": json.dumps(props)}))
                    for child, cv in (v.get("inputs") or {}).items():
                        self.set(script, module, names + [child], cv)
            elif isinstance(v, str):
                if kind != "enum":
                    err("%s: got text %r but the input type is %s" % (where, v, type_ref))
                    return
                if self.send(script, module, names, enum_value(type_ref, v)):
                    self._topo.pop((script, module), None)    # modes reveal / hide other inputs
            else:
                if kind in (None, "enum"):
                    err("%s: cannot write %r to input of type %s; use {\"raw\": ...}" % (where, v, type_ref))
                    return
                self.send(script, module, names, plain(kind, v))
        except Exception as ex:  # noqa: BLE001 - keep going so one bad value does not hide the rest
            err("%s: %s" % (where, ex))


# --------------------------------------------------------------------------- niagara systems

SCRIPTS = {"emitter_spawn": "EmitterSpawnScript", "emitter_update": "EmitterUpdateScript",
           "particle_spawn": "ParticleSpawnScript", "particle_update": "ParticleUpdateScript",
           "system_spawn": "SystemSpawnScript", "system_update": "SystemUpdateScript"}


def fix_refs(props):
    """Turn '/Game/..' strings inside renderer/emitter property dicts into {'refPath': ...}."""
    if isinstance(props, dict):
        return {k: fix_refs(v) for k, v in props.items()}
    if isinstance(props, list):
        return [fix_refs(v) for v in props]
    if isinstance(props, str) and props.startswith("/") and not props.startswith("/Script/"):
        return {"refPath": obj_path(props)}
    return props


SOLVER = "SolveForcesAndVelocity"


def insert_after(topo, script, op):
    """Name of the module a new module goes after ('' = end of stack), or None after an error.

    The tool can only insert *after* a named module. Forces and anything that feeds the solver
    must run before SolveForcesAndVelocity, so that is the default spot in particle update.
    """
    mods = []
    for key in ("emitterSpawnScript", "emitterUpdateScript", "particleSpawnScript", "particleUpdateScript"):
        sc = topo.get(key) or {}
        if sc.get("scriptName") == script:
            mods = [m["moduleName"] for m in sc.get("modules", [])]
    if op.get("after"):
        if op["after"] not in mods:
            err("add %s: no module %s in %s to insert after. Modules: %s"
                % (op["add"], op["after"], script, ", ".join(mods)))
            return None
        return op["after"]
    before = op.get("before") or (SOLVER if SOLVER in mods and not op.get("at_end") else "")
    if not before:
        return ""
    if before not in mods:
        err("add %s: no module %s in %s to insert before. Modules: %s" % (op["add"], before, script, ", ".join(mods)))
        return None
    i = mods.index(before)
    if i == 0:
        err("add %s: cannot insert before the first module (%s); the tool only inserts after a module"
            % (op["add"], before))
        return None
    return mods[i - 1]


def renderer_object_path(system, emitter, index):
    """Object path of an emitter's Nth renderer, for renderer-info data interfaces.

    The emitter lives inside the system as '<System>:<Emitter>_<n>'. Emitters removed earlier in
    the session linger in memory under a lower <n>, so take the highest one.
    """
    import re
    pat = re.compile(re.escape(system) + ":" + re.escape(emitter) + r"_(\d+)\.([^.:]+)$")
    best = {}
    for obj in unreal.ObjectIterator(unreal.NiagaraRendererProperties):
        m = pat.match(obj.get_path_name())
        if m:
            best.setdefault(int(m.group(1)), []).append(obj.get_path_name())
    if not best:
        return None
    found = sorted(best[max(best)], key=lambda p: (len(p), p))
    return found[index] if 0 <= index < len(found) else None


def find_module(topo, name):
    """Return (scriptName, moduleName) for a module in an emitter topology, by exact or loose name."""
    hits = []
    for key in ("emitterSpawnScript", "emitterUpdateScript", "particleSpawnScript", "particleUpdateScript"):
        sc = topo.get(key) or {}
        for m in sc.get("modules", []):
            if m["moduleName"] == name:
                return sc.get("scriptName"), m["moduleName"]
            if m["moduleName"].lower().startswith(name.lower()):
                hits.append((sc.get("scriptName"), m["moduleName"]))
    return hits[0] if len(hits) == 1 else (None, None)


def build_emitter(system, em):
    name = em["name"]
    template = em.get("template", "SimpleSpriteBurst")
    tpath = obj_path(template if template.startswith("/") else EMITTER_TEMPLATES + template)
    topo = tool("AddEmitter", {"system": {"refPath": system}, "templateEmitter": {"refPath": tpath},
                               "emitterName": name}, what=name)
    if topo is None:
        return
    name = topo.get("emitterName", name)
    stack = Stack(system, name)

    def retopo():
        return tool("GetEmitterTopology", {"emitterRef": ref(system, name)}, what=name) or topo

    if em.get("properties"):
        tool("SetEmitterData", {"emitter": ref(system, name),
                                "emitterData": {"propertyValues": json.dumps(fix_refs(em["properties"]))}}, what=name)

    # Renderers first: assigning a material after the modules that feed it has crashed the editor.
    for r in em.get("renderers", []):
        if "add" in r:
            cls = r["add"] if r["add"].startswith("/") else "/Script/Niagara." + r["add"]
            got = tool("AddRenderer", {"newRendererLocation": ref(system, name), "rendererClass": {"refPath": cls}},
                       what=r["add"])
            if got is None:
                continue
            index = got["rendererIndex"]
        elif "remove" in r:
            tool("RemoveRenderer", {"rendererToRemove": ref(system, name, renderer=int(r["remove"]))}, what=name)
            continue
        else:
            index = int(r.get("index", 0))
        if r.get("set"):
            tool("SetRendererData", {"renderer": ref(system, name, renderer=index),
                                     "rendererData": {"propertyValues": json.dumps(fix_refs(r["set"]))}},
                 what="%s renderer %d" % (name, index))

    for op in em.get("modules", []):
        if "remove" in op:
            script, module = find_module(topo, op["remove"])
            if not module:
                warn("%s: nothing to remove named %s" % (name, op["remove"]))
                continue
            tool("RemoveModule", {"moduleToRemove": ref(system, name, script, module)}, what=module)
            topo = retopo()
        elif "add" in op:
            script = SCRIPTS.get(op.get("stack", "particle_update"), op.get("stack"))
            after = insert_after(topo, script, op)
            if after is None:
                continue
            added = tool("AddModule", {"moduleLocationRef": ref(system, name, script, after),
                                       "moduleAsset": {"refPath": obj_path(op["add"])}}, what=op["add"])
            if added is None:
                continue
            topo = retopo()
            for iname, val in (op.get("inputs") or {}).items():
                stack.set(script, added["moduleName"], [iname], val)
        elif "set" in op:
            script, module = find_module(topo, op["set"])
            if not module:
                mods = [m["moduleName"] for k in ("emitterUpdateScript", "particleSpawnScript", "particleUpdateScript")
                        for m in (topo.get(k) or {}).get("modules", [])]
                err("%s: no module named %s. Modules: %s" % (name, op["set"], ", ".join(mods)))
                continue
            for iname, val in (op.get("inputs") or {}).items():
                stack.set(script, module, [iname], val)
        elif "enable" in op or "disable" in op:
            target = op.get("enable", op.get("disable"))
            script, module = find_module(topo, target)
            if module:
                tool("SetModuleEnabled", {"moduleRef": ref(system, name, script, module),
                                          "bEnabled": "enable" in op}, what=module)
        elif "set_parameters" in op:
            script = SCRIPTS.get(op.get("stack", "particle_update"), op.get("stack"))
            entries = []
            for p in op["set_parameters"]:
                kind = p.get("type", "float")
                entry = {"variable": {"name": p["name"], "type": {"classStructOrEnum": {"refPath": STRUCTS[kind]}}}}
                if "value" in p:
                    entry["defaultValue"] = plain(kind, p["value"])
                entries.append(entry)
            tool("AddSetParametersModule", {"moduleLocationRef": ref(system, name, script, op.get("after", "")),
                                            "parameters": entries}, what="set_parameters")
            topo = retopo()
        else:
            err("%s: unknown module op %s" % (name, list(op)))


def build_system(recipe):
    path = pkg_path(recipe["system"])
    folder, asset = path.rsplit("/", 1)
    trace("build_system " + path)
    eal = unreal.EditorAssetLibrary
    exists = eal.does_asset_exist(path)
    if exists:
        # Rebuild in place. Deleting and recreating the asset in one run crashes the editor on
        # save, and emptying it instead keeps every reference to the system valid.
        if not recipe.get("overwrite", True):
            err("system exists and overwrite is false: " + path)
            return
        system = obj_path(path)
        old = tool("GetUserVariables", {"system": {"refPath": system}}, what=path) or {}
        stale = [{"name": u["name"], "type": u["type"]} for u in old.get("userVariables", [])]
        if stale:
            tool("RemoveUserVariables", {"system": {"refPath": system}, "variablesToRemove": stale}, what=path)
    else:
        template = obj_path(recipe.get("template", SYSTEM_TEMPLATE))
        made = tool("CreateNiagaraSystem", {"assetName": asset, "assetPath": folder,
                                            "templateSystem": {"refPath": template}}, what=path)
        if not made:
            return
        system = made["refPath"]
    if exists or not recipe.get("keep_template_emitters", False):
        summary = tool("GetSystemSummary", {"system": {"refPath": system}}, what=path) or {}
        for e in summary.get("emitters", []):
            tool("RemoveEmitter", {"emitterToRemove": ref(system, e["emitterName"])}, what=e["emitterName"])

    USER_NAMES.clear()
    USER_NAMES.update(u["name"] for u in recipe.get("user", []))
    users = []
    for u in recipe.get("user", []):
        kind = u.get("type", "float")
        users.append({"name": u["name"], "type": {"classStructOrEnum": {"refPath": USER_STRUCTS[kind]}},
                      "defaultValue": plain(kind, u.get("value", 0), USER_STRUCTS)})
    if users:
        tool("AddUserVariables", {"system": {"refPath": system}, "variablesToAdd": users}, what=path)

    if recipe.get("properties"):
        tool("SetSystemData", {"system": {"refPath": system},
                               "systemData": {"propertyValues": json.dumps(fix_refs(recipe["properties"]))}}, what=path)

    for em in recipe.get("emitters", []):
        build_emitter(system, em)

    sys_stack = Stack(system, "")
    for op in recipe.get("system_modules", []):
        script = SCRIPTS.get(op.get("stack", "system_update"), op.get("stack"))
        for iname, val in (op.get("inputs") or {}).items():
            sys_stack.set(script, op["set"], [iname], val)

    if eal.save_asset(path, only_if_is_dirty=False):
        done("niagara system " + path)
        BUILT.append(system)
    else:
        err("could not save " + path)


BUILT = []
USER_NAMES = set()


def check_system(system):
    """Ask Niagara for compile state and stack issues. Needs the full editor (ue_run.sh), not a commandlet."""
    state = tool("GetSystemCompileState", {"system": {"refPath": system}}, what=system)
    if state is None:
        return
    if state.get("bHasErrors") or state.get("aggregateStatus") not in ("UpToDate", "UpToDateWithWarnings"):
        for sc in state.get("scripts", []):
            if sc.get("errorSummary") or sc.get("lastCompileStatus") not in ("UpToDate", "UpToDateWithWarnings"):
                err("%s compile: %s %s: %s %s" % (system, sc.get("emitterName"), sc.get("scriptName"),
                                                 sc.get("lastCompileStatus"), sc.get("errorSummary")))
    issues = tool("GetStackIssues", {"system": {"refPath": system}}, what=system) or {}
    for i in issues.get("issues", []):
        if i.get("severity") in ("Error", "Warning") and not i.get("bIsDismissed"):
            msg = "%s %s: %s. %s" % (system, i.get("stackDisplayPath"), i.get("shortDescription"),
                                    (i.get("longDescription") or "")[:300])
            (err if i["severity"] == "Error" else warn)(msg)
    done("checked %s: compile %s, %d stack errors, %d warnings" %
         (system, state.get("aggregateStatus"), issues.get("numErrors", 0), issues.get("numWarnings", 0)))


def dump_system(item):
    system = obj_path(item["system"])
    out = {"summary": tool("GetSystemSummary", {"system": {"refPath": system}}, what=system),
           "user": tool("GetUserVariables", {"system": {"refPath": system}}, what=system), "emitters": {}}
    for e in (out["summary"] or {}).get("emitters", []):
        n = e["emitterName"]
        r = ref(system, n)
        topo = tool("GetEmitterTopology", {"emitterRef": r}, what=n) or {}
        em = {"stacks": {}, "values": tool("GetEmitterInputValues", {"emitterRef": r}, what=n), "renderers": []}
        for key in ("emitterSpawnScript", "emitterUpdateScript", "particleSpawnScript", "particleUpdateScript"):
            sc = topo.get(key) or {}
            em["stacks"][sc.get("scriptName", key)] = [m["moduleName"] for m in sc.get("modules", [])]
        for rd in topo.get("renderers", []):
            data = tool("GetRendererData", {"rendererRef": ref(system, n, renderer=rd["rendererIndex"])}, what=n) or {}
            pv = data.get("propertyValues")
            em["renderers"].append({"class": rd["rendererClass"]["refPath"],
                                    "properties": json.loads(pv) if isinstance(pv, str) else pv})
        data = tool("GetEmitterData", {"emitterRef": r}, what=n) or {}
        pv = data.get("propertyValues")
        em["properties"] = json.loads(pv) if isinstance(pv, str) else pv
        out["emitters"][n] = em
    with open(local(item["out"]), "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1)
    done("dumped %s -> %s" % (system, local(item["out"])))


def list_modules(out_path):
    ar = unreal.AssetRegistryHelpers.get_asset_registry()
    ar.search_all_assets(True)
    lines = []
    for root in ("/Niagara/Modules", "/Niagara/DynamicInputs", "/Niagara/DefaultAssets/Templates", "/Game"):
        for a in ar.get_assets_by_path(root, recursive=True):
            cls = str(a.asset_class_path.asset_name)
            if cls == "NiagaraScript":
                if str(a.get_tag_value("LibraryVisibility")) != "Library" or str(a.get_tag_value("bDeprecated")) == "1":
                    continue
            elif cls not in ("NiagaraEmitter", "NiagaraSystem"):
                continue
            lines.append("%-14s %s" % (cls, a.package_name))
    out_path = local(out_path)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(sorted(lines)) + "\n")
    done("listed %d assets -> %s" % (len(lines), out_path))


def dump_schema(item):
    asset = obj_path(item["asset"])
    name = "GetDynamicInputSchemaFromAsset" if "/DynamicInputs/" in asset else "GetModuleSchemaFromAsset"
    key = "dynamicInputAsset" if "/DynamicInputs/" in asset else "moduleAsset"
    r = tool(name, {key: {"refPath": asset}}, what=asset)
    with open(local(item["out"]), "w", encoding="utf-8") as f:
        json.dump(r, f, indent=1)
    done("schema %s -> %s" % (asset, item["out"]))


# --------------------------------------------------------------------------- entry

def main():
    job_path = os.environ.get("VFX_JOB") or (sys.argv[1] if len(sys.argv) > 1 else "")
    if not job_path or not os.path.isfile(job_path):
        raise RuntimeError("no job file: set VFX_JOB or pass the path as the first argument")
    with open(job_path, encoding="utf-8") as f:
        job = json.load(f)
    global _TRACE, JOB_DIR
    JOB_DIR = os.path.dirname(os.path.abspath(job_path))
    _TRACE = open(job_path + ".trace.txt", "w", encoding="utf-8")
    try:
        if job.get("textures"):
            import_textures(job["textures"])
        mats = job.get("materials")
        if mats:
            dest = mats.get("dest", "/Game/VFX/Materials")
            ensure_masters(dest, mats.get("rebuild_masters", False))
            make_instances(dest, mats.get("instances", []))
        for recipe in job.get("systems", []):
            build_system(recipe)
        for item in job.get("dump", []):
            dump_system(item)
        if job.get("modules"):
            list_modules(job["modules"])
        for item in job.get("schema", []):
            dump_schema(item)
        if os.environ.get("VFX_MODE") == "editor":
            for system in BUILT + [obj_path(c) for c in job.get("check", [])]:
                check_system(system)
    except Exception:  # noqa: BLE001
        err(traceback.format_exc())
    finally:
        with open(job_path + ".result.json", "w", encoding="utf-8") as f:
            json.dump(REPORT, f, indent=1)
        if os.environ.get("VFX_MODE") == "editor":
            # -ExecutePythonScript leaves the editor running; quit once it starts ticking.
            handle = []
            handle.append(unreal.register_slate_post_tick_callback(lambda dt: (
                unreal.unregister_slate_post_tick_callback(handle[0]), unreal.SystemLibrary.quit_editor())))


main()
