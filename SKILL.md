---
name: unreal-vfx
description: >-
  Builds Unreal Engine VFX the way most game effects are really made: quads, a small set of
  textures, and per-particle tricks in Niagara. Scripts the whole pipeline headlessly from JSON
  (no hand-clicking in the editor): packs sprite sheets, imports textures, generates master
  particle materials and instances (additive / alpha, flipbook, erosion, soft particles, UV
  panning), and creates or rebuilds Niagara systems (emitters, modules, curves, random ranges,
  user parameters, renderers), then checks compile state and stack errors. Use whenever the user
  wants a visual effect in an Unreal project: explosion, impact, hit spark, muzzle flash, smoke,
  dust, fire, magic or spell effects, slash or trail, beam, shockwave, aura, pickup glow, or says
  Niagara, particle system, VFX, flipbook, SubUV, sprite sheet, particle material, or asks to
  tune, inspect or fix an existing Niagara system. Also use to turn a texture pack into
  ready-to-use particle materials. Verified on UE 5.8; needs the engine's experimental
  NiagaraToolsets plugin (shipped with 5.8). Not for GLSL/Shadertoy shaders, Unity, or
  post-process and mesh-surface materials unrelated to effects.
---

# Unreal VFX: quads, textures, per-particle tricks

Most game VFX is not simulation. It is a handful of textures drawn on camera-facing quads, with
each particle given its own size, rotation, colour, frame and dissolve over its life. An effect
that reads well is three to six such layers timed against each other. This skill builds exactly
that, and builds it from files, so effects are reviewable, repeatable and tunable by editing
numbers rather than by clicking through the Niagara editor.

Niagara, material and texture assets are binary; you cannot write them directly. The scripts here
drive the editor headlessly instead. One JSON **job** file describes textures, materials and
systems; one command applies it and reports back.

## Workflow

1. **Design the layers before touching anything.** Name the effect's beats and what each layer is
   (flash, core, sparks, smoke, ring, debris, light). `references/effect-recipes.md` has the
   anatomy of common effects with starting values, and the catalogue of per-particle tricks.
2. **Get textures.** Use the user's pack if they have one, generate the staples, or both.
   `scripts/vfx_tex.py` packs variations or frames into a SubUV atlas and generates glow, ring,
   spark and tiling noise. Look at the result with the Read tool before importing.
3. **Write the job.** Start from `examples/impact/job.json`. Put the job in the project
   (for example `<Project>/Tools/vfx/<effect>/job.json`) so it is versioned with the game.
4. **Run it.** `bash scripts/ue_run.sh <Project.uproject> <job.json>` (about 15 seconds).
5. **Read the result and fix what it names.** Errors say which input, and list the valid names
   or enum options. Wrong input names are normal on the first pass; see "Discovering names".
6. **Have it looked at.** The run proves the system compiles; it cannot show you the effect.
   See "What a run does and does not prove".
7. **Tune by editing numbers and rerunning.** Rebuilds are in place, so placed instances and
   references to the system survive.

## Running jobs

```bash
bash <skill>/scripts/ue_run.sh "C:/path/Project.uproject" "C:/path/job.json"
```

- Finds the engine from the project's `EngineAssociation` (override with `UE_ROOT`), starts a
  headless editor, enables `NiagaraToolsets` and `ToolsetRegistry` for that run only (the
  `.uproject` is not modified), and exits non-zero if the job reported errors.
- Writes `<job>.result.json` (`ok`, `errors`, `warnings`, `done`), `<job>.log` (full editor log)
  and `<job>.trace.txt` (every tool call in order).
- **If there is no result file the editor crashed.** The last line of the trace names the call
  that did it.
- Paths inside a job may be relative to the job file. Use forward slashes everywhere; Unreal
  eats backslashes in script paths.
- If the user has the project open in the editor, ask them to close it (or at least the assets
  the job touches) before a run. Two editors writing the same asset is untested here and the
  open one will not see the new version until it reloads. Never close their editor for them.
- There is no system Python on many Windows machines. The texture tool runs on Unreal's own:
  `"<UE>/Engine/Binaries/ThirdParty/Python3/Win64/python.exe" -I scripts/vfx_tex.py ...`

## Textures

```bash
PY="C:/Program Files/Epic Games/UE_5.8/Engine/Binaries/ThirdParty/Python3/Win64/python.exe"
"$PY" -I scripts/vfx_tex.py info  pack/smoke_01.png              # where is the shape: alpha or RGB?
"$PY" -I scripts/vfx_tex.py pack  tex/smoke_atlas.png pack/smoke_0[1-8].png --cols 4 --cell 256
"$PY" -I scripts/vfx_tex.py shape glow tex/glow.png   # also: ring | spark | noise | band | streaks | blade
```

