# Оркестрация задач через субагентов

Этот гайд задаёт проверяемый delivery-пайплайн: сильный Orchestrator фиксирует решения и интегрирует, Workers делают ограниченные изменения в изолированных worktree, а Sol High независимо проверяет реальные commit ranges. Для простой локальной правки не создавайте бюрократию и работайте одним агентом.

## Фаза, волна и состояния

Roadmap хранит только приоритеты и зависимости. Исполнение живёт в phase/task artifacts:

```text
Roadmap -> Phase baseline -> Wave dispatch -> Task candidate/fix commits
        -> Sol review -> Accepted -> Integration evidence -> Phase close
```

Состояния задачи:

```text
Planned -> Ready -> Dispatched -> In Progress -> Candidate -> Sol Review
Changes Requested -> Candidate -> Accepted -> Integrated
```

`Blocked`, `Cancelled`, `Superseded` доступны как исключения. `Replan Required` обязателен после двух неудачных review/fix циклов, scope drift, изменения frozen contract или неверного baseline. `Accepted` означает только качество candidate; `Integrated` требует merge и phase validation.

## Девять gates

1. Planning — цель, scope, DAG, ownership, критерии и risks полны.
2. Baseline — branch, SHA, dirty state, submodule revisions и rollback зафиксированы.
3. Contract Freeze — Sol принял shared/public contracts и владельца shared paths.
4. Dispatch — модели, worktrees, leases, paths, бюджеты и внешние gates доступны.
5. Candidate — есть candidate commit, handoff и task-level validation.
6. Review — Sol High проверил фактический commit range и evidence.
7. Integration — accepted commits влиты в DAG-порядке и проверены вместе.
8. Cleanup — worktrees, markers и временные ресурсы инвентаризированы.
9. Phase Close — roadmap, validation debt и routing scorecard обновлены.

Параллельная wave не начинается без принятых первых трёх gates.

## Artifact contracts

Используйте шаблоны в [`../tasks/templates`](../tasks/templates/):

- `phase-baseline.md` — integration branch, reviewed base, contracts, resources и phase checks;
- `dispatch-manifest.md` — сгенерированный снимок назначений и изоляции волны;
- `task-packet.md` — единая задача и test contract;
- `handoff.md` — candidate/fix ranges и фактическое evidence;
- `sol-review.md` — независимый review;
- `integration-evidence.md` — merge и phase verification;
- `routing-scorecard.md` — фактологическая оценка маршрутизации;
- `consumer-profile.json` — generic consumer contract.

`generic-markdown-v1` — канонический формат. `autoqa-markdown-v1` сохраняет совместимость с существующими AutoQA packet без массовой миграции истории. Runtime state хранится untracked и не заменяет tracked artifacts.

## Модели и назначения

Для каждой роли фиксировать четыре поля: **preferred**, **approved fallback**, **selected** (перед dispatch) и **actual** (в handoff). Прямой user pin выше task/thread/default routing, но не выше availability и safety. Недоступный pin не подменяется молча: блокируйте только этот spawn и запрашивайте замену.

Reviewer всегда `gpt-5.6-sol/high`, read-only. Complex Worker поддерживается как `gpt-5.6-sol/medium`. Consumer profile может задать integration strategy, по умолчанию `no-ff`.

## Планирование и dispatch

1. Прочитать ближайшие `AGENTS.md`, profile, roadmap, прямые owning files и validation surfaces.
2. Зафиксировать phase baseline и Sol-owned shared contracts.
3. Разбить работу на DAG; задача имеет один independently reviewable outcome.
4. Задать read/write/generated/shared/forbidden sets, dependencies, lease, acceptance, risk-to-test matrix, rollback и stop conditions.
5. Выделить уникальные branch/worktree для writers от общего base SHA.
6. Сгенерировать dispatch manifest из task packets/profile и отклонить drift.
7. Передать worker только task packet, applicable `AGENTS.md`, frozen decisions, direct ADRs, interfaces/tests и prerequisite handoff.

