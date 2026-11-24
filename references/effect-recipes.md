# Designing effects from quads and textures

Contents: the idea · layers · per-particle tricks · material tricks · effect anatomy with
starting values · timing · budgets · when quads are not enough

The numbers here are starting points for a character-scale effect (roughly 1 to 3 m), in
centimetres and seconds. Scale sizes and speeds together when the effect is bigger or smaller.
They are a place to begin tuning, not measured values.

## The idea

A particle is a textured quad. What makes it read as fire, smoke or a spark is not the texture
alone but what each quad does over its short life: where it starts, how fast it leaves, how it
grows, spins, changes colour and dissolves, and how its timing sits against the other layers.
A small texture set covers a great deal:

| Texture | Makes |
|---|---|
| soft glow (radial falloff) | flashes, light blooms, cores, soft sparks, embers |
| streak / spark | sparks and debris trails (stretched along velocity), speed lines |
| ring | shockwaves, ripples, charge-up rings, ground decals |
| smoke puffs, 4 to 8 variations in an atlas | smoke, dust, steam, clouds, with random frame per particle |
| flame or wisp shapes in an atlas | fire, magic, energy |
| tiling noise | scrolled through panner materials for beams, auras, trails, heat |
| hard chips or shards | debris, sparks with a silhouette |

## Layers

Build an effect as layers with different jobs and different lifetimes. Short bright layers sell
the hit; slower dark layers give it weight and an aftermath.

| Layer | Job | Typical life | Blend |
|---|---|---|---|
| Flash | one or two big glow quads, the first frame of impact | 0.05 to 0.15 s | additive |
| Core | the hot body: fire puffs or an energy ball | 0.2 to 0.5 s | additive, or alpha with high emissive |
| Ring / shockwave | one quad with a ring texture scaling up fast | 0.2 to 0.4 s | additive |
| Sparks | many small streaks flying out, gravity and drag | 0.3 to 0.8 s | additive |
| Smoke / dust | puffs that expand, drift, erode | 0.8 to 3 s | alpha |
| Debris | chips with gravity, optional bounce | 0.6 to 1.5 s | alpha or mesh |
| Light | one light, very short | 0.1 to 0.3 s | light renderer (skip on mobile) |
| Scorch | ground decal or flat quad that lingers | seconds | alpha or decal |

Not every effect needs every layer. A hit spark is flash + sparks. A dust landing is dust + ring.

## Per-particle tricks

These are what turn a few textures into many looks. Each maps to something in the recipe.

- **Random frame from an atlas.** `SubImageSize` on the renderer plus `SubUVAnimation` with
  `"SubUV Animation Mode": "Random"`. The cheapest way to stop a burst looking like copies.
- **Random rotation and flip.** `"Sprite Rotation Mode": "Random"` on `InitializeParticle`, plus
  `SpriteRotationRate` with a signed range so puffs turn both ways.
- **Random size and lifetime.** Always a range, never one value. Uniform lifetimes make a burst
  blink out all at once.
- **Size over life.** Fast early growth then slow: `[[0, 0.3], [0.15, 1], [1, 1.6]]`. Smoke that
  does not grow looks like a sticker.
- **Colour and alpha over life.** Fade in over the first 5 to 10 percent so particles do not pop,
  and fade out to zero. Fire runs white, yellow, orange, dark red, then becomes smoke.
- **Erosion instead of fading.** Drive Erode (`Index 0 Param 1` of `DynamicMaterialParameters`)
  from 0 to 1 late in life. The shape eats away from its thin edges, which reads as dissipating
  where a plain alpha fade reads as a ghost. Combine with a gentler alpha fade.
- **Velocity alignment and stretch.** Renderer `"Alignment": "VelocityAligned"` with a
  non-uniform sprite size (`[30, 4]`) makes a glow or streak into a spark. `ScaleSpriteSizeBySpeed`
  stretches fast particles.
