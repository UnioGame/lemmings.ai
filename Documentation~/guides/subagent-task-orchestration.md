# Оркестрация реализации задач через субагентов

Гайд описывает cost-aware пайплайн: сильная модель принимает решения и проверяет результат, дешёвые модели выполняют ограниченные задачи, Git worktrees изолируют параллельные изменения, тесты дают проверяемые доказательства.

## Содержание

1. [Принципы](#принципы)
2. [Управление skill](#управление-skill)
3. [Выбор моделей пользователем](#выбор-моделей-пользователем)
4. [Роли и модели по умолчанию](#роли-и-модели-по-умолчанию)
5. [Пайплайн](#пайплайн)
6. [Roadmap и task-файлы](#roadmap-и-task-файлы)
7. [Параллельная работа в одном репозитории](#параллельная-работа-в-одном-репозитории)
8. [Unity и submodules](#unity-и-submodules)
9. [Граничные случаи](#граничные-случаи)
10. [Тестовая стратегия](#тестовая-стратегия)
11. [Экономия токенов](#экономия-токенов)
12. [Аудит плана и улучшения](#аудит-плана-и-улучшения)
13. [Закрытие задачи](#закрытие-задачи)

## Принципы

- Главный агент хранит требования, решения, зависимости и итоговый результат.
- Субагент получает одну ограниченную ответственность.
- План должен быть decision-complete до начала записи файлов.
- Пользовательский выбор модели имеет приоритет над автоматической экономией.
- Неизвестную или недоступную модель нельзя молча заменять.
- Параллелить выгодно чтение, диагностику, независимые реализации и проверки.
- Параллельные writers требуют отдельных worktrees.
- Acceptance criteria и существенные риски должны иметь тестовое доказательство.
- Review выполняет агент, не писавший проверяемый код.
- Security, sandbox, approvals и destructive-action rules нельзя отключить командой skill.

## Управление skill

Использовать explicit skill invocation:

```text
$orchestrate-agent-tasks on
$orchestrate-agent-tasks off
$orchestrate-agent-tasks auto
$orchestrate-agent-tasks status
```

### `on`

- Включает workflow в текущем thread.
- Для многошаговой задачи требует roadmap/task files и review gates.
- Не заставляет создавать субагентов для простой локальной работы.

### `off`

- Отключает implicit и proactive использование orchestration workflow в текущем thread.
- Не создаёт автоматически task files и субагентов.
- Не отменяет последующую прямую команду пользователя.
- Не меняет repository или global configuration.

### `auto`

- Возвращает стандартный description-based trigger.
- Является default для нового thread.

### `status`

Показывает без мутаций:

```text
Orchestration: AUTO|ON|OFF
Orchestrator: gpt-5.6-sol / high
Max subagents: 3
Max parallel writers: 2
Roadmap: <path>
Active task: <id|none>
```

Skill не регистрирует `/orchestration`: custom slash prompts deprecated. Для включения используется `$orchestrate-agent-tasks`.

Persistent explicit-only режим задаётся в `agents/openai.yaml`:

```yaml
policy:
  allow_implicit_invocation: false
```

Explicit `$orchestrate-agent-tasks on` остаётся доступным.

## Выбор моделей пользователем

### Thread-wide assignments

```text
$orchestrate-agent-tasks models set worker=gpt-5.6-sol:medium
$orchestrate-agent-tasks models set explorer=gpt-5.6-terra:low validator=gpt-5.6-terra:medium
```

### Task-specific assignments

```text
$orchestrate-agent-tasks models task ORCH-017 worker=gpt-5.6-sol:medium
$orchestrate-agent-tasks models task ORCH-018 explorer=gpt-5.6-terra:medium
```

### Просмотр и сброс

```text
$orchestrate-agent-tasks models status
$orchestrate-agent-tasks models reset
$orchestrate-agent-tasks models reset worker
$orchestrate-agent-tasks models reset task ORCH-017
```

Формат пользовательского ввода:

```text
<model>:<reasoning-effort>
```

Model и effort сохраняются раздельно. Поддерживаемые user-assigned роли:

- `complex-worker`;
- `worker`;
- `mechanical-worker`;
- `explorer`;
- `validator`;
- `summarizer`.

`workers=` назначает одну конфигурацию трём worker-ролям. `all=` запрещён: он может случайно понизить Reviewer.

Mandatory Reviewer всегда `gpt-5.6-sol/high`. Команда `reviewer=` отклоняется. Пользователь может запросить дополнительного advisory-агента другой модели, но он не заменяет обязательный quality gate.

Natural-language запрос тоже допустим:

```text
Используй Terra Low для explorer и Sol Medium для workers.
```

Orchestrator нормализует его в точные model/effort поля до spawn.

### Scope и precedence

- `models set` действует до reset или конца thread.
- `models task <ID>` записывается в task-файл и сохраняется между threads.
- При `off` assignments сохраняются, но не используются до `on`/`auto`.
- Новый thread не наследует thread assignments.

Приоритет:

1. System/admin/surface availability и safety constraints.
2. Последнее прямое указание пользователя для spawn.
3. Task-specific assignment.
4. Thread role assignment.
5. Skill default.
6. Автоматическая cost-based маршрутизация.

Перед spawn проверить model catalog и поддержку effort. Если pin недоступен:

- не запускать этого субагента;
- не подменять модель молча;
- сообщить точную несовместимость;
- предложить ближайший доступный вариант;
- продолжить независимые задачи с валидными assignments.

Предложение замены строится детерминированно, но применяется только после подтверждения пользователя:

1. Model доступна, effort нет — сохранить model и предложить ближайший поддерживаемый effort не ниже запрошенного; если такого нет, максимальный поддерживаемый.
2. Model недоступна — взять default model роли с запрошенным effort, если сочетание валидно.
3. Иначе предложить полный default assignment роли.

`models status` показывает model, effort, source и scope каждой роли.

## Роли и модели по умолчанию

| Роль | Модель | Effort | Назначение |
| --- | --- | --- | --- |
| Orchestrator | `gpt-5.6-sol` | `high` | требования, архитектура, DAG, интеграция |
| Reviewer | `gpt-5.6-sol` | `high` | plan/code/test review |
| Complex Worker | `gpt-5.6-sol` | `medium` | сложная реализация по готовому плану |
| Standard Worker | `gpt-5.6-terra` | `medium` | обычная ограниченная реализация |
| Mechanical Worker | `gpt-5.6-terra` | `low` | повторяемые изменения, docs, fixtures |
| Explorer | `gpt-5.6-terra` | `low` | узкий поиск, чтение, сбор evidence |
| Validator | `gpt-5.6-terra` | `medium` | тесты, воспроизведение и диагностика failures |
| Summarizer | `gpt-5.6-luna`, если доступна | `low` | extraction/classification |

`Worker-Sol-Medium` — роль, не model slug:

```toml
model = "gpt-5.6-sol"
model_reasoning_effort = "medium"
```

Orchestrator по умолчанию использует `Sol High`. Повышение допускается только явно:

- «выше high» без точного значения означает `xhigh`;
- точный `xhigh`, `max` или `ultra` применяется при доступности;
- Orchestrator нельзя понизить ниже `high`;
- `Ultra` не отменяет ownership, WIP и recursive-spawn limits.

Reviewer нельзя заменить или понизить ниже `Sol High`. Дополнительный user-selected reviewer может дать второе мнение, но обязательный `Sol High` review всё равно выполняется.

## Пайплайн

### 1. Triage

Orchestrator:

- читает применимые `AGENTS.md` и domain skills;
- проверяет branch, dirty state, submodules и текущий roadmap;
- уточняет goal, non-goals, acceptance criteria и ограничения;
- выбирает single-agent или multi-agent режим;
- назначает priority, risk и isolation.

Простая детерминированная задача остаётся single-agent.

### 2. Exploration

Использовать 1–2 read-only Explorer только для неизвестных, меняющих план.

Каждый получает отдельный вопрос и возвращает:

- файлы и символы;
- фактический execution/data flow;
- constraints;
- доказанные риски;
- открытые вопросы.

Лимит: около 12 evidence-пунктов, без raw logs.

### 3. Декомпозиция

Построить dependency DAG. Один task-файл должен давать независимо реализуемый и проверяемый результат.

Не дробить задачу, если coordination/merge overhead выше ожидаемой экономии времени.
Для простой single-agent правки task-файл и запись в roadmap не нужны.

### 4. Task planning

Зафиксировать:

- base revision и submodule revisions;
- role, model, effort и assignment source;
- read/write/generated sets;
- shared resources;
- dependencies и merge order;
- goal, non-goals и решения;
- implementation steps;
- acceptance criteria;
- risk-to-test matrix;
- rollback.

Нерешённый архитектурный или продуктовый вопрос запрещает статус `Ready`.

### 5. Plan Review

Независимый `Sol High` Reviewer проверяет:

- полноту требований;
- архитектурные границы;
- корректность DAG;
- реальную независимость параллельных задач;
- failure modes;
- тестируемость;
- rollback.

Blocking findings закрываются до implementation.

### 6. Workspace allocation

- Read-only агенты могут использовать основной checkout.
- В основном checkout допускается один writer.
- Несколько writers получают отдельные worktrees от одного чистого base commit.
- Каждый worktree имеет detached HEAD или уникальную ветку `codex/<task-id>-<slug>`.

### 7. Dispatch

Worker получает:

- task ID и путь task-файла;
- точные owning paths/symbols;
- declared write set;
- acceptance criteria;
- test commands;
- output contract.

Передавать минимальный контекст, а не полный chat history. Recursive spawn запрещён без разрешения Orchestrator.

### 8. Implementation

Worker:

- меняет только declared write set;
- останавливается при scope drift;
- добавляет тесты вместе с поведением;
- запускает узкий task-level validation;
- возвращает до 8 handoff-пунктов.

### 9. Code Review

Reviewer получает task-файл, diff и test evidence. Он не исправляет код.

Finding содержит:

- severity;
- file/line;
- доказательство;
- нарушенный acceptance criterion;
- ожидаемое исправление.

### 10. Repair

Исправления возвращаются исходному Worker. Максимум две итерации. Затем:

- остановить реализацию;
- вернуть задачу в `Draft`;
- исправить план, scope или model assignment.

### 11. Integration

Orchestrator:

- интегрирует task commits последовательно по DAG;
- не разрешает Workers сливать чужие ветки;
- решает conflicts;
- запускает post-merge integration tests;
- обновляет roadmap.

### 12. Closure

`Done` разрешён только после закрытия findings и test gates. Stable knowledge переносится в owning knowledge docs; raw investigation остаётся task-scoped.

## Roadmap и task-файлы

Priority:

- `P0` — production/security/blocker;
- `P1` — текущая обязательная цель;
- `P2` — следующая очередь;
- `P3` — improvement/experiment;
- `Inbox` — без triage.

Status flow:

```text
Draft -> Plan Review -> Ready -> In Progress -> Code Review -> Integration -> Done
```

`Blocked` доступен из любого активного статуса.

Roadmap хранит:

- ID, priority, status;
- task link;
- compact assignment summary и ссылку на полную per-role таблицу в task-файле;
- isolation;
- dependencies и parallel group;
- shared resources;
- merge order;
- updated date.

Только Orchestrator обновляет общий roadmap. Worker обновляет только Progress/Handoff своего task-файла.

## Параллельная работа в одном репозитории

### Режимы

| Режим | Допустимо | Ограничения |
| --- | --- | --- |
| Shared read-only | Несколько Explorer/Reviewer | Только операции без tracked-state mutation |
| Shared single-writer | Один Worker и read-only агенты | Formatter/codegen/Unity import считаются writer |
| Isolated worktrees | Несколько Workers | Один writer на worktree, общий base commit |
| Serialized integration | Один Orchestrator | Merge, conflict resolution, post-merge tests |

По умолчанию:

- максимум три субагента;
- максимум два параллельных writers;
- третий слот сохраняется для Explorer, Validator или Reviewer.

### Conflict gate

Задачи параллельны только при одновременном выполнении условий:

- между ними нет dependency;
- write sets не пересекаются;
- generated output одной задачи не затрагивает другую;
- одна задача не меняет contract/API, используемый другой;
- нет общего mutable external resource;
- определён безопасный merge order;
- существуют task-level и post-merge test gates.

Даже чистый text merge не доказывает отсутствие semantic conflict.

## Unity и submodules

### Unity hotspots

Сериализовать работу с:

- scenes и prefabs;
- asset и его `.meta`;
- ScriptableObject configs;
- asmdefs;
- `ProjectSettings`;
- package manifests;
- Addressables groups/catalog;
- localization tables;
- generated linker/codegen outputs.

Unity Editor нельзя параллельно запускать против одного project path. Каждый worktree имеет отдельные `Library`, `Temp`, `Logs` и build outputs. Учитывать import time и disk usage.

### Submodules

Для изменения submodule:

1. Записать parent и submodule base revisions.
2. Создать отдельную ветку внутри submodule.
3. Реализовать и проверить изменение.
4. Создать commit submodule.
5. Отдельным шагом обновить parent gitlink.

Два Workers не меняют один submodule параллельно. Dirty submodule нельзя использовать как неявный base.

## Граничные случаи

- **Dirty checkout:** не копировать пользовательские изменения во все worktrees без явной необходимости.
- **Branch занята:** использовать detached HEAD, уникальную ветку или Handoff.
- **Scope drift:** остановить Worker и повторить planning/review.
- **Upstream contract изменился:** downstream task становится stale и проходит повторную validation.
- **Semantic conflict:** owner review и integration test обязательны даже без Git conflict.
- **Shared port/device/account:** использовать resource lease и последовательный запуск.
- **External side effects:** уникальные test identities, idempotent cleanup, запрет production writes.
- **Flaky test:** один diagnostic rerun; случайный pass не закрывает задачу.
- **Interrupted Worker:** сохранить commit/diff/handoff до cleanup worktree.
- **Generated files:** сохранять только declared outputs.
- **Secrets:** копировать в worktree минимально необходимое, не добавлять в Git.
- **Unavailable user model:** блокировать только затронутый spawn, не заменять молча.

## Тестовая стратегия

### Зачем нужны тесты

Тесты:

- превращают acceptance criteria в исполняемое доказательство;
- позволяют дешёвым Workers безопасно менять код;
- дают Reviewer факты вместо доверия к summary;
- защищают contracts между параллельными задачами;
- ловят interaction regressions после merge;
- сокращают repair loops и повторное чтение контекста;
- документируют ожидаемое поведение.

Основная метрика — coverage требований и рисков. Глобальный line-coverage процент без baseline не вводится.

### Минимальные test gates

| Изменение | Проверки |
| --- | --- |
| Docs/skill | Markdown links, YAML/frontmatter, package JSON, `quick_validate.py`, `git diff --check` |
| Pure logic | Compile, NUnit/EditMode, boundary и negative cases |
| Runtime service/state | Unit/component, lifecycle, failure/cancellation, PlayMode при необходимости |
| Async/reactive flow | Success, failure, cancellation, retry, duplicate event, cleanup |
| Backend/auth/save/purchase | Contract, invalid data, timeout, retry, idempotency, integration smoke |
| Serialization/migration | Round-trip, old/corrupt data, identity isolation |
| UI/prefab/scene | Compile, asset validation, PlayMode, visual validation |
| Addressables/localization/config | GUID/key/schema, load smoke, missing-entry behavior |
| Package/asmdef/public API | Package tests, consumer compile, compatibility |
| Platform behavior | Target compile/build, browser/device smoke |
| Performance/build size | Reproducible baseline, benchmark и delta |
| Bug fix | Regression test, failing before and passing after |

Property/fuzz tests применять для parsers, serializers, mathematical invariants и большого пространства входов.

### Coverage rules

- Каждый acceptance criterion связан с test или обоснованной manual validation.
- Каждый high-risk пункт имеет failure/negative test.
- Bug fix без regression test требует записанного обоснования.
- Manual test не заменяет автоматический, если поведение детерминировано.
- Сначала запускать owning asmdef/package, затем расширять scope по integration risk.
- Более сильная модель не уменьшает test coverage.
- Более слабая user-pinned модель может потребовать дополнительный Reviewer/Validator gate.

### Test evidence

Записывать:

- criterion/risk;
- test level и scenario;
- command/environment;
- expected result;
- actual pass/fail;
- base/commit;
- краткую точную ошибку или artifact;
- необходимость post-merge rerun.

Raw logs в task-файл не копировать.

## Экономия токенов

- Главный thread хранит решения, не сырые исследования.
- Использовать `fork_turns="none"` или минимальный fork.
- Передавать точные paths/symbols и task file.
- Использовать `rg` и узкие reads.
- Не загружать все context docs и generated artifacts.
- Применять domain skills вместо повторения больших prompts.
- Caveman `full` подходит для prompts/progress/handoff; `lite` — для гайдов и task files.
- Один Validator может проверять интегрированную parallel group.
- Reuse исходного Worker для repairs.
- Не создавать субагента без полезного параллелизма.

Полезные метрики:

- число субагентов;
- repair loops;
- test failures;
- повторные context reads;
- конфликтующие изменения;
- время import/build;
- причины model escalation.

## Аудит плана и улучшения

Во время четырёх read-only forward-tests и независимого `Sol High` review выявлены следующие пробелы; evidence хранится в [ORCH-001](../tasks/ORCH-001-bootstrap-orchestration-toolkit.md):

- task-файлы ограничены material/multi-agent работой, чтобы простая правка не создавала бюрократию;
- Explorer и Validator получили детерминированный effort;
- exact write/generated sets стали обязательным gate, а заявленная «независимость» задач сама по себе недостаточна;
- shared roadmap, index, codegen и другие общие outputs явно сериализованы;
- worktree isolation отделено от semantic safety: общие contracts, Unity assets, Addressables и submodules всё равно сериализуются;
- добавлены leases для Editor, port, device, account и другого mutable state;
- parent gitlink разрешено обновлять только после достижимости submodule commit;
- test coverage привязано к acceptance criteria и рискам, а не только к line coverage;
- ограничены repair loops, recursive spawn и объём handoff;
- model pins получили scope, precedence, availability check и запрет silent fallback;
- обязательный Reviewer зафиксирован на `gpt-5.6-sol/high`, независимо от worker overrides;
- правило «ближайшей» замены модели стало детерминированным и требует подтверждения пользователя;
- dirty user state, interrupted workers, flaky tests, secrets и external side effects получили отдельные правила.

Следующие улучшения вынесены в roadmap:

1. Машиночитаемая schema и linter для roadmap/task-файлов.
2. CI-набор contract/forward-tests для mode commands, model routing и conflict gates.
3. Benchmark token/cost/latency/repair-rate для адаптивного выбора моделей.
4. После накопления данных — динамические WIP limits и escalation thresholds по типу репозитория.

Текущий план пригоден для ручного использования. Для командного масштабирования приоритетны schema/linter и CI: они превращают текстовые правила в автоматически проверяемый контракт.

## Закрытие задачи

Перед `Done` проверить:

- acceptance criteria выполнены;
- blocking review findings закрыты;
- task-level и post-merge tests записаны;
- model assignments соответствуют пользовательским pins;
- user changes не затронуты;
- submodule pointers корректны;
- roadmap обновлён;
- remaining risks явно перечислены.

## Источники

- [Codex Subagents](https://learn.chatgpt.com/docs/agent-configuration/subagents)
- [Codex Worktrees](https://learn.chatgpt.com/docs/environments/git-worktrees)
- [Codex Models](https://learn.chatgpt.com/docs/models)
- [Build skills](https://learn.chatgpt.com/docs/build-skills)