Worker может сделать один focused context expansion с названием недостающего решения или symbol. Полный transcript, все docs, generated output, logs, videos, screenshots и dumps не передавать без конкретной причины.

## Candidate, review и repair

Worker меняет только owned paths, запускает самый узкий falsifying check и создаёт candidate commit. Handoff содержит changed files, public-contract changes, commands/exit codes, actual model/effort, token/latency/cost, loaded context, assumptions, risks и ссылки на крупные artifacts.

Если validation невозможна, это validation debt: причина, владелец, blocking policy и будущий gate обязательны. Это не `Pass`.

Sol получает task packet, baseline/contracts, **actual candidate/fix commit range**, diff, validation evidence и direct interfaces/tests. Reviewer сам повторяет минимальную falsifying validation, не применяет patch и возвращает findings/verdict. P0–P2 блокируют acceptance, кроме явно оформленного P2 follow-up с владельцем. Fix commits создаёт тот же Worker. Второй неуспешный цикл переводит задачу в `Replan Required`.

## Isolation, integration и cleanup

Нельзя параллелить пересекающиеся owned paths, generated outputs, public contracts, Unity scenes/prefabs/meta/asmdefs/configs, submodules, ports, devices, accounts или mutable external resources. Shared contracts и integration меняет только Sol.

Orchestrator вливает accepted commits по DAG с profile strategy, разрешает semantic conflicts, повторяет integration checks и записывает evidence. `cleanup inspect` только показывает task state, clean/dirty worktree, merged/unmerged branch, last commit и безопасную рекомендацию. Автоматически ничего не удаляйте.

### Параллельные writers в одном репозитории

Каждый writer получает отдельные branch и worktree от **одного phase baseline**. До dispatch сравните literal owned paths, generated outputs и public contracts; не считайте отсутствие Git conflict доказательством независимости. Единственный integration owner serially вливает accepted commits, даже если работа шла параллельно. Один checkout допускает несколько read-only explorers/reviewers или только одного writer.

### Edge cases

- **Dirty base:** не переносите пользовательские изменения в task worktrees. Запишите dirty state в baseline; либо очистите/изолируйте его явно, либо не начинайте wave.
- **CWD binding:** task packet и manifest содержат абсолютный worktree. Worker обязан сверить `cwd`, branch и base SHA перед записью; несовпадение — stop, не «поправить» текущий checkout.
- **Submodules:** запишите parent и submodule SHA. Один Worker меняет submodule, создаёт его commit, после чего Sol отдельным integration step обновляет parent gitlink. Dirty submodule не является base.
- **Generated files:** только declared generated set. Codegen/format/import — writer activity; serialize shared output и проверяйте reproducibility после merge.
- **Unity:** сцены, prefabs, `.meta`, asmdefs, ProjectSettings, Addressables и serialized configs serially owned. Не запускайте несколько Editors на одном project path; в worktrees изолируйте Library/Temp/Logs/build outputs.
- **Shared resources:** порты, devices, accounts, databases, editors и paid quotas требуют lease с owner/window/cleanup. External effects используют test identities и idempotent cleanup.
- **Semantic conflicts:** при чистом merge всё равно проверить contracts, lifecycle and integration scenarios с owner review.
- **Interrupted worker:** сохранить branch, commit/diff и partial handoff; не удалять worktree до inventory и решения Orchestrator.
- **Cleanup:** `inspect` безопасен и ничего не удаляет. Удаление accepted worktree — отдельное подтверждённое действие после integration evidence.

## Зачем нужны тесты и какой уровень выбрать

Тест — исполняемое доказательство acceptance criterion, а не формальность для Worker. Он ограничивает риск дешёвых model tiers, даёт Reviewer независимый факт и ловит interaction defects после merge. Каждому critical criterion и material risk назначьте уровень:

