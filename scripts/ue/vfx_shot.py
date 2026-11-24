"""Render Niagara systems to PNG stills without opening a window. Runs INSIDE the editor.

Launched by scripts/ue_shot.sh, which sets VFX_SHOT to a config JSON:

  {"out_dir": "shots",                      relative to the config file
   "camera": {"at": [-350, 0, 120], "pitch": 0, "yaw": 0, "fov": 60},
   "size": [1280, 720],
   "backdrop": false,                       true adds a lit grey floor and wall behind the effect
   "systems": [
     {"path": "/Game/VFX/Systems/NS_X", "at": [0, 0, 100], "rot": [0, 0, 0],        roll, pitch, yaw
      "swing": {"axis": "yaw", "from": -90, "to": 120, "start": 0.5, "time": 0.3,
                "arm": [0, 0, 90]},         optional motion: rotate, with the emitter out on an arm
      "restart_at": 0.5}                    optional: reactivate at this time (one-shot bursts)
   ],
   "shots": [0.55, 0.65, 0.8, 1.2]}         seconds after play starts

The effects run in Simulate-in-Editor on an empty (black) level, so they tick as they do in game,
and a scene capture writes shot_<time>.png for each entry. The editor quits when done.
"""
import json
import os
import traceback

import unreal

CFG_PATH = os.environ["VFX_SHOT"]
with open(CFG_PATH, encoding="utf-8") as f:
    CFG = json.load(f)
OUT = CFG.get("out_dir", "shots")
if not os.path.isabs(OUT):
    OUT = os.path.join(os.path.dirname(os.path.abspath(CFG_PATH)), OUT)
os.makedirs(OUT, exist_ok=True)
LOG = open(os.path.join(OUT, "shot.log.txt"), "w", encoding="utf-8")
LEVEL = "/Game/VFX/_VfxShotTmp"
S = {"t": None, "wall": 0.0, "shots": sorted(CFG.get("shots", [1.0])), "taken": 0, "restarted": set(),
     "handle": None, "rt": None}


def log(*a):
    LOG.write(" ".join(str(x) for x in a) + "\n")
    LOG.flush()


def finish():
    unreal.unregister_slate_post_tick_callback(S["handle"])
    try:
        unreal.get_editor_subsystem(unreal.LevelEditorSubsystem).editor_request_end_play()
    except Exception:  # noqa: BLE001
        pass
    log("done")
    LOG.close()
    unreal.SystemLibrary.quit_editor()


def rot(values):
    return unreal.Rotator(roll=values[0], pitch=values[1], yaw=values[2])


def setup():
    les = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
    eas = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    if unreal.EditorAssetLibrary.does_asset_exist(LEVEL):
        les.load_level(LEVEL)
        for actor in eas.get_all_level_actors():
            if isinstance(actor, (unreal.NiagaraActor, unreal.SceneCapture2D, unreal.StaticMeshActor, unreal.Light)):
                actor.destroy_actor()
    else:
        les.new_level(LEVEL)
    world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
    w, h = CFG.get("size", [1280, 720])
    S["rt"] = unreal.RenderingLibrary.create_render_target2d(
        world, w, h, unreal.TextureRenderTargetFormat.RTF_RGBA8_SRGB)
    cam = CFG.get("camera", {})
    cap = eas.spawn_actor_from_class(unreal.SceneCapture2D, unreal.Vector(*cam.get("at", [-350, 0, 120])),
                                     unreal.Rotator(roll=0, pitch=cam.get("pitch", 0), yaw=cam.get("yaw", 0)))
    cc = cap.capture_component2d
    cc.texture_target = S["rt"]
    cc.capture_source = unreal.SceneCaptureSource.SCS_FINAL_TONE_CURVE_HDR
    cc.set_editor_property("capture_every_frame", False)
    cc.set_editor_property("capture_on_movement", False)
    cc.fov_angle = cam.get("fov", 60)
    if CFG.get("backdrop"):
        plane = unreal.load_asset("/Engine/BasicShapes/Plane")
        for at, r in (([200, 0, 0], [0, 0, 0]), ([500, 0, 300], [0, 90, 0])):
            a = eas.spawn_actor_from_class(unreal.StaticMeshActor, unreal.Vector(*at), rot(r))
            a.static_mesh_component.set_static_mesh(plane)
            a.set_actor_scale3d(unreal.Vector(30, 30, 1))
        eas.spawn_actor_from_class(unreal.DirectionalLight, unreal.Vector(0, 0, 500),
                                   unreal.Rotator(roll=0, pitch=-50, yaw=30))
    for i, s in enumerate(CFG["systems"]):
        asset = unreal.load_asset(s["path"])
        if not asset:
            log("MISSING", s["path"])
            continue
        a = eas.spawn_actor_from_class(unreal.NiagaraActor, unreal.Vector(*s.get("at", [0, 0, 100])),
                                       rot(s.get("rot", [0, 0, 0])))
        a.set_actor_label("VfxShot_%d" % i)
        a.niagara_component.set_asset(asset)
        a.niagara_component.set_editor_property("auto_activate", True)
    les.editor_play_simulate()
    log("simulate requested")


