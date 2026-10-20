# 기존 자산으로 보는 로컬 미리보기

저장소 루트 AGENTS.md와 docs/외부스킬-운영.md를 우선한다. 작업별 도구는 docs/도구역할-선택기준.md에 따라 선택하며 Higgsfield는 선호 제작 플랫폼이다. 이 폴더는 기존 자산을 조합하는 HyperFrames 검토 화면이다.

전문 품질 판정은 저장소 루트의 `.agents/skills/preproduction-review/SKILL.md`와 `docs/생성전-품질검토.md`를 따른다. 실제 관찰·반례·근거·결함 재검수가 필요하며 기술 통과와 내용 통과를 구분한다. 원본 미리보기는 고치지 않고 검토 기록만 추가하는 요청도 구분한다.

- `.agents/skills/hyperframes/SKILL.md`, hyperframes-core·cli의 해당 지침을 읽는다. 설치 경로는 저장소 루트 기준이다.
- 신규 이미지·AI 영상·음성 생성, TTS, 계정 연결, 업로드, publish, cloud·lambda·cloudrun은 현재 범위 밖이다. 공식 Studio 버튼의 존재는 실행 승인으로 해석하지 않는다.
- 저장소 루트에서 `python3 scripts/hyperframes.py --project <이 폴더> preview --background`와 `check --json --snapshots`를 쓴다. 실행기는 로컬 CLI·JS·이미지·동영상·폰트를 사용한다.
- 자동 스킬 업데이트·외부 자산 검색·음성 추천에 따른 생성·피드백 전송을 수행하지 않는다. 버전은 config/external-skills.lock.json과 tools/hyperframes/pnpm-lock.yaml로 고정한다.
- 기준 계획은 build-manifest.json의 plan 경로다. 대본·동작·순서를 바꾸면 계획에도 반영한다. 기존 승인·검토본을 덮지 않는다.
- 첫 동작 한 주기는 자르지 않는다. 가상 강사 정지 이미지는 외형 참고, Blender 마네킹은 동선 참고다. 운동 전문가 승인을 대신하지 않는다.
- HTML 수정 전 `history begin --who codex --label ...`, 수정 후 `history end`로 변경을 기록한다. 기존 사용자 수정을 먼저 확인한다.
- 재생·되감기·지지점이 보이는 구도·한국어 글리프를 실제 브라우저와 스냅샷으로 확인한다. check의 통과만으로 고객 제시나 운동 정확성을 승인하지 않는다.
- MP4 내보내기가 필요해지면 현재 요청 범위와 최종 미리보기 채택을 먼저 대조한다. 이번 시험은 Studio 미리보기와 스냅샷으로 끝낸다.