| Уровень | Когда использовать |
| --- | --- |
| Unit | Детерминированная логика, branches, boundary/negative cases. |
| Contract | Public API, schema, serialization, provider/consumer compatibility. |
| Integration | Хранилище, queue, auth, device provider, несколько модулей или реальная инфраструктура. |
| E2E | Пользовательский критический поток через настоящие boundaries; дорого, поэтому только high-value journeys. |
| Regression | Воспроизводимый bug: должен падать до fix и проходить после него. |
| Property/fuzz | Parsers, serializers, mathematical invariants и большое пространство входов. |
| Manual | Hardware, visual quality, unavailable paid/external environment; не заменяет доступный автоматический test. |

Сначала запускайте самый узкий falsifying check. Затем task-level ladder, а после merge — phase/integration rerun. Недоступная среда становится validation debt с владельцем и будущим gate.

## Hooks и cost discipline

Pre-dispatch hook блокирует task без baseline/Ready/manifest, model mismatch, closed gate, shared writer worktree, path overlap, не-Sol shared edit и recursive spawn. Stop hook проверяет commit, handoff, actual model, validation, clean branch и ownership. Reviewer profile read-only. Повторный stop или cancellation не образует цикл.

Установка project hooks сама по себе не активирует task enforcement. Перед
dispatch Orchestrator создаёт JSON runtime state и выполняет:

```text
orchestration_cli runtime activate --repo <worktree> --state <state.json>
```

Marker хранится в результате
`git rev-parse --git-path codex-orchestration/active.json`, то есть отдельно
для каждого worktree. State содержит inline `task`, `phase`, `manifest`,
`profile` либо ссылки на строгие JSON artifacts. Без marker hooks возвращают
`allow/inactive`; повреждённый активный marker блокирует `PreToolUse`, но
quality events только предупреждают. `runtime deactivate` удаляет только этот
marker. Stop-continuation записывается атомарно и допускается один раз.

### Hybrid hook failure policy

Hooks являются enforcement aid, а не единственным source of truth: artifacts и review остаются обязательны. В `hybrid` profile fail closed для `spawn-safety` и `write-scope`: hook unavailable, malformed input или deny не позволяет spawn/write. Fail open только для `handoff-quality` и `validation-completeness`: не прерывайте уже выполненную работу, но создайте visible warning/validation debt и потребуйте gate до acceptance. Повторная continuation разрешена один раз; затем `Cancelled` или явное решение Orchestrator, без stop-loop.

### Native workspace activation и soft modes

**Native workspace activation** — consumer кладёт `.codex/config.toml`, `orchestration.json`, `hooks.json` и agent profiles в свой workspace, включает hooks и использует repo-scoped plugin marketplace. Это режим AutoQA: правила могут быть проверены при dispatch/write, но остаются ограничены возможностями host surface.

**Soft mode** — skill вызывается явно (`$orchestrate-agent-tasks on/auto`) или по trigger description без workspace hooks. Он всё равно требует artifacts, gates, ручную сверку worktree/model/ownership и Sol review, но не обещает runtime blocking. Используйте soft mode, когда repo не может или не должен получать `.codex` config; не изменяйте global Codex config ради активации.

## Управление skill и модельные назначения

Используйте explicit invocation:

```text
$orchestrate-agent-tasks on
$orchestrate-agent-tasks off
$orchestrate-agent-tasks auto
$orchestrate-agent-tasks status
```

`on` включает workflow только в текущем thread: material work получает artifacts/review, но простая правка остаётся single-agent. `off` прекращает implicit/proactive orchestration, не отменяет прямую команду пользователя и не меняет repository/global config. `auto` — default нового thread и включает skill по description-based trigger. `status` не меняет state и показывает mode, Orchestrator, WIP limits, roadmap и active phase/task. Не создавайте `/orchestration`: используйте `$orchestrate-agent-tasks`.

Для назначений поддерживаются:

```text
$orchestrate-agent-tasks models set worker=gpt-5.6-sol:medium
$orchestrate-agent-tasks models set explorer=gpt-5.6-terra:low validator=gpt-5.6-terra:medium
$orchestrate-agent-tasks models task ORCH-017 worker=gpt-5.6-sol:medium
$orchestrate-agent-tasks models status
$orchestrate-agent-tasks models reset [role]
$orchestrate-agent-tasks models reset task ORCH-017
```

