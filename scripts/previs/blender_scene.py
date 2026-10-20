"""Run only through Blender --background --python; procedural joint mannequin."""
import json
import math
import sys
from pathlib import Path
import bpy
from mathutils import Vector

spec_path, output_path = sys.argv[sys.argv.index('--') + 1:]
spec = json.loads(Path(spec_path).read_text())
out = Path(output_path)
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)
scene = bpy.context.scene
scene.render.engine = 'BLENDER_WORKBENCH'
scene.display.shading.light = 'STUDIO'
scene.display.shading.color_type = 'OBJECT'
scene.display.shading.show_shadows = True
scene.display.shading.show_cavity = True
scene.display.shading.background_type = 'WORLD'
scene.world.color = (0.12, 0.14, 0.17)
scene.render.resolution_x = spec['width']
scene.render.resolution_y = spec['height']
scene.render.resolution_percentage = 100
scene.render.fps = spec['fps']
scene.frame_start = 1
scene.frame_end = round(spec['duration_seconds'] * spec['fps'])
scene.render.image_settings.file_format = 'PNG'
scene.render.filepath = str(out / 'frames' / 'frame_')
scene.view_settings.view_transform = 'Standard'

joints = {}
for name, position in spec['poses'][0]['joints'].items():
    radius = spec.get('joint_radii', {}).get(name, 0.15 if name == 'head' else 0.045)
    bpy.ops.mesh.primitive_uv_sphere_add(segments=12, ring_count=6, radius=radius, location=position)
    obj = bpy.context.object
    obj.name = name
    # Anatomical left/right are explicit and consistent across views.
    obj.color = (0.2, 0.55, 0.9, 1) if name.startswith('left') else ((0.95, 0.5, 0.15, 1) if name.startswith('right') else (0.7, 0.72, 0.75, 1))
    joints[name] = obj

if spec.get('mat'):
    mat = spec['mat']
    bpy.ops.mesh.primitive_cube_add(size=1, location=mat['center'])
    bpy.context.object.name = 'exercise_mat'
    bpy.context.object.dimensions = mat['dimensions']
    bpy.context.object.color = (0.12, 0.30, 0.34, 1)

for foot in spec.get('feet', []):
    bpy.ops.mesh.primitive_cube_add(size=1, location=foot['center'])
    bpy.context.object.name = foot['name']
    bpy.context.object.dimensions = foot['dimensions']
    bpy.context.object.color = joints[foot['anchor']].color

links = []
for a, b in spec['bones']:
    bpy.ops.mesh.primitive_cylinder_add(vertices=12, radius=0.035, depth=1)
    obj = bpy.context.object
    obj.name = a + '__' + b
    obj.color = joints[a].color
    links.append((a, b, obj))

for pose in spec['poses']:
    frame = 1 + round(pose['time_seconds'] * (scene.frame_end - 1) / spec['duration_seconds'])
    for name, xyz in pose['joints'].items():
        joints[name].location = xyz
        joints[name].keyframe_insert(data_path='location', frame=frame)
    for a, b, obj in links:
        start, end = Vector(pose['joints'][a]), Vector(pose['joints'][b])
        delta = end - start
        obj.location = (start + end) / 2
        obj.rotation_euler = delta.to_track_quat('Z', 'Y').to_euler()
        obj.scale = (1, 1, delta.length)
        for prop in ('location', 'rotation_euler', 'scale'):
            obj.keyframe_insert(data_path=prop, frame=frame)

# Evaluate endpoints on each frame so all segments remain joined during interpolation.
for frame in range(1, scene.frame_end + 1):
    scene.frame_set(frame)
    for a, b, obj in links:
        delta = joints[b].location - joints[a].location
        obj.location = (joints[a].location + joints[b].location) / 2
        obj.rotation_euler = delta.to_track_quat('Z', 'Y').to_euler()
        obj.scale = (1, 1, delta.length)
        for prop in ('location', 'rotation_euler', 'scale'):
            obj.keyframe_insert(data_path=prop, frame=frame)

bpy.ops.mesh.primitive_plane_add(size=200)
bpy.context.object.color = (0.22, 0.24, 0.27, 1)
bpy.ops.object.camera_add()
camera = bpy.context.object
positions = {'front': (0, -7, 1.2), 'side': (7, 0, 1.2), 'three_quarter': (4, -7, 2.6)}
framing = spec.get('camera_settings', {})
camera.location = framing.get('position', positions[spec['camera']])
camera.rotation_euler = (Vector(framing.get('target', (0, 0, 1))) - camera.location).to_track_quat('-Z', 'Y').to_euler()
camera.data.type = 'ORTHO'
camera.data.ortho_scale = framing.get('ortho_scale', 3.1)
scene.camera = camera
scene.frame_set(1)
bpy.ops.wm.save_as_mainfile(filepath=str(out / 'scene.blend'))
bpy.ops.render.render(animation=True)
