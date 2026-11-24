# Scripting Niagara from a job file

Contents: how it works · system recipe · emitter recipe · module ops · value shorthands ·
renderers · emitter and system properties · inspecting · engine quirks · going beyond the recipe

## How it works

UE 5.8 ships an experimental plugin, `NiagaraToolsets`, that exposes Niagara editing as JSON tool
calls through `ToolsetRegistry` (`unreal.ToolsetRegistry.execute_tool(toolset, tool, json)`).
`scripts/ue/vfx_job.py` wraps those calls in a recipe format. The tools are the engine's own, so
their error messages are precise; the wrapper adds name lookups and shorthands.

The API is marked experimental by Epic and may change between engine versions. If a run fails in
a way that looks like the API moved (unknown tool, schema mismatch), dump the live schema and
adapt the wrapper rather than guessing:

```python
# inside the editor's Python
unreal.ToolsetRegistry.get_toolset_json_schema(unreal.NiagaraToolset_System)
```

The engine also ships its own guidance for these tools, worth reading when stuck:
`<UE>/Engine/Plugins/Experimental/Toolsets/NiagaraToolsets/Content/Python/skills/*.py`
(fundamentals, renderers, optimization, blueprint_interop, custom_hlsl).

## System recipe

```json
{
  "system": "/Game/VFX/Systems/NS_Name",
  "overwrite": true,
  "user": [
    {"name": "Tint",  "type": "color", "value": [1, 1, 1, 1]},
    {"name": "Scale", "type": "float", "value": 1.0}
  ],
  "properties": { },
  "emitters": [ ]
}
```

- A new system is created from `/Niagara/DefaultAssets/DefaultSystem` and its template emitter is
  removed. An existing system is **emptied and rebuilt in place** (emitters and user parameters
  removed, then re-added), which keeps references and placed instances valid.
- `user` types: `float`, `int`, `bool`, `vec2`, `vec3`, `vec4`, `color`, `position`, `quat`.
  Names are written without the `User.` prefix here and with it when linked.
- `"overwrite": false` refuses to touch an existing system.

## Emitter recipe

```json
{
  "name": "Smoke",
  "template": "SimpleSpriteBurst",
  "properties": {"bLocalSpace": false},
  "renderers": [ {"index": 0, "set": {"Material": "/Game/VFX/Materials/MI_VFX_Smoke"}} ],
  "modules": [ ]
}
```