Формат — `<model>:<reasoning-effort>`, но в artifact model и effort хранятся раздельно. Поддерживаемые роли: `complex-worker`, `worker`, `mechanical-worker`, `explorer`, `validator`, `summarizer`; `workers=` назначает три worker-роли. `all=` не поддерживается, а `reviewer=` отклоняется: дополнительный advisory reviewer не заменяет mandatory Sol gate.

Приоритет назначения: system/admin/surface availability и safety; последнее прямое user instruction для spawn; task pin; thread pin; role default; automatic cost routing. Сохраняйте partial overrides. Перед spawn проверяйте catalog/effort; неизвестный user pin не заменяйте молча. Если model доступна, но effort нет, предложите ближайший поддерживаемый не ниже requested, иначе highest; если model недоступна — role default с requested effort при совместимости, иначе полный default. Применять replacement можно только после user confirmation.

## Роли и модели по умолчанию

| Роль | Model / effort | Назначение |
| --- | --- | --- |
| Orchestrator | `gpt-5.6-sol/high` | requirements, architecture, DAG, integration |
| Reviewer | `gpt-5.6-sol/high` | independent plan/code/commit-range review |
| Complex Worker | `gpt-5.6-sol/medium` | complex, risky, multi-boundary packets |
| Standard Worker | `gpt-5.6-terra/medium` | ordinary bounded implementation |
| Mechanical Worker | `gpt-5.6-terra/low` | deterministic docs/fixtures/repetition |
| Explorer | `gpt-5.6-terra/low` | bounded evidence gathering |
| Validator | `gpt-5.6-terra/medium` | run checks and diagnose failures |
| Summarizer | `gpt-5.6-luna/low`, if available; otherwise Terra | status/evidence compression |

Orchestrator нельзя понижать ниже high. «Выше high» без точного уровня означает `xhigh`; `xhigh`, `max`, `ultra` допустимы только явно и при availability. Reviewer неизменно Sol High. Более сильная модель не уменьшает required test coverage; слабый user pin может потребовать additional Validator/Reviewer gate.

## Полный delivery pipeline

1. **Triage:** прочитать applicable `AGENTS.md`, consumer profile, branch, dirty state, submodules и roadmap; определить goal, non-goals, acceptance, isolation, priority/risk.
2. **Exploration:** для неизвестных, меняющих plan, запустить 1–2 read-only Explorer с разными вопросами; каждый возвращает files/symbols, flow, constraints, risks и open questions, без raw logs.
3. **DAG:** разбить только когда expected parallel gain выше coordination/merge cost. Одно task packet — один independently reviewable outcome.
4. **Phase planning:** создать baseline: branch, SHA, submodules, rollback, frozen contracts, Sol-owned paths, external/paid policy и phase validation.
5. **Packet planning:** зафиксировать model quartet, read/write/generated/shared/forbidden sets, leases, dependencies, merge order, acceptance, risk-to-test, failure modes, rollback и stop conditions.
6. **Plan review:** independent Sol High проверяет requirements, architecture, contracts, DAG, ownership, external side effects, testability и rollback. Blocking findings закрыть до `Ready`.
7. **Workspace allocation:** один writer на worktree/branch от phase base; read-only agents могут использовать primary checkout. CWD/branch/SHA — часть dispatch contract.
8. **Dispatch:** сгенерировать manifest из packets/profile, проверить gates и передать bounded context packet. Recursive spawn запрещён без explicit Orchestrator authorization.
9. **Implementation:** Worker работает в owned paths, останавливается на drift, пишет/обновляет test, запускает narrow validation, создаёт candidate commit и handoff.
10. **Commit-range review:** Sol High читает packet, frozen contracts, actual range/diff/evidence и direct interfaces/tests; сам выполняет minimum falsifying check, но не patch-ит code.
11. **Repair:** findings возвращаются original Worker. Он создаёт fix commit и новый range. Максимум два failed cycles, затем `Replan Required`.
12. **Integration/close:** Orchestrator serially merges accepted commits per DAG/profile, запускает phase validation, writes integration evidence, inspects cleanup, updates roadmap/debt/scorecard and stable knowledge.

