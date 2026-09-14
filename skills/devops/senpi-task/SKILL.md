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

## Multi-project isolation (Phase 3 E2E 검증됨)

병렬 코딩 세션 운영 규칙:

- **프로젝트당 하나의 RPC 호스트**: `OMO_MEMORY_HOME`은 프로세스 env이므로 프로젝트별로 `make_child(<project>)` 패턴으로 호스트를 분리한다 (`scripts/`의 P3 드라이버 참고). 하나의 호스트에 서로 다른 cwd 세션을 여는 것은 **격리 파괴**.
- **auto identity 주의**: 시딩 전에 반드시 실제 세션이 바인딩될 identity를 먼저 확보한다 — 가장 확실한 방법은 1회 throwaway 세션을 열어 `senpi-memory.session-binding`에서 id를 읽고, 그 id에 시딩한 뒤 본 세션을 돌리는 것. (lab B에서 probe identity에 시딩해 놓쳤고, 재시딩으로 수정함.)
- **격리 검증 기준 (I-1)**: ① 각 세션의 `recalled-memory source=[[...]]` 경로가 자기 프로젝트 것인지 ② 상대 store의 `repo/` 트리에 상대 프로젝트 문자열 0회. **주의**: `runtime/reflection/pending.json`은 transcript 캡처(디렉토리 listing 포함)라 상대 이름이 나올 수 있는데 이건 memory repo가 아니라 진단 캐시다 — 오염 아님. 트랜스크립트의 "yarn/pnpm" 단어 존재도 단독으로 오염 증거가 아니다 (tool이 버전 배열을 도는 경우 등).
- **cross-identity guard 실증**: 자식이 다른 identity의 memory repo를 발견해도 정책이 read를 거부한다 (lab B 세션이 `senpi-lab-b-probe` repo를 "cross-identity read denied, left unread"로 기록).
- **학습 승계 (UC-3)**: 같은 identity의 다음 세션은 이전 세션의 write-back을 compiled block/nudge로 승계한다. 실증: lab B run 1이 스스로 학습해 `setup.md` write-back + facts 11건 → run 2(시딩 후)에서 `recalled_sources: [package-manager.md(시드), notes/facts/2026-09.md]` 둘 다 주입, yarn 사용, npm install 0회.
- **wake 동시성**: lease는 identity의 `runtime/locks/`에 있는 파일 락(recall-wake.slot-<n>.lock, 기본 2슬롯, FIFO tickets). 6 wake 관측 전부 `slotWaitMs ≤ 6ms`, 상한 위반 0. wakes 분석은 각 `wakes.ndjson`의 `at`+`durationMs` 시간창 겹침으로.

## Verification

spawn 후 세션 JSONL에서:

```bash
grep -o "omo-kibitzer:[a-z]*" <session>.jsonl | sort | uniq -c
# 기대: 1 omo-kibitzer:nudged / 1 omo-kibitzer:recall
```

`wakes.ndjson`의 `status: "completed"` + `nudged` 배열에 시딩 path가 있으면 E2E 통과.
