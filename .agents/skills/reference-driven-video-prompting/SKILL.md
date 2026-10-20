---
name: reference-driven-video-prompting
description: Use for image-to-video, reference-video continuity, keyframe-driven prompting, camera control, and consistency workflows based on official Runway, Kling, and character/style reference patterns.
tags:
  - image-to-video
  - reference-video
  - keyframes
  - camera-control
  - consistency
agents:
  allow:
    - planner
    - image_designer
    - image_edit_agent
    - video_designer
    - flf_video_designer
---
# Reference Driven Video Prompting

Use this skill when the workflow depends on image inputs, prior videos, keyframes, or reference-based consistency.

## Source Basis
- Runway Gen-3 prompting, keyframes, and camera control docs.
- Kling image-to-video prompt pattern.
- Midjourney character/style reference concepts for identity/style persistence.

## Reference Hierarchy
1. identity reference
2. world/location reference
3. previous formal shot continuity
4. style reference
5. motion prompt

## Prompting Rules
- With an input image, focus text on desired motion and camera behavior rather than re-describing the full image.
- Use direct, descriptive motion language; avoid conceptual chatter.
- Separate subject motion from camera motion.
- When using keyframes, define what must transform between first/middle/last frame.

## Consistency Rules
- Character references preserve face, hair, clothing silhouette, and recurring identity traits.
- Style references preserve palette, texture, light behavior, and rendering attitude.
- Previous video references preserve action phase, movement direction, and world continuity.

## Camera Control Rules
- Choose one dominant camera move per shot unless complexity is essential.
- If the subject moves toward camera, note whether the camera compensates, holds, or retreats.
- Do not overload a shot with contradictory camera verbs.

## Good Motion Language
- sways gently in wind
- advances in heavy armor with restricted weight
- camera slowly pushes in as expression hardens
- orbit tightens during transformation

## Avoid
- vague commands with no motion target
- multiple unrelated action verbs in one short shot
- re-randomizing identity when continuity references already exist