- **Drag.** High initial speed plus strong drag (2 to 6) gives the punch-then-hang motion of
  explosions. Without drag everything drifts at constant speed and feels weightless.
- **Gravity with sign.** Negative Z for sparks and debris, slightly positive for hot smoke and
  embers.
- **Curl noise.** `CurlNoiseForce` at low strength makes smoke and magic wander. Too much looks
  like a swarm.
- **Camera offset.** `CameraOffset` pulls a flash toward the camera so it does not clip into the
  wall it hit.
- **Emissive over 1.** Colours above 1 (for example `[4, 2, 0.5, 1]`) bloom. This is the
  difference between orange and hot. Keep alpha-blended smoke at or below 1.
- **Inherited colour through a user parameter.** One `User.Tint` linked into several layers
  retints the whole effect from gameplay.
- **Spawn shape.** `ShapeLocation` (sphere surface, ring, box, cone) decides the silhouette of a
  burst before anything moves.
- **Staggered start.** A `Spawn Time` or loop delay of 0.03 to 0.1 s on later layers lets the
  flash land first.

## Material tricks

The four generated masters cover these:

- **Additive vs alpha.** Additive only brightens, so it never looks like smoke and it washes out
  over bright backgrounds. Alpha can darken and occlude but costs sorting. Fire usually needs
  both: an alpha body with an additive core.
- **Soft particles** (`SoftParticles`, `SoftDistance`). Fades the quad where it meets geometry so
  the hard intersection line disappears. Turn it off on mobile and for tiny particles.
- **Flipbook with frame blending.** `bSubImageBlend` on the renderer smooths a low frame count.
- **Two scrolling layers times a mask** (the panner masters). A noise texture scrolled at two
  speeds and multiplied flickers like energy; the mask gives it a silhouette (a soft line for a
  beam, a ring for an aura). On a ribbon it is a trail or beam; on a single stretched quad it is
  a slash or a pillar; on a ring mesh it is a shockwave band.
- **Erode on a panner.** Driving Erode on a trail or slash makes it break up along the noise
  instead of fading evenly.

Write a new master, by extending `_build_sprite` / `_build_panner` in `scripts/ue/vfx_job.py`,
when you need something these do not have: UV distortion from a second texture, a colour ramp
lookup from greyscale, polar coordinates for radial scrolling, vertex offset, refraction, or lit
smoke. Keep the dynamic parameter layout (x Erode, y Boost) so recipes stay interchangeable, and
use the spare z and w for the new feature.

## Effect anatomy with starting values

### Impact / hit spark (bullet, sword, punch)
- Flash: 1 glow, life 0.08 to 0.12, size 150 to 250, scale 0.3 to 1.4.
- Sparks: 12 to 30, life 0.25 to 0.6, speed 300 to 900 from a point or in a cone along the
  surface normal, gravity -980, drag 2, velocity aligned, size `[30, 4]`, colour `[4, 2, 0.5]`.
- Optional dust: 4 to 8 puffs, life 0.6 to 1.2, size 60 to 120, speed 40 to 140, drag 1.5.
- Optional ring: 1 ring, life 0.25 to 0.35, scale 0.2 to 3.
- `examples/impact/job.json` is this effect.

### Explosion
- Flash: 1 or 2 glows, life 0.1, size 600 to 1000.
- Fireball: 10 to 20 flame puffs from an atlas, life 0.4 to 0.8, speed 200 to 600 with drag 4,
  size 150 to 300 growing to 1.8x, colour curve white-yellow to orange to dark red, erode late.
- Smoke: 12 to 20 puffs, starts 0.05 s later, life 1.5 to 3, speed 100 to 300 with drag 3,
  gravity +30 to +80, size 200 to 400 growing to 2.5x, colour dark grey, alpha in fast and out
  slow, erode over the last half.
- Sparks: 30 to 60, life 0.5 to 1.2, speed 600 to 1500, gravity -980, drag 1.
- Shockwave: 1 ring, life 0.3, scale to 6x. Ground variant: a flat quad facing up.
- Light: one, life 0.2, only where the platform affords it.

