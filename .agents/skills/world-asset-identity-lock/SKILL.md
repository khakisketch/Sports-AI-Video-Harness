---
name: world-asset-identity-lock
description: Use for recurring characters, props, creatures, costumes, and environments that must keep stable identity across multi-shot generation, world assets, and continuation runs.
tags:
  - identity
  - continuity
  - worldbuilding
  - character-consistency
  - asset-lock
agents:
  allow:
    - planner
    - script_writer
    - image_designer
    - image_edit_agent
    - video_designer
    - flf_video_designer
---
# World Asset Identity Lock

Use this skill when recurring elements must not drift.

## Identity Lock Rules
- Assign stable ids and reuse them everywhere.
- Separate immutable traits from variable shot-specific behavior.
- Lock face, silhouette, costume logic, material response, palette, and behavioral signature for recurring heroes.

## Prompt Rules
- Reuse the same identity core before adding per-shot actions.
- Keep location names and spatial anchors stable across adjacent clips.
- For props and creatures, lock outline, scale, surface logic, and signature motion.

## Continuation Rules
- New shots inherit the last confirmed stable state unless the script explicitly changes it.
- If transformation occurs, describe the transition from prior state to next state, not two unrelated identities.

## Avoid
- renaming recurring assets
- changing silhouette or palette without cause
- vague continuity language
