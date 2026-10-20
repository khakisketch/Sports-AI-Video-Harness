"""Local previs renderer and technical QA. No external APIs or paid generation."""
import argparse
import json
import math
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / 'previs'))
from common import executable

ROOT = Path(__file__).resolve().parents[1]


def run(args, log=None):
    result = subprocess.run([str(x) for x in args], capture_output=True, text=True)
    if log:
        Path(log).write_text(result.stdout + result.stderr)
    if result.returncode:
        raise RuntimeError('명령 실패: ' + str(args[0]) + '\n' + (result.stderr + result.stdout)[-3000:])
    return result.stdout


def validate(spec):
    for key in ('width', 'height', 'fps'):
        value = spec[key]
        if type(value) is not int or not 1 <= value <= {'width': 1920, 'height': 1920, 'fps': 60}[key]:
            raise ValueError(key + ': 허용 범위 밖')
    if spec['width'] % 2 or spec['height'] % 2:
        raise ValueError('H.264용 가로·세로는 짝수여야 합니다.')
    duration = spec['duration_seconds']
    if not isinstance(duration, (int, float)) or not math.isfinite(duration) or not 0 < duration <= 60:
        raise ValueError('길이는 0초 초과 60초 이하')
    if abs(duration * spec['fps'] - round(duration * spec['fps'])) > 1e-6:
        raise ValueError('길이 × fps는 정수 프레임이어야 합니다.')
    if round(duration * spec['fps']) < 2:
        raise ValueError('움직임 시안은 최소 2프레임이 필요합니다.')
    if spec['camera'] not in ('front', 'side', 'three_quarter'):
        raise ValueError('지원하지 않는 카메라')
    framing = spec.get('camera_settings', {})
    for key in ('position', 'target'):
        if key in framing:
            value = framing[key]
            if not isinstance(value, list) or len(value) != 3 or not all(isinstance(v, (float, int)) and math.isfinite(v) for v in value):
                raise ValueError('카메라 ' + key + '는 유한한 3차원 좌표여야 합니다.')
    if 'position' in framing and 'target' in framing and framing['position'] == framing['target']:
        raise ValueError('카메라 위치와 주시점이 같을 수 없습니다.')
    scale = framing.get('ortho_scale', 3.1)
    if not isinstance(scale, (int, float)) or not math.isfinite(scale) or scale <= 0:
        raise ValueError('카메라 확대율은 양수여야 합니다.')
    for item in ([spec['mat']] if spec.get('mat') else []) + spec.get('feet', []):
        for key in ('center', 'dimensions'):
            value = item[key]
            if len(value) != 3 or not all(isinstance(v, (float, int)) and math.isfinite(v) for v in value):
                raise ValueError('매트·발의 좌표와 크기는 유한한 3차원 값이어야 합니다.')
        if any(v <= 0 for v in item['dimensions']):
            raise ValueError('매트·발의 크기는 양수여야 합니다.')
    poses = spec['poses']
    if len(poses) < 2 or poses[0]['time_seconds'] != 0 or poses[-1]['time_seconds'] != duration:
        raise ValueError('최소 2개 포즈 및 시작 0초·마지막 종료 시간 필요')
    names = set(poses[0]['joints'])
    for name, radius in spec.get('joint_radii', {}).items():
        if name not in names or not isinstance(radius, (int, float)) or not math.isfinite(radius) or radius <= 0:
            raise ValueError('잘못된 관절 반지름')
    for foot in spec.get('feet', []):
        if foot['anchor'] not in names:
            raise ValueError('발의 기준 관절이 없습니다.')
    if not names or not spec['bones']:
        raise ValueError('관절과 뼈대 필요')
    previous = -1
    for pose in poses:
        t = pose['time_seconds']
        if not math.isfinite(t) or not previous < t <= duration:
            raise ValueError('포즈 시간은 증가해야 합니다.')
        previous = t
        if set(pose['joints']) != names:
            raise ValueError('모든 포즈의 관절 이름이 같아야 합니다.')
        for xyz in pose['joints'].values():
            if len(xyz) != 3 or not all(isinstance(v, (float, int)) and math.isfinite(v) and abs(v) <= 10 for v in xyz):
                raise ValueError('관절 좌표는 유한한 3차원 미터 좌표여야 합니다.')
    for a, b in spec['bones']:
        if a not in names or b not in names or a == b:
            raise ValueError('잘못된 뼈대 연결')
        for pose in poses:
            if sum((x-y)**2 for x, y in zip(pose['joints'][a], pose['joints'][b])) < 1e-8:
                raise ValueError('길이 0인 뼈대')