## Roadmap, packets и dispatch artifacts

Roadmap — компактный реестр priority, state, link, dependencies и isolation; он не дублирует execution evidence. Только Orchestrator изменяет общий roadmap. Worker изменяет только свой packet/handoff.

Task packet обязан содержать goal/non-goals, inputs/frozen decisions, branch/worktree/base SHA, four model fields and rationale, ownership sets, dependencies/lease/integration order, public contracts, requirements, acceptance, matrix, failure/retry/cancellation/cleanup/idempotency, budget, stop conditions, handoff/review and rollback. Existing AutoQA Markdown остаётся valid посредством adapter; history не мигрируйте ради формы.

Dispatch manifest генерируется, а не редактируется вручную: phase/wave, baseline, WIP, per-task worktree/branch, models, paths, dependencies, leases/budget и order должны совпадать с packet/profile. Handoff хранит candidate/fix SHA, actual model, outcome, changed files, validation commands/exit/results, debt, loaded context, metrics and risks. Integration evidence хранит ranges/merges, order, validation, semantic conflicts, deferred checks, cleanup and remaining risks.

## Parallel modes и conflict gate

| Mode | Допустимо | Ограничение |
| --- | --- | --- |
| Shared read-only | Explorers/Reviewers | no tracked-state mutation |
| Shared single-writer | one Worker + readers | formatter/codegen/import тоже writer |
| Isolated worktrees | parallel Workers | one writer per worktree, common phase base |
| Serialized integration | Orchestrator | merges, conflicts, phase validation |

По умолчанию максимум три active subagents и два parallel writers; третий slot оставьте Validator/Reviewer/Explorer. Wave может быть параллельной, только если dependencies отсутствуют, write/generated sets не пересекаются, contracts не конфликтуют, external resource is not shared (или имеет safe lease), merge order определён и task/post-merge gates существуют.

Shared roadmap/index/codegen, public contracts, Unity assets, Addressables и submodules требуют serialization даже с чистым text merge. `sharedContractOwner` (normally Sol/Orchestrator) один меняет frozen shared paths. Scope expansion, path overlap, changed external gate or upstream contract invalidates downstream packet and requires revalidation.

## Unity, submodules и external systems

Для Unity сериализуйте scenes, prefabs, `.meta`, ScriptableObject configs, asmdefs, ProjectSettings, package manifests, Addressables groups/catalog, localization tables и generated linker/codegen. Unity Editor параллельно не работает с одним project path; worktree получает own Library/Temp/Logs/build outputs, с учётом disk and import time.

Для submodule записывайте parent/submodule base revisions, создавайте dedicated branch внутри submodule, проверяйте и commit-ите там, затем отдельным parent commit обновляйте gitlink. Один submodule не меняется двумя Workers одновременно; parent pointer обновляйте только после достижимости submodule commit.

External side effects требуют bounded tool, least privilege, unique test identities, approved cost ceiling и idempotent cleanup. Missing device, PostgreSQL, secret, credential or paid-model evidence переводите в validation debt, не обходите mock-ом без recorded decision.

## Test strategy, coverage и evidence

Основная метрика — coverage требований и рисков, не global line coverage. Каждое acceptance criterion связывается с test или justified manual validation; high-risk behavior имеет negative/failure case. Reproducible bug fix получает regression test; manual не заменяет deterministic automation.

