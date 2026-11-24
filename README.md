# unreal-vfx

A Claude skill for building Unreal Engine VFX the way most game effects are made: quads, a small set of textures, and per-particle tricks in Niagara. It scripts the whole pipeline headlessly from one JSON job file, so effects are built and tuned by editing numbers instead of clicking through the Niagara editor.

- `SKILL.md`: the skill. Workflow, running jobs, textures, materials, the Niagara recipe format, and what a run does and does not prove.
- `scripts/vfx_tex.py`: packs PNGs into a SubUV atlas and generates starter shapes (glow, ring, spark, tiling noise). Standard library only, so it runs on Unreal's bundled Python.
- `scripts/ue_run.sh`: runs a job in a headless editor and prints the result.
- `scripts/ue_shot.sh`: renders systems offscreen to PNG stills, so the effect can be looked at.
- `scripts/ue/vfx_job.py`: the Unreal-side runner. Imports textures, generates four master particle materials and their instances, creates or rebuilds Niagara systems, then checks compile state and stack errors.
- `references/`: the recipe format and engine quirks, effect design notes with starting values, and how to spawn and drive the effects from gameplay code.
- `examples/impact/job.json`: a four-layer impact effect (flash, ring, sparks, smoke).

## Requirements

- Unreal Engine 5.8. The Niagara scripting relies on the engine's experimental `NiagaraToolsets` and `ToolsetRegistry` plugins, which ship with 5.8. The launcher enables them for each run only; your `.uproject` is not modified.
- Windows with Git Bash, for `ue_run.sh`.

## Install

Copy this folder to `~/.claude/skills/unreal-vfx`.

## Try the example

```bash
PY="C:/Program Files/Epic Games/UE_5.8/Engine/Binaries/ThirdParty/Python3/Win64/python.exe"
cp -r examples/impact /path/to/Project/Tools/vfx/impact && cd /path/to/Project/Tools/vfx/impact
mkdir tex
"$PY" -I ~/.claude/skills/unreal-vfx/scripts/vfx_tex.py pack tex/smoke_atlas.png /path/to/smoke_0[1-8].png --cols 4 --cell 256
for s in glow ring spark noise; do "$PY" -I ~/.claude/skills/unreal-vfx/scripts/vfx_tex.py shape $s tex/$s.png; done
bash ~/.claude/skills/unreal-vfx/scripts/ue_run.sh /path/to/Project/Project.uproject job.json
```

The smoke atlas needs eight same-sized smoke puff PNGs with the shape in alpha; [Kenney's Particle Pack](https://kenney.nl/assets/particle-pack) (CC0) works. No textures are included in this repo.

## Status

Tested on UE 5.8 on Windows: the example builds on a new project and passes Niagara's compile and stack-issue checks. A job run proves an effect compiles, not how it looks; use `ue_shot.sh` to render stills and tune against them. Epic marks the toolset plugins experimental, so they may change in later engine versions.

## License

MIT. See `LICENSE`.
