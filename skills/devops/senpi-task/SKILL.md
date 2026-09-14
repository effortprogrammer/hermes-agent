---
name: senpi-task
description: Spawn senpi coding-agent sessions with OMO kibitzer memory.
version: 0.1.0
author: Hojin Yang (effortprogrammer)
license: MIT
platforms: [macos]
metadata:
  hermes:
    tags: [orchestration, coding-agent, memory]
    category: devops
---

# senpi-task Skill

Hermes가 senpi 코딩 에이전트를 자식 프로세스로 spawn하고, 프로젝트별 OMO `.memory/` git store(kibitzer sidecar 포함)가 자동으로 판정·주입·추출하게 한다. 2026-09-14 MVP에서 E2E 검증됨(nudge 주입 → 행동 변화 → write-back). Phase 2(running 세션 steer)는 미구현.

## When to Use

- 코딩 task를 senpi 자식에 위임할 때 (Hermes 본체가 직접 코딩하지 않을 때)
- 프로젝트별 기억 격리가 필요한 병렬 코딩 세션 운영
- OMO store의 기억을 Hermes 본체 대화에서 회상할 때 (UC-4)

## Prerequisites

- `senpi` CLI (2026.9.7+): `npm i -g @code-yeongyu/senpi`
- OMO plugin 설치됨: `~/Projects/oh-my-openagent` 클론 → `bun install --frozen-lockfile` → `bun run build:senpi-plugin:native` → `senpi install ~/Projects/oh-my-openagent/packages/omo-senpi/plugin`
- zai auth: `~/.pi/agent/auth.json`에 api_key (Hermes `.env`의 `ZAI_API_KEY`에서, chmod 600, 값 노출 금지)

## How to Run

`terminal`로:

```bash
cd <project-root>
OMO_MEMORY_HOME="$PWD/.memory" senpi -p --model zai/glm-5.3 "<task 지시>"
```

## Quick Reference

- 세션 JSONL: `~/.senpi/agent/sessions/<cwd-slug>/<timestamp>_<id>.jsonl`
- store: `<project>/.memory/agents/<id>/repo/` (plain markdown + git)
- nudge 증명: JSONL의 `omo-kibitzer:recall` / `omo-kibitzer:nudged` 엔트리
- wake 레코드: `<store>/agents/<id>/runtime/recall/sidecars/<session>/wakes.ndjson`

## Procedure

1. **검사**: 대상 프로젝트의 `.gitignore`에 `.memory/`가 있는지, `.omo/omo.jsonc`에 `{"memory": {"agent": "<project-name>"}}`가 있는지 확인. 없으면 추가.
2. **초기 시딩 필요 시**: `cd ~/Projects/oh-my-openagent && SEED_STORE_ROOT=<project>/.memory bun run seed-memory.mts` — memory-core의 `runMemoryTool`(create) 경유. 파일 내용에는 frontmatter를 넣지 말고 `description` param만 준다(자동 생성).
3. **identity 확인**: explicit 이름도 `<name>-<shorthash>`로 변환됨(`memory-core/src/identity/resolve.ts` `deriveExplicitId`). 실제 id는 `search_files`로 `<store>/agents/` 디렉토리 조회 또는 세션 JSONL의 `senpi-memory.session-binding`에서. 시딩은 변환된 id에.
4. **spawn**: How to Run의 커맨드. `OMO_MEMORY_HOME` 누락 금지(기본 `~/.omo/memory`로 새어 나감).
5. **검증**: Quick Reference의 JSONL에서 `omo-kibitzer:recall` 존재 + nudge 시각 이후 tool_call 행동 변화 확인.

## Pitfalls

- **one-shot transient 라우팅**: headless `-p`는 transient-run으로 가지만, `<store>/agents/<id>/repo/`가 이미 있으면 durable 바인딩(`transient-identity.ts` `isDurableIdentityRoot`). 시딩 먼저, spawn 나중.
- **dirty repo 에러**: 시딩 실패 시 staged 파일이 남는다. `git -C <repo> restore --staged .` 후 대상 파일 삭제하고 재시도.
- **쓰기 금지**: Hermes는 store에 read-only(I-2). write는 항상 OMO wrapper 경유. 자식이 `memory` tool/facts로 스스로 기록한다.
- **kibitzer judge 과금**: `memory.recall.category`(기본 `quick`)의 저가 체인에 고정 — main 모델과 별도 비용 없음.
- reflection launch "ctx is stale" 경고는 one-shot 종료 시점의 benign 메시지.
- **RPC 자식은 백그라운드 foreground 루프를 선호**: "background로 돌려" 지시에 `&`를 붙이면 턴이 즉시 끝나 steer 타이밍이 사라진다. "FOREGROUND로, `&` 없이"라고 명시할 것.
- **steer는 즉시 tool을 중단하지 않는다**: 세션 steering 큐에 적재되어 **턴 경계에서 합류**한다. 실증: 8회 루프 중 4회 시점 steer → `count.txt = [1,2,3,4,MARKER]`.

## RPC orchestration (UC-5/UC-6) — Phase 2 E2E 검증됨

장기 실행 세션 steer + Hermes 재시작 후 재부착. `terminal`의 `background` 실행으로 `scripts/rpc_orchestrator.py` 구동 (총 5-8분).

```python
child = RpcChild()  # senpi --mode rpc --multi-session, cwd=<project>, OMO_MEMORY_HOME 지정
opened = child.send("open_session", cwd=LAB, provider="zai", modelId="glm-5.3")  # routing handle 반환
child.send("prompt", sessionId=rsid, message=task)      # 턴 시작
# busy 감지: get_state → isStreaming 폴링 (이벤트 아님)
child.send("steer", sessionId=rsid, message="...")      # mid-run 주입
# 복구: list_sessions → sessionPath 확보 → 자식 kill → 새 RpcChild → open_session(sessionPath=...)
```

- **routing handle vs durable id**: `open_session` 응답의 `data.sessionId`는 프로세스 생애 전용 라우팅 핸들, `data.state.sessionId`가 durable JSONL id. 재부착은 `sessionPath`로.
- **busy 감지**: `get_state` 폴링 (`isStreaming`). 세션 부팅에 최대 2분 여유.
- **검증 증거 구조**: `scripts/p2-evidence-example.json` (protocol/open/task/steer/recovery 페이즈).

## Verification

spawn 후 세션 JSONL에서:

```bash
grep -o "omo-kibitzer:[a-z]*" <session>.jsonl | sort | uniq -c
# 기대: 1 omo-kibitzer:nudged / 1 omo-kibitzer:recall
```

`wakes.ndjson`의 `status: "completed"` + `nudged` 배열에 시딩 path가 있으면 E2E 통과.