### Muzzle flash
- Flash: 1 or 2 muzzle shapes from an atlas (random frame), life 0.04 to 0.06, local space,
  aligned to the barrel, random roll.
- Glow: 1 soft glow, life 0.05.
- Smoke wisp: 2 or 3 puffs, life 0.4 to 0.8, small, forward drift.
- Sparks: 3 to 8 short ones.
- Keep it in local space so it stays on the gun.

### Smoke column / campfire smoke (looping)
- `Fountain` or `SimpleSpriteBurst` switched to `SpawnRate` 6 to 15 per second, `Loop Behavior`
  Infinite.
- Life 2 to 4, initial upward speed 80 to 150, gravity +20, curl noise low, size 80 growing to
  3x, random rotation with slow spin, alpha 0 to 0.5 to 0, erode over the last 40 percent.

### Fire (looping)
- Flames: 15 to 30 per second from a flame atlas, life 0.5 to 0.9, upward 100 to 200, size 60 to
  120 shrinking toward the end, colour yellow-white to orange to red, additive or alpha with
  emissive 3 to 6.
- Embers: 3 to 8 per second, glow texture, tiny (4 to 8), life 1 to 2, upward with curl noise.
- Smoke as above, starting where the flames end.

### Magic projectile and trail
- Core: `SingleLoopingParticle` glow, emissive 5+, slight size pulse.
- Trail: `LocationBasedRibbon` with a `panner_add` instance (noise scrolling along U, soft line
  mask), width tapering over ribbon age.
- Sparkles: 20 to 40 per second, tiny glows shed behind, life 0.3 to 0.6, small random velocity.
- Impact: the impact recipe, tinted with the same `User.Tint`.

### Slash / melee arc
- One quad or a curved mesh card with a `panner_add` instance: streak texture scrolled across
  U, arc mask, Erode from 0 to 1 over 0.15 to 0.25 s so it tears away along the swing.
- A few sparks along the arc. Life under 0.3 s overall; slashes must be fast.

### Shockwave / ground ring
- 1 ring quad facing up (set the renderer facing or use a mesh plane), life 0.3 to 0.5, scale
  0.2 to 5 with a cubic ease-out, alpha fading from mid-life, plus a dust ring: 12 to 20 puffs
  spawned on a ring shape moving outward with heavy drag.

### Aura / pickup glow (looping)
- 1 persistent glow with a slow size pulse, a `panner_add` ring or column with slow scroll, and
  a few rising motes (`HangingParticulates` or low `SpawnRate`).

## Timing

Readability comes from contrast in time. Bright layers are over in a few frames; dark layers
linger. If an effect feels weak, shorten and brighten the flash and add drag before adding
particles. If it feels noisy, cut counts and lengthen the smoke. One-shot effects should have
`Loop Behavior` set to `Once` on every emitter so the system completes and can be pooled.

## Budgets

Overdraw (how many translucent layers cover a pixel) is the real cost, not particle count.

- Mobile: under about 50 particles alive per effect, 3 to 4 emitters, no lights, soft particles
  off, large quads brief. Prefer one large quad with a good texture to many overlapping ones.
- Desktop and console: hundreds per effect is fine on CPU; move an emitter to GPU sim only when
  it sustains well over a hundred particles, and give it fixed bounds.
- Fewer emitters beats fewer particles: one emitter can feed several renderers.
- Bounds: set `CalculateBoundsMode` to `Fixed` with sensible `FixedBounds` on effects that are
  spawned often; dynamic bounds are recalculated every frame.

## When quads are not enough

Reach past sprites deliberately: mesh particles for chunky debris and hero shapes (a puff with
real volume lights correctly from every side, which mattered for a mushroom cloud), ribbons for
anything that must connect over distance, and a custom master material when the look depends on
distortion or lighting. Say so when you make that call, because it costs more to author and
to render.