def game_actors():
    world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_game_world()
    if not world:
        return None, {}
    found = {}
    for a in unreal.GameplayStatics.get_all_actors_of_class(world, unreal.NiagaraActor):
        label = a.get_actor_label()
        if label.startswith("VfxShot_"):
            found[int(label.split("_")[1])] = a
    return world, found


def tick(dt):
    try:
        S["wall"] += dt
        world, actors = game_actors()
        if world is None:
            if S["wall"] > 60:
                log("simulate never started")
                finish()
            return
        if S["t"] is None:
            S["t"] = 0.0
            log("play started; actors", sorted(actors))
        S["t"] += dt
        t = S["t"]
        for i, s in enumerate(CFG["systems"]):
            a = actors.get(i)
            if not a:
                continue
            sw = s.get("swing")
            if sw:
                k = min(1.0, max(0.0, (t - sw.get("start", 0.0)) / sw["time"]))
                k = k * k * (3 - 2 * k)
                r = list(s.get("rot", [0, 0, 0]))
                r[{"roll": 0, "pitch": 1, "yaw": 2}[sw.get("axis", "yaw")]] += sw["from"] + (sw["to"] - sw["from"]) * k
                # "arm" (local offset from the pivot) makes the emitter travel an arc, like a blade.
                arm = unreal.Vector(*sw.get("arm", [0, 0, 0]))
                pivot = unreal.Vector(*s.get("at", [0, 0, 100]))
                offset = unreal.MathLibrary.greater_greater_vector_rotator(arm, rot(r))
                a.set_actor_location_and_rotation(pivot + offset, rot(r), False, False)
            if "restart_at" in s and i not in S["restarted"] and t >= s["restart_at"]:
                S["restarted"].add(i)
                a.niagara_component.activate(True)
        while S["taken"] < len(S["shots"]) and t >= S["shots"][S["taken"]]:
            name = "shot_%05.2f.png" % S["shots"][S["taken"]]
            # The capture does not fire by itself offscreen; trigger the play-world copy by hand.
            for cap in unreal.GameplayStatics.get_all_actors_of_class(world, unreal.SceneCapture2D):
                cap.capture_component2d.texture_target = S["rt"]
                cap.capture_component2d.capture_scene()
            unreal.RenderingLibrary.export_render_target(world, S["rt"], OUT, name)
            log("shot", name, "t=%.3f" % t)
            S["taken"] += 1
        if S["taken"] >= len(S["shots"]) or S["wall"] > 120:
            finish()
    except Exception:  # noqa: BLE001
        log(traceback.format_exc())
        finish()


try:
    setup()
    S["handle"] = unreal.register_slate_post_tick_callback(tick)
except Exception:  # noqa: BLE001
    log(traceback.format_exc())
    LOG.close()
    _h = []
    _h.append(unreal.register_slate_post_tick_callback(lambda dt: (
        unreal.unregister_slate_post_tick_callback(_h[0]), unreal.SystemLibrary.quit_editor())))
