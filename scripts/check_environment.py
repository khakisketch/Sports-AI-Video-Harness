"""로컬 실행 도구만 조회한다. 설치·외부 연결·유료 호출은 하지 않는다."""
import json
import hashlib
import shutil
from pathlib import Path
from hyperframes import node_path
from media_runtime import media_binary

root = Path(__file__).resolve().parents[1]
blender_app = Path('/Applications/Blender.app/Contents/MacOS/Blender')
tools = {name: shutil.which(name) for name in ('python3', 'blender', 'ffmpeg', 'ffprobe')}
for name in ('ffmpeg', 'ffprobe'):
    try:
        tools[name] = media_binary(name)
    except RuntimeError:
        tools[name] = None
if not tools['blender'] and blender_app.is_file():
    tools['blender'] = str(blender_app)
series = json.loads((root / 'config/series.json').read_text(encoding='utf-8'))
production = series.get('production', {})
try:
    tools['node_for_preview'] = str(node_path())
except RuntimeError:
    tools['node_for_preview'] = None
lock_path = root / 'config/external-skills.lock.json'
skill_checks = []
if lock_path.is_file():
    locked = json.loads(lock_path.read_text())
    for source in locked['sources']:
        for skill in source['skills']:
            path = root / skill['path'] / 'SKILL.md'
            skill_checks.append({'name': skill['name'], 'installed': path.is_file(), 'matches_locked_skill': path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == skill['skill_sha256']})
    for skill in locked.get('project_skills', []):
        path = root / skill['path'] / 'SKILL.md'
        skill_checks.append({'name': skill['name'], 'ownership': 'project', 'installed': path.is_file(), 'matches_locked_skill': path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == skill['skill_sha256']})
runtime_path = root / 'tools/hyperframes/node_modules/hyperframes/package.json'
print(json.dumps({
    'local_tools': tools,
    'instructor_approved': series['instructor']['approved'],
    'style_approved': series['style']['approved'],
    'character_appearance_adopted': production.get('instructor_candidate', {}).get('image_approved', False),
    'preferred_voice_confirmed': production.get('voice_candidate', {}).get('confirmed_usable', False),
    'preproduction': production.get('preproduction', {}),
    'interaction': {
        'legacy_config_key': 'production.consultation',
        'scope': production.get('interaction_scope'),
        'configured_paths': production.get('consultation', {}),
        'all_configured_files_present': bool(production.get('consultation')) and all(
            (root / path).is_file() for path in production.get('consultation', {}).values()
        ),
        'behavior_validation': '경로·스킬 설치 확인이며 실제 사용자와의 전체 제작 진행 품질을 자동 판정하는 것은 아님',
    },
    'planning_checker_present': (root / 'scripts/production.py').is_file(),
    'project_skills': skill_checks,
    'hyperframes_runtime_version': json.loads(runtime_path.read_text())['version'] if runtime_path.is_file() else None,
    'preview_wrapper_present': (root / 'scripts/hyperframes.py').is_file(),
    'quality_checker_present': (root / 'scripts/quality.py').is_file(),
    'quality_rubric_present': (root / 'config/quality-rubric.json').is_file(),
    'tool_routing_config_present': (root / 'config/tool-routing.json').is_file(),
    'tool_routing_guide_present': (root / 'docs/도구역할-선택기준.md').is_file(),
    'external_connections': '이번 로컬 점검에서는 조회하지 않음. 실제 선택한 서비스의 읽기 도구와 현재 입력으로 별도 확인',
    'missing_optional_tools': [name for name in ('blender', 'ffmpeg', 'ffprobe') if not tools[name]],
}, ensure_ascii=False, indent=2))