`template` is a name under `/Niagara/DefaultAssets/Templates/Emitters/` or a full asset path to
any emitter (including one of the project's own, which is how you reuse a library emitter).
Engine templates and what they start with:

| Template | Starts as |
|---|---|
| `SimpleSpriteBurst` | one burst of sprites, no motion. The best blank slate for a layer. |
| `OmnidirectionalBurst` | burst flying outward with gravity and drag |
| `DirectionalBurst` | burst in a cone |
| `Fountain` | continuous spawn, upward cone, gravity |
| `HangingParticulates` | slow ambient motes in a volume |
| `BlowingParticles` | ambient particles pushed by wind and curl noise |
| `SingleLoopingParticle` | one persistent particle (auras, glows, a single quad) |
| `UpwardMeshBurst` | mesh renderer burst (debris) |
| `StaticBeam`, `DynamicBeam` | ribbon beam between two points |
| `LocationBasedRibbon` | ribbon trail following the emitter |
| `ConfettiBurst` | tumbling sprites aligned to mesh orientation |
| `Minimal` | emitter state and one particle, nothing else |

`SimpleSpriteBurst` contains: emitter update `EmitterState`, `SpawnBurst_Instantaneous`; particle
spawn `InitializeParticle`; particle update `ParticleState`, `ScaleColor`,
`SolveForcesAndVelocity`. Dump any other template once to see its stack before editing it.

Renderers are applied before modules. Do not rely on the reverse.

## Module ops

`modules` is an ordered list; each entry is one op.

| Op | Meaning |
|---|---|
| `{"set": "ModuleName", "inputs": {...}}` | set inputs on a module already in the stack |
| `{"add": "/Niagara/Modules/...", "stack": "...", "inputs": {...}}` | add a library module, then set inputs |
| `{"remove": "ModuleName"}` | remove a module |
| `{"disable": "ModuleName"}` / `{"enable": ...}` | toggle without removing |
| `{"set_parameters": [{"name": "Particles.MyValue", "type": "float", "value": 0}], "stack": "particle_spawn"}` | add a Set Parameters module |

- `stack`: `emitter_spawn`, `emitter_update`, `particle_spawn`, `particle_update` (default).
- **Placement.** In particle update, an added module goes just before `SolveForcesAndVelocity`,
  because forces placed after the solver do nothing. Elsewhere it goes at the end. Override with
  `"after": "ModuleName"`, `"before": "ModuleName"` or `"at_end": true`. The engine tool can only
  insert after a module, so `before` the first module in a stack is an error.
- Module names in `set` are the names in the stack (`InitializeParticle`, no spaces), matched
  exactly or by unique prefix. A second copy of a module gets a numeric suffix; dump to see it.
- Input names are display names (`Spawn Count`, `Uniform Sprite Size`). Matching forgives case
  and stray spaces, nothing else.
- Inputs are applied in the order written. Put mode switches first.

Modules used constantly (all under `/Niagara/Modules/`):

| Path | Stack | For |
|---|---|---|
| `Emitter/SpawnRate` | emitter_update | continuous spawn |
| `Emitter/SpawnBurst_Instantaneous` | emitter_update | bursts |
| `Spawn/Location/V2/ShapeLocation` | particle_spawn | spawn in a sphere, box, ring, cone |
| `Spawn/Velocity/AddVelocity` | particle_spawn | initial velocity: linear, from point, in cone |
| `Update/Forces/GravityForce`, `Update/Forces/Drag` | particle_update | fall and slow down |
| `Update/Forces/V2/CurlNoiseForce` | particle_update | turbulence for smoke and magic |
| `Update/Forces/VortexForce`, `Update/Forces/V2/PointAttractionForce` | particle_update | swirl, implode |
| `Update/Size/ScaleSpriteSize` | particle_update | size over life |
| `Update/Color/Color`, `Update/Color/ScaleColor` | particle_update | colour and alpha over life |
| `Update/Orientation/SpriteRotationRate` | particle_update | spin |
| `Update/SubUV/V2/SubUVAnimation` | particle_update | flipbook playback or random frame |
| `Update/Material/DynamicMaterialParameters` | particle_update | per-particle Erode and Boost |
| `Update/Camera/CameraOffset` | particle_update | push a layer toward or away from camera |
| `Update/Velocity/ScaleVelocity`, `Update/Velocity/InheritVelocity` | particle_update | motion shaping |
| `Update/Lifetime/KillParticles` | particle_update | conditional kill |

## Value shorthands

The wrapper reads each input's type from the live stack and encodes the value for you.

| Write | Sets |
|---|---|
| `12`, `0.5`, `true` | a float, int or bool, by the input's type |
| `[x, y]`, `[x, y, z]`, `[r, g, b, a]` | vector or colour, by the input's type |
| `"Random"` | an enum or mode input, by display name. A wrong value lists the options. |
| `{"range": [min, max]}` | uniform random per particle (float, int, vector, colour inputs) |
| `{"curve": [[t, v], ...]}` | float over normalised age. Add `"interp": "cubic"` or `"constant"` (default linear). |
| `{"curve2": [[t, [x, y]], ...]}`, `{"curve3": ...}` | vector curves |
| `{"color_curve": [[t, [r, g, b, a]], ...]}` | colour over normalised age |
| `{"link": "User.Tint"}` | read a parameter: `User.*`, `Emitter.*`, `Particles.*`, `Engine.*` |
| `{"hlsl": "Particles.NormalizedAge * 2"}` | a custom expression (last resort; single rvalue, basic maths only) |
| `{"dynamic": "/Niagara/DynamicInputs/...", "inputs": {...}}` | any dynamic input, with its own inputs |
| `{"renderer": 0}` | point a renderer-info input (SubUVAnimation's `Sprite Renderer`) at this emitter's renderer |
| `{"raw": {"struct": {...}, "value": {...}}}` | the tool's native payload, when nothing above fits |

Notes that save a rerun:

- Many modules have their own min/max mode (`"Lifetime Mode": "Random"` then `Lifetime Min` and
  `Lifetime Max`). Prefer it to `{"range": ...}` when it exists.
- Curve inputs come in two kinds. Most are plain floats that take a curve dynamic input; some
  modules expose the curve directly once a mode is set (`ScaleSpriteSize` with
  `"Scale Sprite Size Mode": "Uniform Curve"` then `Uniform Curve Sprite Scale`). `{"curve": ...}`
  handles both.
- A curve's index defaults to `Particles.NormalizedAge`. To drive it from something else, add
  `"inputs": {"CurveIndex": {"link": "Emitter.LoopedAge"}}` beside the curve.
- A link to a `User.*` name the recipe does not declare is an error here, because Niagara itself
  accepts it silently and uses a default.
- Units are centimetres and seconds. A 2 m puff is `Uniform Sprite Size: 200`. Gravity is
  `[0, 0, -980]`.

## Renderers

```json
"renderers": [
  {"index": 0, "set": {"Material": "/Game/VFX/Materials/MI_VFX_Smoke",
                       "SubImageSize": {"x": 4, "y": 2}, "bSubImageBlend": true,
                       "Alignment": "VelocityAligned", "SortMode": "None"}},
  {"add": "NiagaraLightRendererProperties", "set": { }},
  {"remove": 1}
]
```

- `set` takes renderer property names as the engine spells them (PascalCase). Asset paths may be
  plain `/Game/...` strings. Only the properties you list are changed. A dump shows every
  property and its current value, which is the reliable way to learn enum spellings
  (`Alignment`, `FacingMode`, `SortMode`).
- Classes for `add`: `NiagaraSpriteRendererProperties`, `NiagaraMeshRendererProperties`,
  `NiagaraRibbonRendererProperties`, `NiagaraLightRendererProperties`,
  `NiagaraDecalRendererProperties`. Light, decal and component renderers force the emitter to CPU.
- For a flipbook: `SubImageSize` matching the atlas, a `SubUVAnimation` module, and that module's
  `Sprite Renderer` input set to `{"renderer": 0}`. All three, or the frames do not advance.
- Mesh cards (a ring, cone, cylinder or plane with a panner material) use the mesh renderer: set
  `Meshes` on it. Dump a mesh emitter (`UpwardMeshBurst`) once to see the exact property shape.

## Emitter and system properties

`"properties"` on an emitter takes `FVersionedNiagaraEmitterData` fields: `bLocalSpace`,
`SimTarget` (`"CPUSim"` or `"GPUComputeSim"`), `CalculateBoundsMode` (`"Dynamic"`, `"Fixed"`,
`"Programmable"`), `FixedBounds`, `bDeterminism`, `RandomSeed`. A dump's `properties` block shows
every field. GPU emitters need fixed bounds.

Looping, duration and delay are not properties; they are inputs on the `EmitterState` module
(`Loop Behavior`: `Once`, `Infinite`, `Multiple`; `Loop Duration`; `Loop Delay`). To delay a
layer, set `"UseLoopDelay": true` and `Loop Delay`, or give the burst a `Spawn Time`.

## Inspecting

`"dump"` output per emitter: `stacks` (module names in order per script), `values` (every
visible input with its encoded value), `renderers` (class and properties), `properties`. Use it
to learn names, to confirm a value landed, and to read systems you did not build. Inputs hidden
by a mode do not appear until the mode is set, so set the mode, run, then dump.

`"schema": [{"asset": "/Niagara/Modules/...", "out": "x.json"}]` describes a module asset
without a system, but it omits inputs gated by static switches, so a dump is usually more useful.

`"check": ["/Game/VFX/Systems/NS_X"]` runs compile and stack-issue checks on a system without
rebuilding it. Systems built in the same job are checked automatically.

## Engine quirks found in 5.8

- Deleting a system and recreating it at the same path in one editor session crashes on save.
  The wrapper never deletes; keep it that way.
- The stack-issue tool needs a full editor. `-run=pythonscript` (a commandlet) asserts in Slate;
  `ue_run.sh` uses `-ExecutePythonScript` for this reason.
- Tool results arrive JSON-encoded twice, and the material-instance setters in
  `MaterialEditingLibrary` return `False` even when they succeed.
- Removed emitters linger in memory for the session as `<System>:<Emitter>_<n>`; anything that
  resolves objects by path must take the highest `<n>`.
- Curve keys set through the tool do not rebuild the curve's baked lookup table, so with
  `bUseLUT` on, particles keep reading the old curve (colours and fades silently wrong). The
  wrapper writes `bUseLUT: false` with every curve.
- `-ExecutePythonScript` makes the editor exit a few frames after the script returns. Jobs fit in
  that window; anything that must tick for seconds (`ue_shot.sh`) uses `-ExecCmds="py <file>"`.
- Native enums such as `ENiagaraCoordinateSpace` take display names like any other mode input
  (`"Local"`, `"World"`, `"Simulation"`).
- Lightweight (stateless) emitters and Niagara Data Channels are not supported by the toolset.
  Avoid the `*Lightweight` system templates.
- The editor may be localised, in which case tool descriptions and enum display names come back
  translated; `ue_run.sh` passes `-culture=en` so enum display names match what you write.

## Going beyond the recipe

When an effect needs something the wrapper has no op for, extend `scripts/ue/vfx_job.py` rather
than working around it; `tool(name, payload)` calls any of the 46 system tools, and the recipe
ops are thin layers over them. Custom per-particle logic can go in an `{"hlsl": ...}` input or a
Set Parameters module. A whole custom module (Scratch Pad) cannot be authored from script; write
the HLSL and ask the user to paste it into a Scratch Pad module, then reference that module by
path from the recipe.