- **The shape must be in alpha** for the sprite materials. Art on a black background has it in
  RGB instead; `info` says so, and `pack --luma-alpha` fixes it.
- `pack` prints the `SubImageSize` to put on the renderer. Order is row-major from the top left.
- An atlas is used two ways: **variation** (each particle picks one random frame, which is what
  hides repetition) or **flipbook** (frames play over the particle's life).
- Keep cells at 256 or less unless the effect fills the screen; overdraw, not resolution, is
  what costs.
- Job entries: `{"src": "tex/x.png", "name": "T_VFX_X", "kind": "sprite" | "tile"}`. `tile`
  wraps (noise, scrolling streaks); `sprite` clamps. All import as sRGB colour textures on
  purpose so any texture can go in any material slot.

## Materials

The job creates four master materials once in `/Game/VFX/Materials` and any number of instances.
Prefer a new instance over a new master.

| `master` key | Use | Texture parameters |
|---|---|---|
| `sprite_add` | glows, sparks, flashes, fire cores | `Texture` (SubUV aware) |
| `sprite_alpha` | smoke, dust, debris, anything that should darken | `Texture` |
| `panner_add` / `panner_alpha` | beams, trails, shockwave bands, auras: two scrolling layers times a mask | `Texture`, `Noise`, `Mask` |

Shared: scalars `EmissiveBoost`, `ErodeSoftness`, `SoftDistance`, the switch `SoftParticles`, and the
colour `HotColor`, which is added where the shape is strongest (red particle colour plus a yellow
`HotColor` gives a red body with yellow cores; black, the default, turns it off).
Panner extras: `MainTileU/V`, `MainSpeedU/V`, `NoiseTileU/V`, `NoiseSpeedU/V`, `NoiseAmount`,
`TextureUseAlpha`.

Every master reads particle colour and one dynamic parameter: **x = Erode** (0 to 1 dissolves the
shape from its thin edges inward) and **y = Boost** (brightness multiplier). The panner masters also
read **z and w as a UV offset** on the main texture (`Index 0 Param 3` and `4`); a curve on z slides
the texture along a ribbon as it ages. Drive them with the
`DynamicMaterialParameters` module: `Index 0 Param 1` is Erode, `Index 0 Param 2` is Boost. If you
add that module, set Param 2 to 1, because it writes all four values and the default 0 turns
the particle black.

Instance: `{"name": "MI_VFX_Smoke", "master": "sprite_alpha", "params": {"Texture": "/Game/VFX/Textures/T_VFX_SmokeAtlas", "SoftDistance": 60}}`.
Number means scalar, `true`/`false` a switch, a `/Game/...` string a texture, a list a colour.
`"rebuild_masters": true` regenerates the masters after you change the generator.

More material tricks (and when to write a new master): `references/effect-recipes.md`.

## Niagara systems

A system recipe lists emitters; each emitter starts from an engine template and applies ordered
edits. Full format, value shorthands and gotchas: `references/niagara-scripting.md`. Read it
before writing your first recipe. The shape:

```json
{"system": "/Game/VFX/Systems/NS_Impact",
 "user": [{"name": "Tint", "type": "color", "value": [1, 0.6, 0.2, 1]}],
 "emitters": [{
   "name": "Sparks", "template": "SimpleSpriteBurst",
   "renderers": [{"index": 0, "set": {"Material": "/Game/VFX/Materials/MI_VFX_Spark", "Alignment": "VelocityAligned"}}],
   "modules": [
     {"set": "SpawnBurst_Instantaneous", "inputs": {"Spawn Count": 24}},
     {"set": "InitializeParticle", "inputs": {"Lifetime Mode": "Random", "Lifetime Min": 0.25, "Lifetime Max": 0.6}},
     {"add": "/Niagara/Modules/Spawn/Velocity/AddVelocity", "stack": "particle_spawn",
      "inputs": {"Velocity Mode": "From Point", "Velocity Speed": {"range": [300, 900]}}},
     {"add": "/Niagara/Modules/Update/Forces/GravityForce", "inputs": {"Gravity": [0, 0, -980]}}
   ]}]}
```

The three rules that prevent most failed runs:

- **Set mode inputs before the inputs they reveal.** `"Lifetime Mode": "Random"` must come before
  `Lifetime Min`. Inputs are applied in the order written. A hidden input is refused with a
  message, and the run lists the module's mode inputs for you.
- **Use the exact module and input names.** They are display names with spaces
  (`Uniform Sprite Size`), and they differ per module version. Do not guess from memory; dump.
- **Expose what gameplay will change as `user` parameters** and link to them with
  `{"link": "User.Tint"}`, so code can retint or rescale without a new asset.

### Discovering names

Do not rely on remembered Niagara names; they drift between engine versions. Ask the engine:

- `"dump": [{"system": "/Game/...", "out": "dump.json"}]` writes every module, every visible
  input with its current value, and renderer and emitter properties. Run it on a system you just
  built (after `set`ting the mode you want) or on any existing system, including Epic's templates
  under `/Niagara/DefaultAssets/Templates/`. This is also how you read a system the user made.
- `"modules": "modules.txt"` lists every library module, dynamic input and emitter template.
- A wrong name costs one 15-second run: the error lists the valid inputs or enum options.

## What a run does and does not prove

Proven by `"ok": true`: assets exist and saved, every input was accepted, Niagara reports the
system compiled, and there are no stack errors.

Not proven: **how it looks**. A system can compile cleanly and still be dim, thin, the wrong
colour or invisible; the first sword trail built with this skill passed every check and looked
like a few faint lines. So look before you report.

## Looking at an effect

```bash
bash <skill>/scripts/ue_shot.sh "C:/path/Project.uproject" "C:/path/shot.json"
```

Renders the systems offscreen (no window) with a real GPU and writes PNG stills you can open
with the Read tool. Config, in full, is at the top of `scripts/ue/vfx_shot.py`:

```json
{"out_dir": "shots", "camera": {"at": [-520, 0, 150], "fov": 60},
 "systems": [
   {"path": "/Game/VFX/Systems/NS_Blaze", "at": [0, -200, 215]},
   {"path": "/Game/VFX/Systems/NS_Trail", "at": [0, 90, 150],
    "swing": {"axis": "roll", "from": 70, "to": 310, "start": 8.0, "time": 0.28, "arm": [0, 0, -120]}},
   {"path": "/Game/VFX/Systems/NS_Impact", "at": [0, 0, 60], "restart_at": 8.0}],
 "shots": [8.14, 8.24, 8.36, 8.5]}
```

- Start the action around 8 seconds in. Shaders compile on first use and earlier frames can
  show nothing.
- The background is black, which suits additive effects. `"backdrop": true` adds a lit grey
  floor and wall for smoke and anything alpha-blended.
- Trails and ribbons only exist while the emitter travels: give them a `swing` with an `arm`
  so the emitter moves along an arc. Rotating in place draws nothing.
- One-shot bursts have finished long before the first shot; use `restart_at`.
- It creates a temporary level at `/Game/VFX/_VfxShotTmp`. Prefer shooting in a scratch project
  that has the same job applied, so the user's project gains no test level.
- This shows the effect alone on a test rig, not on the user's character or in their level.
  Say so, and still ask them how it looks in game.

Iterate against the stills: change numbers, rerun the job, reshoot. Give tuning as specific
number changes, not adjectives.

## Mobile and performance

Check the project's target before choosing techniques (a `CLAUDE.md`, memory note, or
`Config/DefaultEngine.ini`). On mobile: keep emitters on CPU sim, keep counts low, prefer one
bigger particle to many small ones, set `"SoftParticles": false` on instances (depth fade needs
a scene depth read), avoid light renderers, and keep translucent layers few and small on screen
because overdraw is the budget. `references/effect-recipes.md` has budgets.

## Effects on melee attacks

When the effect sits on an attack and the user turns to how the attack itself behaves (combo
timing, attack chaining, clicks feeling insensitive, movement cancelling attacks), that is a
separate skill: use `unreal-melee-combat` if it is installed.

## Reference files

- `references/niagara-scripting.md`: recipe format, value shorthands, module placement, known
  engine quirks. Read before writing or debugging a recipe.
- `references/effect-recipes.md`: layer anatomy and starting values for common effects, the
  per-particle trick catalogue, material tricks, budgets. Read when designing an effect.
- `references/runtime.md`: spawning, pooling and driving user parameters from C++ or Blueprint.
  Read when wiring an effect into gameplay.
- `examples/impact/job.json`: a complete four-layer impact (flash, ring, sparks, smoke) that
  exercises every feature. Copy it as the starting point.