| Change | Minimum gate |
| --- | --- |
| Docs/skill | links, YAML/JSON/TOML, skill validation, `git diff --check` |
| Pure logic | unit plus boundary/negative cases |
| Runtime/state | unit/component, lifecycle, failure/cancellation, integration when boundary is real |
| Async/reactive | success, failure, cancellation, retry, duplicate event, cleanup |
| Backend/auth/save/purchase | contract, invalid data, timeout, retry, idempotency, integration smoke |
| Serialization/migration | round-trip, old/corrupt data, identity isolation |
| UI/prefab/scene | compile, asset validation, PlayMode and visual/manual check |
| Addressables/localization/config | GUID/key/schema, load smoke, missing-entry behavior |
| Package/public API | package tests, consumer compile, compatibility |
| Platform/device | target build and device/browser smoke |
| Performance/build size | reproducible baseline, benchmark and delta |

Evidence records criterion/risk, level, scenario, command/environment, expected/actual result, base/commit, concise failure/artifact and post-merge rerun requirement. A flaky test gets one diagnostic rerun only; random pass does not close the gate. Start at owning asmdef/package/module and widen based on integration risk.

## Token, cost и context discipline

- Передавать exact paths/symbols, task packet и short excerpts, не full roadmap/transcript.
- Загружать direct ADRs/knowledge and affected interfaces/tests, а не весь docs tree.
- Exclude generated artifacts, logs, screenshots, videos, APKs, dumps, `node_modules`, `bin`, `obj`, `runs` unless a concrete blocker justifies them.
- Explorer returns roughly twelve evidence points; worker handoff/reviewer output are concise, links point to large evidence outside Git.
- Использовать `fork_turns="none"` или minimum context where the surface allows it; do not delegate without useful parallelism.
- Caveman `full` подходит для prompts/progress/handoff, `lite` — для guide/packet text; никогда не сжимайте contracts/evidence до потери фактов.
- Summarizer вызывайте только когда measured context saving exceeds coordination cost; reviewer never receives worker hidden transcript.

Отслеживайте active agents, repair loops, test failures, repeated context reads, conflicts, import/build time, model escalation, token/latency/cost и escaped defects. Routing scorecard использует фактические данные phase, а не предположения.

## Audit, CLI и AutoQA-only integration

CLI surface: `profile validate`, `phase validate <phase>`, `task validate <task>`, `wave plan <phase> <wave>`, `dispatch validate <manifest>`, `status [--json]`, `worktree allocate|status|release`, `cleanup inspect`, `routing scorecard <phase>`. Validators check transitions, dependency cycles, baseline/model/path/worktree drift, gates, candidate commits, review limits, validation-debt owners and stale markers. `cleanup inspect` is non-destructive.

Audit improvements target cross-file schema/linter, hook fixtures, runtime worktree bindings, status aggregation and routing evals. Text artifacts are source contracts; hooks provide enforcement but cannot erase review obligations.

The plugin lives in `unigame.ai.tools`. AutoQA activates it repo-scoped through its workspace marketplace/pinned submodule and uses `autoqa-markdown-v1`; its docs check invokes the validator and reviews receive Markdown/link hygiene. Do not enable marketplace/plugin/hooks/profiles globally or in `mtt.client`; do not change product source or run paid OpenAI/device actions in the pilot.

## Closure checklist

- All acceptance criteria and blocking findings are closed.
- Candidate/fix/merge ranges, actual model, task and phase validation are recorded.
- `Accepted` and `Integrated` were not conflated.
- Validation debt has owner, policy and future gate.
- User changes, submodule pointers, contracts and external cleanup are safe.
- Roadmap, integration evidence, cleanup inventory and routing scorecard are current.

После phase сравните preferred/fallback/actual модели по completion без replan, severity/count findings, fix cycles, tokens, latency, paid cost, validation failures и escaped defects. Снижать tier можно только при сохранении quality threshold.

## Validation checklist

- Каждому acceptance criterion и material risk соответствует automated test или обоснованная manual validation.
- Сначала выполнить узкий owning check, затем post-merge/phase rerun.
- Проверить compatibility, licenses/dependencies, external gates и cleanup inventory.
- Документационные изменения: Markdown links, YAML/JSON, `quick_validate.py` и `git diff --check`.
