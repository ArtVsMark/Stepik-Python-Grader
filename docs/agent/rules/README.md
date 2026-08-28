<!-- СГЕНЕРИРОВАНО scripts/generate_rules_index.py — не править руками -->

# Указатель правил

> **Что это.** Правила, действующие в этом проекте, — собранные из следов
> каталога [claude-code-playbook](https://github.com/ArtVsMark/claude-code-playbook). Указатель **генерируется**
> (`python scripts/generate_rules_index.py`), а не ведётся руками: список,
> который поддерживают вручную, начинает отставать с первого же нового
> правила — молча.
>
> **Признак принятия — наличие следа.** Правило без ссылки на этот проект
> сюда не попадает: здесь оно не действует.
>
> **Второй рубеж — [дайджест](DIGEST.md).** Там утверждение каждого правила
> одной строкой, и его читает окно на старте: указатель отвечает «какие
> правила есть и чем держатся», дайджест — «что именно они требуют».
>
> **Указатель — для ревизии, а не для работы.** В работе правило действует,
> только если попало в `CLAUDE.md`, в стартовое сообщение окна или в
> задание исполнителя.

## Чем держатся правила

Всего правил, действующих здесь: **90**.

| Уровень | Что это | Сколько |
|---|---|---|
| **гейт** | падает в CI или в `preflight.py` | 44 |
| **шаг процесса** | проверяется человеком в названный момент | 3 |
| **не объявлено** | механизм не назван в каталоге — очередь на автоматизацию | 43 |

**Не объявлено: 43.** Это метрика, и она обязана уменьшаться.
Уровень берётся из раздела «Механизм» самого правила — догадываться по тексту
нельзя: правило, где слово «гейт» встретилось в описании инцидента, не
становится от этого обеспеченным, а метрика начала бы врать в приятную сторону.

## Правила

| Правило | След | Чем держится |
|---|---|---|
| [Транспорт к GitHub: REST по умолчанию, GraphQL только там, где REST не умеет](https://github.com/ArtVsMark/claude-code-playbook/blob/main/rules/ru/001-transport-rest-not-graphql.md) | #1233, #1265 | гейт |
| [Правило без механизма — обещание, а не гарантия](https://github.com/ArtVsMark/claude-code-playbook/blob/main/rules/ru/002-rule-without-mechanism.md) | #1296, #1329 | гейт |
| [Имя ветки может быть переключателем поведения, а не соглашением о стиле](https://github.com/ArtVsMark/claude-code-playbook/blob/main/rules/ru/003-branch-name-is-a-switch.md) | #1302, #1320 | шаг процесса |
| [Конфликт — штатная ситуация конвейера, а не авария](https://github.com/ArtVsMark/claude-code-playbook/blob/main/rules/ru/004-conflict-is-normal-not-outage.md) | #1313 | гейт |
| [Агентское окно живёт три–пять дней](https://github.com/ArtVsMark/claude-code-playbook/blob/main/rules/ru/006-window-lifetime.md) | #1283 | не объявлено |
| [Окно, зависшее на разрешении, снаружи неотличимо от работающего](https://github.com/ArtVsMark/claude-code-playbook/blob/main/rules/ru/007-blocked-window-looks-alive.md) | #1321, #1323 | шаг процесса |
| [Пустой список проверок означает «не стартовало», а не «всё хорошо»](https://github.com/ArtVsMark/claude-code-playbook/blob/main/rules/ru/010-empty-checklist-is-not-green.md) | #1232 | гейт |
| [Наблюдение: событие вместо опроса, а если опрос — то условный](https://github.com/ArtVsMark/claude-code-playbook/blob/main/rules/ru/011-events-not-polling.md) | `docs/agent/preflight.md` | не объявлено |
| [В чужую ветку не пушить](https://github.com/ArtVsMark/claude-code-playbook/blob/main/rules/ru/012-do-not-push-to-someone-elses-branch.md) | `docs/agent/preflight.md` | гейт |
| [Код с экранированием писать файлом, а не heredoc'ом](https://github.com/ArtVsMark/claude-code-playbook/blob/main/rules/ru/013-write-escapes-to-file-not-heredoc.md) | `docs/agent/preflight.md` | гейт |
| [«Тест краснеет до фикса» доказывается полу-откатом, а не откатом всего](https://github.com/ArtVsMark/claude-code-playbook/blob/main/rules/ru/014-red-before-fix-needs-partial-revert.md) | `docs/agent/preflight.md` | не объявлено |
| [Агенты возвращают данные — файлы правит хост](https://github.com/ArtVsMark/claude-code-playbook/blob/main/rules/ru/015-agents-return-data-host-writes-files.md) | `docs/agent/multiagent.md` | не объявлено |
| [Обрезать вывод молча нельзя — только с маркером обрыва](https://github.com/ArtVsMark/claude-code-playbook/blob/main/rules/ru/016-no-silent-truncation.md) | `docs/agent/multiagent.md` | не объявлено |
| [Остаток лимита мерить, а не угадывать — и смотреть первым шагом](https://github.com/ArtVsMark/claude-code-playbook/blob/main/rules/ru/017-measure-quota-do-not-guess.md) | `docs/agent/preflight.md` | не объявлено |
| [Одно окружение проверяет узлы, другое — цепочку](https://github.com/ArtVsMark/claude-code-playbook/blob/main/rules/ru/018-cloud-checks-nodes-local-checks-chain.md) | `docs/agent/environments.md` | не объявлено |
| [Аудит планируется от поверхностей продукта, а не от файлов](https://github.com/ArtVsMark/claude-code-playbook/blob/main/rules/ru/019-audit-from-surfaces-not-files.md) | `docs/agent/multiagent.md` | не объявлено |
| [После сбоя перезапускать дельту, а не всю волну](https://github.com/ArtVsMark/claude-code-playbook/blob/main/rules/ru/020-restart-only-the-delta.md) | `docs/agent/multiagent.md` | не объявлено |
| [Пустое состояние надо объявлять явно](https://github.com/ArtVsMark/claude-code-playbook/blob/main/rules/ru/027-empty-state-is-a-state.md) | `docs/agent/claude-handoff.md` | гейт |
| [Параллельные исполнители запускаются волнами фиксированного размера](https://github.com/ArtVsMark/claude-code-playbook/blob/main/rules/ru/031-waves-not-salvos.md) | `docs/agent/multiagent.md` | не объявлено |
| [Если предмет роли наблюдаем в работающем продукте — роль обязана его запустить](https://github.com/ArtVsMark/claude-code-playbook/blob/main/rules/ru/032-role-must-run-the-product.md) | `docs/agent/roles.md` | не объявлено |
| [Темп длинной работы считается от лимита, а не от желания](https://github.com/ArtVsMark/claude-code-playbook/blob/main/rules/ru/033-pace-from-limit-not-desire.md) | `docs/agent/multiagent.md` | не объявлено |
| [Зона одного исполнителя должна быть маленькой](https://github.com/ArtVsMark/claude-code-playbook/blob/main/rules/ru/034-small-zone-per-executor.md) | `docs/agent/multiagent.md` | не объявлено |
| [Дорогое окружение входит в аудит дважды и коротко](https://github.com/ArtVsMark/claude-code-playbook/blob/main/rules/ru/036-expensive-window-enters-twice-and-briefly.md) | `docs/agent/environments.md` | не объявлено |
| [Находка, полученная не на той поверхности, — гипотеза](https://github.com/ArtVsMark/claude-code-playbook/blob/main/rules/ru/037-finding-status-depends-on-window.md) | `docs/agent/environments.md` | не объявлено |
| [Имя окна начинается с окружения, а не с задачи](https://github.com/ArtVsMark/claude-code-playbook/blob/main/rules/ru/038-window-name-declares-its-environment.md) | `docs/agent/environments.md` | не объявлено |
| [У проверки три исхода, а не два](https://github.com/ArtVsMark/claude-code-playbook/blob/main/rules/ru/039-three-outcomes-not-two.md) | `docs/dev/supply-chain.md` | гейт |
| [Пропуск без причины неотличим от забытого теста](https://github.com/ArtVsMark/claude-code-playbook/blob/main/rules/ru/040-skip-without-reason-is-a-forgotten-test.md) | `scripts/skip_inventory.py`, `tests/test_skip_inventory.py` | гейт |
| [Решение записывается вместе с отвергнутыми вариантами](https://github.com/ArtVsMark/claude-code-playbook/blob/main/rules/ru/042-decision-records-its-alternatives.md) | `docs/dev/adr/README.md` | не объявлено |
| [Решение не правится задним числом — его отменяет новое](https://github.com/ArtVsMark/claude-code-playbook/blob/main/rules/ru/043-decisions-are-superseded-not-edited.md) | `docs/dev/adr/README.md` | не объявлено |
| [Смена правил работы — повод перезапустить окна, а не рассылка](https://github.com/ArtVsMark/claude-code-playbook/blob/main/rules/ru/047-rule-change-restarts-the-windows.md) | #1283 | не объявлено |
| [Предупреждают о вероятном, запрещают достоверное](https://github.com/ArtVsMark/claude-code-playbook/blob/main/rules/ru/051-warn-on-likely-block-on-certain.md) | `scripts/check_work_overlap.py` | гейт |
| [Порядок очереди задаётся правилом, а не готовностью](https://github.com/ArtVsMark/claude-code-playbook/blob/main/rules/ru/053-queue-order-is-a-rule-not-arrival.md) | #1325, #1326, #1329 | гейт |
| [Сбор и разбор — разные проходы](https://github.com/ArtVsMark/claude-code-playbook/blob/main/rules/ru/054-collect-and-analyse-are-separate-passes.md) | `docs/agent/course-walkthrough.md` | не объявлено |
| [Собственный эталон — тоже гипотеза](https://github.com/ArtVsMark/claude-code-playbook/blob/main/rules/ru/055-your-own-expectations-are-a-hypothesis.md) | `docs/agent/course-walkthrough.md` | не объявлено |
| [Правило, которое нельзя проверить машиной, называется явно](https://github.com/ArtVsMark/claude-code-playbook/blob/main/rules/ru/057-unmechanizable-rules-are-named-explicitly.md) | `docs/agent/preflight.md` | не объявлено |
| [Исчерпав квоту — остановиться, а не повторять](https://github.com/ArtVsMark/claude-code-playbook/blob/main/rules/ru/058-when-the-quota-is-out-stop.md) | `docs/agent/preflight.md` | не объявлено |
| [У каждого исчерпаемого ресурса есть заранее составленная карта обхода](https://github.com/ArtVsMark/claude-code-playbook/blob/main/rules/ru/059-map-the-detour-before-the-resource-runs-out.md) | `docs/agent/preflight.md` | не объявлено |
| [Разбор после каждой волны, и качество важнее механики](https://github.com/ArtVsMark/claude-code-playbook/blob/main/rules/ru/060-debrief-every-wave-quality-first.md) | `docs/agent/multiagent.md` | не объявлено |
| [Запреты окружения пишутся в задании, а не подразумеваются](https://github.com/ArtVsMark/claude-code-playbook/blob/main/rules/ru/061-environment-bans-belong-in-the-task.md) | `docs/agent/multiagent.md` | не объявлено |
| [Роль заводится, если способна возразить, а не дополнить](https://github.com/ArtVsMark/claude-code-playbook/blob/main/rules/ru/062-a-role-must-be-able-to-object.md) | `docs/agent/roles.md` | не объявлено |
| [Автоматическое вмешательство включается по всем условиям сразу](https://github.com/ArtVsMark/claude-code-playbook/blob/main/rules/ru/063-automatic-intervention-needs-all-conditions.md) | `docs/agent/dispatcher.md` | не объявлено |
| [Блокировку берут на спутника, а не на файл, который заменяется целиком](https://github.com/ArtVsMark/claude-code-playbook/blob/main/rules/ru/066-lock-the-companion-not-the-target.md) | `src/stepik_grader/atomic_io.py` | гейт |
| [Уборка после сбоя не превращает сбой в успех](https://github.com/ArtVsMark/claude-code-playbook/blob/main/rules/ru/067-cleanup-must-not-swallow-the-failure.md) | `src/stepik_grader/atomic_io.py` | гейт |
| [Список разрешённого, а не список запрещённого](https://github.com/ArtVsMark/claude-code-playbook/blob/main/rules/ru/068-allowlist-not-denylist.md) | #1280, #1346, `src/stepik_grader/web/statement_adapter.py` | гейт |
| [Пишем поле, а не снимок, если писателей несколько](https://github.com/ArtVsMark/claude-code-playbook/blob/main/rules/ru/069-write-the-field-not-the-snapshot.md) | `src/stepik_grader/web/settings_adapter.py` | гейт |
| [Эвристическая защита ослабляется осознанно — с записью остаточного риска](https://github.com/ArtVsMark/claude-code-playbook/blob/main/rules/ru/070-a-heuristic-guard-fails-open-with-a-written-risk.md) | `src/stepik_grader/web/http_guards.py` | гейт |
| [Намеренный дубль подписывается](https://github.com/ArtVsMark/claude-code-playbook/blob/main/rules/ru/071-deliberate-duplication-is-signed.md) | `src/stepik_grader/web/auth_adapter.py`, `src/stepik_grader/launcher.py` | шаг процесса |
| [Причину ловит гейт, факт — фикстура: нужны обе](https://github.com/ArtVsMark/claude-code-playbook/blob/main/rules/ru/072-guard-the-cause-and-the-effect.md) | `scripts/check_test_isolation.py`, `tests/conftest.py` | гейт |
| [Версия инструмента — из одного источника и с верхней границей](https://github.com/ArtVsMark/claude-code-playbook/blob/main/rules/ru/073-tool-version-from-one-source-with-an-upper-bound.md) | `scripts/check_ruff_pin.py` | гейт |
| [Необратимый шаг проверяется инвариантами заранее](https://github.com/ArtVsMark/claude-code-playbook/blob/main/rules/ru/074-one-shot-irreversible-steps-get-their-own-guard.md) | `scripts/check_workflow_guardrails.py` | гейт |
| [Гейт, не нашедший предмета проверки, обязан упасть](https://github.com/ArtVsMark/claude-code-playbook/blob/main/rules/ru/075-a-guard-that-finds-nothing-must-fail.md) | `scripts/check_workflow_guardrails.py` | гейт |
| [Сообщение ссылается на то, что есть у получателя](https://github.com/ArtVsMark/claude-code-playbook/blob/main/rules/ru/076-messages-point-at-what-the-user-actually-has.md) | `scripts/check_locale_guardrails.py` | гейт |
| [Совпадение ключей — ещё не перевод](https://github.com/ArtVsMark/claude-code-playbook/blob/main/rules/ru/077-key-parity-is-not-translation.md) | `scripts/check_locale_guardrails.py` | гейт |
| [Отмена — отдельный исход, а не разновидность ошибки](https://github.com/ArtVsMark/claude-code-playbook/blob/main/rules/ru/078-cancelled-is-not-an-error.md) | `src/stepik_grader/web/runs.py` | гейт |
| [Срок хранения отсчитывается от завершения, а не от постановки](https://github.com/ArtVsMark/claude-code-playbook/blob/main/rules/ru/079-ttl-counts-from-completion.md) | `src/stepik_grader/web/runs.py` | не объявлено |
| [Чужой код запускают из приватного каталога, а не из общего временного](https://github.com/ArtVsMark/claude-code-playbook/blob/main/rules/ru/081-untrusted-code-runs-in-a-private-directory.md) | `src/stepik_grader/web/playground.py` | гейт |
| [Состав ролей покрывает все пласты продукта, а не только разработку](https://github.com/ArtVsMark/claude-code-playbook/blob/main/rules/ru/082-roles-must-cover-every-layer.md) | `docs/agent/roles.md` | не объявлено |
| [Тяжесть находки ставит не тот, кто её нашёл — но опровергателю нужна шкала](https://github.com/ArtVsMark/claude-code-playbook/blob/main/rules/ru/086-the-finder-does-not-grade-the-finding.md) | `docs/agent/multiagent.md` | не объявлено |
| [Повторный проход получает на вход прошлые находки и запрет их переоткрывать](https://github.com/ArtVsMark/claude-code-playbook/blob/main/rules/ru/087-a-second-pass-needs-a-novelty-rule.md) | `docs/agent/multiagent.md` | не объявлено |
| [Критик проверяет метод фазы, а не предмет работы](https://github.com/ArtVsMark/claude-code-playbook/blob/main/rules/ru/088-the-critic-checks-the-method-not-the-subject.md) | `docs/agent/multiagent.md` | не объявлено |
| [Из оригинала в его копию не ссылаются](https://github.com/ArtVsMark/claude-code-playbook/blob/main/rules/ru/089-never-link-from-the-original-to-its-copy.md) | `docs/dev/glossary.md` | гейт |
| [Находки и порядок разбора — разные документы](https://github.com/ArtVsMark/claude-code-playbook/blob/main/rules/ru/092-findings-and-ordering-live-in-different-documents.md) | `docs/agent/claude-handoff.md` | не объявлено |
| [У проверяющего инструмента две ошибки, и каждая держится своим тестом](https://github.com/ArtVsMark/claude-code-playbook/blob/main/rules/ru/097-a-checker-has-two-error-types.md) | `docs/dev/corpus.md` | не объявлено |
| [Единица дробления определяется употреблением, а не формальным признаком](https://github.com/ArtVsMark/claude-code-playbook/blob/main/rules/ru/098-the-unit-of-splitting-follows-usage.md) | `docs/dev/glossary.md` | не объявлено |
| [Конфликт классификации разрешается по последствию, а не по правильности](https://github.com/ArtVsMark/claude-code-playbook/blob/main/rules/ru/099-classification-conflicts-resolve-by-consequence.md) | `docs/dev/glossary.md` | не объявлено |
| [Дедлайнов два: на запуск и на работу](https://github.com/ArtVsMark/claude-code-playbook/blob/main/rules/ru/100-two-deadlines-start-and-work.md) | `docs/use/configuration.md` | не объявлено |
| [Повторяют только те отказы, которые могут пройти сами](https://github.com/ArtVsMark/claude-code-playbook/blob/main/rules/ru/101-retry-only-what-can-heal-itself.md) | `docs/use/installation.md` | гейт |
| [Снисхождение перечисляется таблицей и отключается режимом](https://github.com/ArtVsMark/claude-code-playbook/blob/main/rules/ru/102-leniency-is-enumerated-and-switchable.md) | `docs/use/configuration.md` | гейт |
| [Сторож побочных эффектов обвиняет не виновника — и исключения задаются формой](https://github.com/ArtVsMark/claude-code-playbook/blob/main/rules/ru/103-a-side-effect-guard-blames-the-wrong-suspect.md) | `tests/conftest.py` | гейт |
| [У событийной автоматики должна быть ручная кнопка](https://github.com/ArtVsMark/claude-code-playbook/blob/main/rules/ru/104-event-driven-automation-needs-a-manual-button.md) | `.github/workflows/ci.yml` | гейт |
| [Огласка умножает и хорошее, и плохое — сначала настоящий прогон](https://github.com/ArtVsMark/claude-code-playbook/blob/main/rules/ru/106-publicity-multiplies-both-sides.md) | `docs/dev/corpus.md` | не объявлено |
| [Каждый выход из переходного состояния обязан быть терминальным](https://github.com/ArtVsMark/claude-code-playbook/blob/main/rules/ru/109-every-exit-from-a-transient-state-must-be-terminal.md) | `src/stepik_grader/launcher.py` | гейт |
| [Если инструмент может сделать сам — он делает, а не советует](https://github.com/ArtVsMark/claude-code-playbook/blob/main/rules/ru/111-do-it-instead-of-advising-it.md) | `src/stepik_grader/launcher.py` | не объявлено |
| [Контракт описывает правила собственной эволюции](https://github.com/ArtVsMark/claude-code-playbook/blob/main/rules/ru/113-a-contract-states-how-it-may-change.md) | `docs/dev/result-contract.md` | не объявлено |
| [У настроек один якорь и ограниченная зона поиска](https://github.com/ArtVsMark/claude-code-playbook/blob/main/rules/ru/115-config-has-one-anchor-and-a-bounded-search.md) | `docs/use/configuration.md` | гейт |
| [Сборщик результатов — тоже источник потерь, и у него своя сверка](https://github.com/ArtVsMark/claude-code-playbook/blob/main/rules/ru/116-the-collector-script-is-a-source-of-loss.md) | `docs/archive/audit-2026-07-30-full-roles.md` | гейт |
| [У задания исполнителя есть числовые границы](https://github.com/ArtVsMark/claude-code-playbook/blob/main/rules/ru/117-numeric-limits-belong-in-the-task-spec.md) | `docs/archive/audit-2026-07-30-full-roles.md`, `docs/agent/multiagent.md` | не объявлено |
| [Исходник хранится рядом с производным](https://github.com/ArtVsMark/claude-code-playbook/blob/main/rules/ru/118-keep-the-source-next-to-the-derived.md) | `docs/use/grader-workflow.md` | гейт |
| [Свои артефакты держат вне маски входа](https://github.com/ArtVsMark/claude-code-playbook/blob/main/rules/ru/119-tool-artefacts-stay-outside-the-input-mask.md) | `tests/conftest.py` | гейт |
| [Каталог правил ведётся по своим правилам, а указатель к нему генерируется](https://github.com/ArtVsMark/claude-code-playbook/blob/main/rules/ru/120-how-to-run-a-rule-catalogue.md) | #1342 | гейт |
| [Закрытие контейнера — не доказательство закрытия работы](https://github.com/ArtVsMark/claude-code-playbook/blob/main/rules/ru/121-closing-the-container-is-not-closing-the-work.md) | `docs/agent/claude-handoff.md` | гейт |
| [Рядом с отформатированным значением отдают сырое](https://github.com/ArtVsMark/claude-code-playbook/blob/main/rules/ru/122-ship-the-raw-value-next-to-the-formatted-one.md) | `docs/dev/api.md` | не объявлено |
| [Перезапускать минимум, но зелёное со второго раза — находка, а не починка](https://github.com/ArtVsMark/claude-code-playbook/blob/main/rules/ru/124-rerun-the-minimum-and-record-the-flake.md) | #924, #1171, #1344 | гейт |
| [У заморозки должен быть выход, не проходящий через замороженное действие](https://github.com/ArtVsMark/claude-code-playbook/blob/main/rules/ru/126-a-freeze-needs-a-thaw-path.md) | #1326, #1344, #1347 | гейт |
| [Обязательное поле проверяется на полноту, а не на непустоту](https://github.com/ArtVsMark/claude-code-playbook/blob/main/rules/ru/128-a-required-field-is-checked-for-completeness.md) | #1329, #1345, #1350 | гейт |
| [У каталога есть контракт потребления, а не только правила ведения](https://github.com/ArtVsMark/claude-code-playbook/blob/main/rules/ru/129-a-catalogue-needs-a-consumption-contract.md) | #15, #1351 | гейт |
| [Из облачного окна не пишут: на записи учётные данные подменяются](https://github.com/ArtVsMark/claude-code-playbook/blob/main/rules/ru/131-no-writes-from-a-cloud-session.md) | #1302 | гейт |
| [Изменение везут одной темой: сборное честно, но неразбираемо](https://github.com/ArtVsMark/claude-code-playbook/blob/main/rules/ru/132-one-change-carries-one-topic.md) | #20, #21, #1350 | гейт |
| [Границу изменения задаёт пересечение файлов, а не число задач](https://github.com/ArtVsMark/claude-code-playbook/blob/main/rules/ru/133-file-overlap-sets-the-boundary.md) | #1345, #1350 | гейт |
| [Окно контекста для разбора прозы — абзац, а не предложение](https://github.com/ArtVsMark/claude-code-playbook/blob/main/rules/ru/144-context-window-for-prose-is-a-paragraph.md) | `scripts/check_audit_registry.py` | гейт |
