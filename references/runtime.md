# Using the effects from gameplay

The function names below were checked against the UE 5.8 headers
(`<UE>/Engine/Plugins/FX/Niagara/Source/Niagara/Public/NiagaraFunctionLibrary.h` and
`.../Public/NiagaraComponent.h`). On another engine version, grep those headers for the name
before relying on it; Niagara's API moves between releases.

## Setup

- The `Niagara` plugin must be enabled in the `.uproject`, and a C++ module that spawns effects
  needs `"Niagara"` in its `Build.cs` dependencies.
- Reference systems through a soft pointer or a data table of soft references rather than hard
  paths in code, so effects can be swapped without recompiling.

## Spawning

Pick by ownership and lifetime; do not stack mechanisms.

| Situation | Use |
|---|---|
| One-shot in the world (impact, explosion) | `UNiagaraFunctionLibrary::SpawnSystemAtLocation` |
| One-shot that follows something (muzzle flash on a socket) | `UNiagaraFunctionLibrary::SpawnSystemAttached` |
| Part of an actor for its whole life (trail, aura, idle glow) | a `UNiagaraComponent` on the actor |
| Tied to an animation frame | a Niagara anim notify on the montage or sequence |
| Driven by gameplay tags with GAS | a Gameplay Cue |

Repeating one-shots should be pooled: pass the auto-release pool method to the spawn call
(`ENCPoolMethod::AutoRelease`). Without it every spawn allocates a component. Effects only
return to the pool when they complete, which is why one-shot emitters need `Loop Behavior: Once`.

Sketch:

```cpp
#include "NiagaraFunctionLibrary.h"
#include "NiagaraComponent.h"

UNiagaraComponent* Fx = UNiagaraFunctionLibrary::SpawnSystemAtLocation(
    this, ImpactSystem, Hit.ImpactPoint, Hit.ImpactNormal.Rotation(),
    FVector(1.f), /*bAutoDestroy*/ true, /*bAutoActivate*/ true, ENCPoolMethod::AutoRelease);
if (Fx)
{
    Fx->SetVariableLinearColor(TEXT("Tint"), SurfaceTint);
}
```

## Driving user parameters

Whatever the recipe lists under `"user"` is the effect's interface.

- Pass the bare name (`"Tint"`), without the `User.` prefix.
- The type must match exactly. A wrong-type set is a silent no-op, not an error. World-space
  points are `position`; directions, offsets and velocities are `vec3`.
- Set parameters right after spawning, before the first tick, or the first frame uses defaults.
- For values that change every frame (beam end point, charge amount), keep the component and set
  the parameter each tick instead of respawning.

Good parameters to expose: `Tint`, `Scale`, `Intensity` (multiplies spawn counts), `Direction` or
`Normal` (aims a cone), `EndPoint` (beams), `Radius` (area effects).

## A data-driven effect table

A table that maps a gameplay event to system, sound and decal keeps effects out of code:

| Row | System | Scale | Tint | Sound |
|---|---|---|---|---|
| `Impact.Metal` | `NS_Impact` | 1.0 | orange | metal ping |
| `Impact.Flesh` | `NS_Impact` | 0.8 | red | thud |

One system retinted and rescaled covers many surfaces, which is the point of exposing user
parameters in the recipe.

## Orientation conventions

Niagara's templates treat the system's local +X as forward (cones, directional bursts). Spawn an
impact with its rotation built from the surface normal so +X points away from the surface, and
build directional layers along X in the recipe. Check one in the editor before assuming.