def qa(video, out, expected=None):
    probe = json.loads(run([executable('ffprobe'), '-v', 'error', '-count_frames', '-show_streams', '-show_format', '-of', 'json', video]))
    streams = [s for s in probe['streams'] if s['codec_type'] == 'video']
    if not streams:
        raise ValueError('영상 스트림 없음')
    s = streams[0]
    duration = float(s.get('duration', probe['format']['duration']))
    fps_num, fps_den = map(int, s['avg_frame_rate'].split('/'))
    fps = fps_num / fps_den
    count = int(s['nb_read_frames'])
    # Decode entire video; xerror makes damaged input a failed check.
    run([executable('ffmpeg'), '-v', 'error', '-xerror', '-i', video, '-map', '0:v:0', '-f', 'null', '-'], out / 'decode.log')
    checks = {'decodes_without_error': True, 'has_frames': count > 0}
    if expected:
        checks.update({
            'resolution_matches': (s['width'], s['height']) == (expected['width'], expected['height']),
            'duration_matches': abs(duration - expected['duration_seconds']) <= 1 / expected['fps'] + 0.002,
            'fps_matches': abs(fps - expected['fps']) < 0.01,
            'frame_count_matches': count == round(expected['duration_seconds'] * expected['fps'])})
    # Evenly distributed decoded frames, with frame numbers in the report.
    indices = sorted(set(round((count-1) * i / 3) for i in range(4)))
    expression = '+'.join('eq(n\\,%d)' % n for n in indices)
    run([executable('ffmpeg'), '-v', 'error', '-i', video, '-vf', 'select=' + expression + ',scale=270:-1,tile=%dx1' % len(indices), '-frames:v', '1', '-y', out / 'contact_sheet.jpg'])
    report = {
        'video': str(video), 'created_at': datetime.now(timezone.utc).isoformat(),
        'width': s['width'], 'height': s['height'], 'fps': fps, 'duration_seconds': duration,
        'decoded_frames': count, 'codec': s['codec_name'], 'pixel_format': s.get('pix_fmt'),
        'audio_present': any(x['codec_type'] == 'audio' for x in probe['streams']),
        'checks': checks, 'technical_pass': all(checks.values()),
        'contact_sheet_frame_indices_zero_based': indices,
        'expert_motion_review': 'PENDING', 'generation_approved': False,
    }
    (out / 'qa.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    (out / 'review.md').write_text('# 프리비주얼 검수\n\n기술 검수: ' + ('통과' if report['technical_pass'] else '실패') + '\n\n![자세 프레임](contact_sheet.jpg)\n\n- [ ] 기준 자료와 동작 순서·속도 일치\n- [ ] 좌우·지지점·관절·균형 확인\n- [ ] 카메라에서 전신·핵심 관절 확인\n- [ ] 설명과 시범 일치\n- [ ] 전문가 검토자·일시·수정 사항 기록\n\n전문가 승인: 대기. 기술 검수 통과는 운동 정확성이나 유료 생성 승인이 아닙니다.\n')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    render = sub.add_parser('render')
    render.add_argument('--spec', required=True, type=Path)
    render.add_argument('--output', required=True, type=Path)
    inspect = sub.add_parser('inspect')
    inspect.add_argument('--video', required=True, type=Path)
    inspect.add_argument('--expected-spec', type=Path)
    inspect.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    spec = None
    if args.command == 'render' or args.expected_spec:
        spec = json.loads((args.spec if args.command == 'render' else args.expected_spec).read_text())
        validate(spec)
    out = args.output.resolve()
    # Preserve every prior version and reject an accidental overwrite.
    out.mkdir(parents=True, exist_ok=False)
    if args.command == 'render':
        (out / 'spec.json').write_text(json.dumps(spec, ensure_ascii=False, indent=2))
        (out / 'frames').mkdir()
        run([executable('blender'), '--background', '--factory-startup', '--python-exit-code', '1', '--python', ROOT / 'scripts/previs/blender_scene.py', '--', out / 'spec.json', out], out / 'blender.log')
        video = out / 'preview.mp4'
        run([executable('ffmpeg'), '-v', 'error', '-framerate', spec['fps'], '-start_number', '1', '-i', out / 'frames/frame_%04d.png', '-c:v', 'libx264', '-crf', '25', '-pix_fmt', 'yuv420p', '-movflags', '+faststart', '-n', video], out / 'encode.log')
    else:
        video = args.video.resolve()
    report = qa(video, out, spec)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report['technical_pass'] else 2


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (RuntimeError, ValueError, KeyError, OSError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
