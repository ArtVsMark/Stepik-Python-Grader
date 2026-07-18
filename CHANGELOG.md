# Changelog

## [Unreleased]

<!--
Единственный актуальный «Unreleased» — этот, вверху файла. Записи вида
`## [unreleased] / <дата>` и `## [Unreleased] — <месяц> — …` ниже по файлу —
исторические до-релизные снимки ранних спринтов, а не текущий незарелиженный
раздел. Не путать с этим блоком.
-->

### Added
- `--ai-hints` — opt-in AI explanation of failing WA/RE cases (CLI modes 1/2) via a BYOK OpenAI-compatible endpoint on bare `requests`; off by default, silent-skip on any failure, works with cloud and local ollama (ADR-0003) (#435).
- `POST /api/v1/runs` applies back-pressure — at most `CONFIG.max_active_runs` (default 20) concurrent non-terminal jobs, else **429** `too_many_runs`; a seam for server mode (#151) (#429).
- `--import-reference <task_dir>` imports the pinned Stepik solution (+ top-liked, `--import-top N`, default 5) into the task folder as `task{N}_{100+}.py` — a reference competitor for modes 2–4, binding recorded in `meta.json` (#464, #55).
- Web: the «Найти эталонное решение» button in the check section now imports the pinned Stepik reference into the current task folder via `POST /api/import-reference` (non-browser session, path confined to the workspace) and refreshes the solutions list, so imported references show up for comparison in modes 2–4 (#466, #55).
- Web grading now records runs to the local history DB (`source="web"`) so the «Подучить» section fills up in `--serve` — history is on by default for `--serve` (opt-out with `--no-history`); the grading→history helpers moved to `core/history_recording` so both CLI and web share them (#395).
- Lint violations now flow into history (`run_lint → LintRecord → record_run(lint=...)`) in CLI modes 1/2 (`--lint --history`) and web grading, so «Подучить» surfaces personal PEP-8 cards; ruff runs once per solution, shared by the «Стиль» print and the history record (#403).
- «Подучить» now reports time-to-first-green per task (attempts + time to the first full AC) in `--insights` and `GET /api/progress`, computed on the fly from history with no stored state (#431).
- `--export-progress md|html` exports aggregate progress (per-task TTFG, verdict and failure-kind tallies — never solution source) to a self-contained file; empty history is a friendly message, not an error (#432).
- The web «Правила» section highlights rules you've personally violated (`violated` flag on `/api/rules`, from history lint) — completes the deferred highlight from #403.
- `--history`/`record_history`: opt-in SQLite-история прогонов (`.grader_history.db`, `core/history.py` — схема v1 runs/case_results/lint_violations, WAL, `user_version`-миграции, best-effort); по умолчанию выключена, фундамент будущих разделов «Правила»/«Подучить» (#344).
- `rules/` — база карточек правил PEP 8: `RuleCard` + `JsonRulesProvider` + bundled `pep8_ru.json` (≥30 кодов E/W/F); общий mtime-кеш вынесен из web-адаптера в `core/mtime_cache` (#345).
- `core/lint.py` — opt-in PEP-проверка решения через ruff (extra `[lint]`, best-effort, не влияет на вердикт) + опциональное поле `lint` в контракте результата (#346).
- `core/insights.py` — таксономия падений (`failure_kind`) + затухание карточек «Подучить» (`classify_status`/`learning_cards`: active/fading/archived/watch по номерам прогонов, пороги N/T/K в конфиге); `failure_kind` пишется в историю при `--history` (#347).
- CLI-витрина инсайтов: флаг `--insights` (сводка карточек «Подучить» + пункт меню «5. Подучить») и `--lint` для режимов 1/2 (блок «Стиль» из ruff-нарушений с однострочником правила) (#349).
- Web API инсайтов (backend, часть #348): эндпоинты `GET /api/rules` (`?q&tag`), `/api/rules/{code}`, `/api/insights` + адаптеры `web/rules_adapter`/`web/insights_adapter` поверх core-слоя (#345/#347).
- Web UI разделов «Правила»/«Подучить» (#348): list+detail правил (поиск, чипы-теги, deep-link `#/rules/<code>`, примеры «до/после»), карточки «Подучить» со статусом затухания + бейдж активных в sidebar, единый hash-роутер (замена glossary-only, риск R6). Закрывает эпик #342.
- Двуязычные описания карточек глоссария (фундамент эпика #363): `summary`/`body` хранятся вложенным `{ru,en}`, web-API (`/api/glossary`, `/api/glossary/<id>`, `/api/code-terms`) отдаёт их строкой по `?lang=` с fallback на RU (`GlossaryCard.to_api_dict`/`localized`, обратная совместимость плоских `card.summary`); селект «Язык интерфейса» в «Настройках» переключает глоссарий на лету (#363).
- Глоссарий, волна В1 «Строки (str)» (#371): 45 методов `str` доведены до `ready` гранулярными двуязычными карточками (RU+EN `summary`, примеры `# → результат`, прогнанные в Python); групповые карточки (`capitalize/title/swapcase`, `find/rfind`, `split/rsplit` и др.) расформированы на отдельные методы, черновики перенесены из `drafts.json` в `str.json` (#363).
- Глоссарий, волна В1 «Числа» (#363): 13 методов `int`/`float`/`complex` (`bit_length`/`bit_count`/`to_bytes`/`from_bytes`/`as_integer_ratio`/`is_integer`/`conjugate`/`hex`/`fromhex`) доведены до `ready` per-method двуязычными карточками `int.X`/`float.X`/`complex.X` (RU+EN `summary`, примеры `# → результат`, проверенные конвейером `glossary_draft_pipeline` #438); групповая карточка `int.bit_length/to_bytes/from_bytes` расформирована на отдельные методы, черновики перенесены из `drafts.json` в `op.json`.
- Глоссарий, волна В1 «Списки» (#363): 11 методов `list` (`append`/`clear`/`copy`/`count`/`extend`/`index`/`insert`/`pop`/`remove`/`reverse`/`sort`) переведены на per-method двуязычные карточки `list.X` (RU+EN `summary`, примеры `# → результат`, проверенные конвейером #438); голые/групповые карточки (`append`, `sort-sorted`, `reverse-reversed`, …) консолидированы в qualname, тематические (срезы, comprehension, распаковка) сохранены, черновики перенесены `drafts.json`→`seq.json`. Точность coverage: `operator.index`/`os.remove` больше не покрываются ложно короткими именами голых карточек — вернулись в очередь пробелов до своих волн (В4/В5).
- Глоссарий, волна В1 «Словари» (#363): 10 методов `dict` (`clear`/`copy`/`get`/`items`/`keys`/`pop`/`popitem`/`setdefault`/`update`/`values`) переведены на per-method двуязычные карточки `dict.X` (RU+EN `summary`, примеры `# → результат`, проверенные конвейером #438); групповые карточки, смешивавшие методы с синтаксисом, расформированы: методы ушли в `dict.X`, а синтакс-операции перефокусированы в отдельные карточки `del d[key]` / доступ `d[key]` / `d[key] = value`. Тематические (dict-comprehension, слияние, defaultdict/Counter, вложенные) сохранены, черновики перенесены `drafts.json`→`mapset.json`.
- Глоссарий, волна В1 «Множества» (#363): 25 методов `set`/`frozenset` доведены до `ready` per-method двуязычными карточками (17 `set.X`: `add`/`update`/`remove`/`discard`/`pop`/`clear`/`copy`/`union`/`intersection`/`difference`/`symmetric_difference` + три `*_update` + `issubset`/`issuperset`/`isdisjoint`; 8 `frozenset.X`). RU+EN `summary`, `syntax`, примеры `# → результат` (детерминированные — `sorted()`/int-only), проверенные конвейером #438. Групповые карточки (`add-update`, `remove-discard-pop`, `union-intersection-difference-symmetric_`, …) консолидированы в qualname; тематические (операторы `|`/`&`/`-`/`^`, set-comprehension, создание, `frozenset`-тип, практика) сохранены, черновики перенесены `drafts.json`→`mapset.json`.
- Глоссарий, волна В1 «Кортежи» (#363): 2 метода `tuple` (`count`/`index`) переведены на per-method двуязычные карточки `tuple.X` (RU+EN `summary`, примеры `# → результат`, проверенные конвейером #438); групповая карточка `count-index-для-кортежа` расформирована, тематические (создание, распаковка, срезы, namedtuple, неизменяемость) сохранены. Завершает методы встроенных типов волны В1 (str/числа/list/dict/set/frozenset/tuple).
- Глоссарий, В1 хвост + В2 старт (#363): 3 встроенные функции (`len`/`aiter`/`anext`) завершают «Встроенные функции» волны В1, и **55 встроенных исключений** доведены до `ready` двуязычными карточками (`ArithmeticError`/`ZeroDivisionError`/`OverflowError`, `RuntimeError`, `SyntaxError`/`IndentationError`/`TabError`, `EOFError`, `KeyboardInterrupt`/`SystemExit`/`GeneratorExit`, `LookupError`, `Unicode*Error`, `OSError`-семейство `FileExistsError`/`IsADirectoryError`/`ConnectionError`/…, `ExceptionGroup`, всё семейство `Warning` и др.) — начало волны В2 «Исключения». RU+EN `summary`, примеры-`try/except` с сверкой типа прогоном через конвейер #438; аддитивно к 22 существующим `ready` в `exc.json`. Черновики `drafts.json` (726→668). Модульные исключения (65) — в следующих PR В2.
- Глоссарий, В2 модульные исключения (#363): 35 публичных stdlib-исключений из модулей доведены до `ready` двуязычными карточками — `json.JSONDecodeError`, `re.PatternError`, `struct.error`, `zlib.error`, `copy.Error`, `locale.Error`, `io.UnsupportedOperation`, `statistics.StatisticsError`, `dataclasses.FrozenInstanceError`, `argparse.ArgumentError`/`ArgumentTypeError`, `shutil.*` (6), `threading.BrokenBarrierError`, `tokenize.TokenError`, `zipimport.ZipImportError`, `signal.ItimerError` и вся иерархия `decimal.*` (14 сигналов). RU+EN `summary`, примеры-`try/except` с сверкой короткого `type(e).__name__` прогоном через конвейер #438. Черновики `drafts.json` (668→633). Осознанно **не** наполнялись: приватные (`_pickle.*`/`_frozen_importlib.*`), сторонний `rich.*` и собственный `stepik_grader.*` — они не входят в официальный Python/stdlib (инвариант истины глоссария) и требуют фильтра в инвентаре (отдельная задача).
- Глоссарий, В3 «Учебные модули», батч 1 (#363): 24 функции/итератора трёх ходовых модулей доведены до `ready` двуязычными карточками — `json` (`dumps`/`loads`/`dump`/`load`), `functools` (`lru_cache`/`cache`/`cmp_to_key`/`update_wrapper`), `itertools` (16: `count`/`cycle`/`repeat`/`combinations`/`permutations`/`product`/`combinations_with_replacement`/`compress`/`dropwhile`/`takewhile`/`filterfalse`/`starmap`/`pairwise`/`batched`/`zip_longest`/`tee`). RU+EN `summary`, детерминированные примеры `# → результат`, проверенные конвейером #438. Групповые карточки `itertools.combinations-permutations-prod`/`itertools.cycle-repeat-count` расформированы в qualname (`itertools.count` добавлен). Черновики `drafts.json` (633→610); coverage +1 (`itertools.count`).
- Глоссарий, В3 «Учебные модули», батч 2 (#363): 23 карточки модулей `datetime` и `statistics` до `ready` двуязычными карточками — `datetime` (5 классов: `date`/`datetime`/`time`/`timezone`/`tzinfo`), `statistics` (18: `mean`/`fmean`/`geometric_mean`/`harmonic_mean`, `median`/`median_low`/`median_high`/`median_grouped`, `mode`/`multimode`, `stdev`/`pstdev`/`variance`/`pvariance`, `correlation`/`covariance`/`linear_regression`, `NormalDist`). RU+EN `summary`, детерминированные примеры `# → результат` (float через `round()` где нужно), проверенные конвейером #438. Групповые `statistics.mean-median-mode`/`statistics.stdev-variance-pstdev` расформированы в qualname (`statistics.quantiles` сохранена). Черновики `drafts.json` (610→587).
- Глоссарий, В3 «Учебные модули», батч 3 (#363): 16 карточек модулей `textwrap`/`dataclasses`/`re` до `ready` двуязычными карточками — `textwrap` (6: `dedent`/`indent`/`wrap`/`fill`/`shorten`/`TextWrapper`), `dataclasses` (5: `asdict`/`astuple`/`is_dataclass`/`make_dataclass`/`InitVar`), `re` (5: `sub`/`subn`/`purge`/`Pattern`/`RegexFlag`). RU+EN `summary`, детерминированные примеры `# → результат`, проверенные конвейером #438. Групповые `dataclasses.asdict-astuple`/`re.sub-re.subn` расформированы в qualname (`re.group-re.groups` сохранена). Черновики `drafts.json` (587→571).
- Глоссарий, В3 «Учебные модули», батч 4 (#363): 15 карточек `enum`/`pathlib` до `ready` двуязычными карточками — `enum` (10: `IntEnum`/`StrEnum`/`Flag`/`IntFlag`/`ReprEnum`/`member`/`nonmember`/`verify`/`EnumCheck`/`FlagBoundary`), `pathlib` (5 классов путей: `PurePath`/`PurePosixPath`/`PureWindowsPath`/`PosixPath`/`WindowsPath`). RU+EN `summary`, детерминированные примеры `# → результат` (классы enum через определение, пути через `Pure*` и проверки подклассов — OS-независимо), проверенные конвейером #438. Черновики `drafts.json` (571→556).
- Глоссарий, В3 «Учебные модули», батч 5 (#363): 26 функций модуля `math` доведены до `ready` двуязычными карточками — гиперболические (`sinh`/`cosh`/`tanh`/`asinh`/`acosh`/`atanh`), спецфункции (`erf`/`erfc`/`gamma`/`lgamma`), корни/степени (`isqrt`/`cbrt`/`exp2`/`expm1`/`log1p`), представление (`frexp`/`modf`/`ldexp`/`ulp`/`nextafter`/`copysign`), остаток (`fmod`/`remainder`), `dist`/`sumprod`/`fma`. RU+EN `summary`, детерминированные примеры `# → результат` (точные значения; `cbrt` через `round()`), проверенные конвейером #438. Завершает функции модуля `math`. Черновики `drafts.json` (556→530).
- Глоссарий, В3 «Учебные модули», батч 6 (#363): 18 карточек `contextlib`/`abc`/`string` до `ready` двуязычными карточками — `contextlib` (9: `redirect_stdout`/`redirect_stderr`/`chdir`/`AbstractContextManager`/`AbstractAsyncContextManager`/`ContextDecorator`/`AsyncExitStack`/`asynccontextmanager`/`aclosing`), `abc` (8: `ABC`/`ABCMeta`/`abstractmethod`/`abstractproperty`/`abstractclassmethod`/`abstractstaticmethod`/`get_cache_token`/`update_abstractmethods`), `string.Formatter`. RU+EN `summary`, детерминированные примеры `# → результат` (протоколы CM/ABC — через `issubclass`/`__abstractmethods__`, перенаправление — через `io.StringIO`), проверенные конвейером #438. Аддитивно. Черновики `drafts.json` (530→512).
- Глоссарий, В3 «Учебные модули», батч 7 (#363): 24 протокольных ABC модуля `collections.abc` доведены до `ready` двуязычными карточками — `Iterable`/`Iterator`/`Reversible`/`Generator`, `Sized`/`Container`/`Collection`/`Hashable`, `Sequence`/`MutableSequence`, `Mapping`/`MutableMapping`, `MutableSet`, `KeysView`/`ValuesView`/`ItemsView`/`MappingView`, `Buffer`/`ByteString` (deprecated), async-протоколы (`Awaitable`/`Coroutine`/`AsyncIterable`/`AsyncIterator`/`AsyncGenerator`). RU+EN `summary`, педагогичные примеры `# → результат` через `isinstance` (какой встроенный тип удовлетворяет какому протоколу: `set` — `MutableSet` True, `frozenset` — False, `[]` — `Hashable` False). Проверено конвейером #438. Аддитивно. Черновики `drafts.json` (512→488).
- Глоссарий, В3 «Учебные модули», батч 8 (#363): 15 карточек `collections.User*`/`io` доведены до `ready` двуязычными карточками — `collections` (3 обёртки типов: `UserDict`/`UserList`/`UserString`), `io` (12: абстрактные базы `IOBase`/`RawIOBase`/`BufferedIOBase`/`TextIOBase`, бинарные потоки `FileIO`/`BufferedReader`/`BufferedWriter`/`BufferedRandom`/`BufferedRWPair`, служебные `IncrementalNewlineDecoder`/`text_encoding`/`open_code`). RU+EN `summary`, детерминированные примеры `# → результат` (иерархия классов через `issubclass`/`isinstance`, буферизованные потоки — конструктивно поверх `BytesIO`), проверенные конвейером #438. Аддитивно. Черновики `drafts.json` (488→473).
- Глоссарий, В3 «Учебные модули», батч 9 (#363): 23 карточки `random`/`enum`-внутренностей доведены до `ready` двуязычными карточками — `random` (14: распределения `betavariate`/`binomialvariate`/`expovariate`/`gammavariate`/`lognormvariate`/`normalvariate`/`paretovariate`/`triangular`/`vonmisesvariate`/`weibullvariate`, сырые биты/байты `getrandbits`/`randbytes`, состояние ГСЧ `getstate`/`setstate`), `enum` (9: метакласс `EnumType`/`EnumMeta`/`EnumDict`, глобализация `global_enum`/`global_enum_repr`/`global_flag_repr`/`global_str`, pickle `pickle_by_enum_name`/`pickle_by_global_name`). RU+EN `summary`, версо-стабильные примеры (распределения — проверки диапазона/типа, `getrandbits` — точное значение под seed, `randbytes` — длина, `getstate`/`setstate` — round-trip, `EnumDict` — робастно для 3.12), проверенные конвейером #438. Закрывает крупные модульные группы В3; аддитивно. Черновики `drafts.json` (473→450).
- Глоссарий, В4 «Операторы и типизация», батч 10 (#363): 52 функции модуля `operator` доведены до `ready` двуязычными карточками — арифметика (`add`/`sub`/`mul`/`truediv`/`floordiv`/`mod`/`neg`/`pos`), битовые (`and_`/`or_`/`xor`/`invert`/`inv`/`lshift`/`rshift`), сравнение (`eq`/`ne`/`lt`/`le`/`gt`/`ge`), тождество/логика (`is_`/`is_not`/`not_`/`truth`), последовательности (`concat`/`contains`/`countOf`/`indexOf`/`getitem`/`setitem`/`delitem`/`length_hint`), `matmul`, вызов/доступ (`call`/`itemgetter`/`attrgetter`/`methodcaller`) и 14 in-place (`iadd`…`imatmul`). RU+EN `summary`, детерминированные примеры `# → результат` (matmul/imatmul — через классы с `__matmul__`/`__imatmul__`), проверенные конвейером #438. Начинает волну В4; аддитивно. Черновики `drafts.json` (450→398).
- Глоссарий, В4 «Операторы и типизация», батч 11 (#363): 37 имён модуля `typing` доведены до `ready` двуязычными карточками — интроспекция (`get_args`/`get_origin`/`get_type_hints`/`get_overloads`/`get_protocol_members`/`is_protocol`/`is_typeddict`), приведение/отладка (`cast`/`reveal_type`/`assert_type`/`assert_never`), обобщения/параметры (`Generic`/`NewType`/`ParamSpec`/`ParamSpecArgs`/`ParamSpecKwargs`/`TypeVarTuple`/`TypeAliasType`/`ForwardRef`), потоки I/O (`IO`/`BinaryIO`/`TextIO`/`Text`), протоколы `Supports*` (7), декораторы/маркеры (`overload`/`override`/`no_type_check`/`no_type_check_decorator`/`runtime_checkable`/`dataclass_transform`/`clear_overloads`). RU+EN `summary`, версо-стабильные примеры (3.13+-имена `is_protocol`/`get_protocol_members` — через getattr-fallback; `reveal_type` — через `callable`), проверенные конвейером #438. Завершает волну В4. Черновики `drafts.json` (398→361).
- Глоссарий, В5 «Системные модули», батч 12 (#363): 30 функций `os.path` доведены до `ready` двуязычными карточками — разбор пути (`basename`/`dirname`/`split`/`splitdrive`/`splitroot`/`normcase`/`normpath`/`commonprefix`/`commonpath`/`relpath`), абсолютизация (`abspath`/`realpath`/`expanduser`/`expandvars`/`isabs`), проверки существования/типа (`exists`/`lexists`/`isdir`/`isfile`/`islink`/`ismount`/`isjunction`/`isdevdrive`), метаданные (`getsize`/`getmtime`/`getatime`/`getctime`/`samefile`/`samestat`/`sameopenfile`). RU+EN `summary`, **кросс-платформенно детерминированные** примеры `# → результат` (вывод сверен в `posixpath` и `ntpath`; stat/exists — через `os.__file__`, присутствующий на всех ОС), проверенные конвейером #438. Начинает волну В5; аддитивно. Черновики `drafts.json` (361→331).
- Web: `--serve --sandbox` now isolates code execution — the `SandboxRunner` is injected as the active runner and the grade, playground and microbench paths all honor it; the step tracer refuses under `--sandbox` (its module can't be exposed to the isolated child, so it returns a clear error instead of running unsandboxed) (#396).
- Interactive menu now loops until `0`/EOF instead of exiting after one action, adds a «Веб-интерфейс» item (launches `--serve`, Ctrl+C returns to the menu), hints at the file/folder picker on the path prompt, and reports the substituted default on non-numeric profile input (#445).
- Interactive menu can toggle run-history recording (item 7, persisted between runs in `.grader_settings.json` via the new `core/user_settings` layer); after a failing run with history off, a one-line «Подучить» nudge is printed. History stays opt-in in the CLI (ADR-0002), and the web insights empty-state now points to a web action instead of the `--history` CLI flag (#430).
- `downloader` offers a guided first-run wizard that creates `secrets.json` (client_id/secret + default `redirect_uri`, written `0600`) instead of a raw `FileNotFoundError`, printing the Stepik OAuth-app link and required fields, with a next-step hint on auth failure (#433).
- Web `--serve` gains a first-run browser OAuth wizard: the «Загрузчик задач» section checks the token (`GET /api/auth/status`) and, when missing, offers a client_id/secret form whose «Authorize» button runs the loopback OAuth flow as an async job (`POST /api/auth/start` → `kind="auth"`, polled via `/api/v1/runs/{id}`) and writes `secrets.json` (`0600`) — no manual secrets file or CLI step needed; thin `web/auth_adapter.py` over `core/oauth_flow` (#402).
- `README.en.md` — an English entry point (install, quick start, generic mode for your own non-Stepik tests across formats 1–3, links to the bilingual web UI/glossary via `?lang=en`), linked from `README.md`; canonical content stays in `docs/*`, linked rather than duplicated (#437).

### Changed
- Glossary web section shows only `status=ready` cards by default (787 auto-draft cards hidden unless `?status=all`/`draft` is chosen); privately-named auto-drafts (`os._exit`, `_pickle.X` — not dunders, ~11 cards) are always excluded from student-facing search/lists. The private filter applies only to non-`ready` cards, so hand-authored `ready` cards (incl. OOP dunder-operator cards) stay visible; the missing-term detector and queue still see the full base (#436).
- Web sidebar cleanup: dropped the dead «Рабочее пространство» label and the disabled «Настройки» stub → a working Settings section (theme/language, landing spot for the #342 history toggle); recent paths moved to a path-field `datalist` (#364).
- Web config panel: removed the «Путь»/«Параметры» tabs — mode 3/4 params render inline under the path field, «Функции в коде» shows only in mode 1 (#366).
- Web result panel: merged «Детали»+«Лог» into one «Разбор» tab (side-by-side «Ожидалось/Получено» + collapsible raw stdout/stderr); mode-2 action cards limited to copy input/output (#368).
- Web modes 3/4: stacked layout (config strip on top, results full-width) + benchmark tables/KPI aligned to the CLI reporter (Mean/Max/Std dev added to mode 3) (#370).
- Web: check-mode buttons now lead with the task («Один файл / Папка / Бенчмарк / Микробенчмарк») and demote «Режим N» to a secondary caption instead of CLI-jargon-first; «Переключить раздел» cycles all 7 sections via one shared `SECTIONS` registry used by both `setSection`/`switch_section` (rules/insights/settings were silently dropped — a regression of #317); an internal issue number was removed from a user-facing tooltip (#428).

### Removed
- Web: the localStorage «История» block in the config panel (product history returns on SQLite in «Подучить», epic #342) (#365).
- Web: the always-empty «Эталон» result tab (a reference is a `REFERENCE` row in the modes 3/4 tables; #55 backend groundwork kept) (#369).
- `core/executor.py` (dead `run_solution`/`RunResult`/`main`, superseded by `LocalRunner` since #138 — only its own tests exercised it) and the now-unused `executor_timeout` config field / `EXECUTOR_TIMEOUT` env var; execution has a single path (`LocalRunner`), `grader.py` `__all__` unchanged (#406).

### Internal
- `scripts/glossary_draft_pipeline.py` — semi-automatic B1 draft pipeline: builds a card by the wave-B1 template (RU+EN summary + examples) via a swappable `DraftProvider` (offline default, BYOK-LLM an opt-in seam — no runtime deps), **validates examples by execution** (`# → result` compared to actual stdout), and prints a review diff; broken examples block the write, nothing is auto-merged into the ready base. A `check` mode audits existing cards' examples (surfaced 5 non-reproducing `ready` examples). Closes the last child of the 2026-07-15 audit epic #416 (#438).
- `tests/test_import_dag.py` — AST-based DAG guard: the `stepik_grader` import graph stays acyclic (load-time imports) and the leaf modules (`core/storage`/`normalizers`/`glossary`) import nothing from the project, codifying CLAUDE.md invariants 1–2 (#410).
- `web/server.py` routing moved from the growing `do_GET`/`do_POST` if/elif chains to declarative per-method route tables (`_API_GET_EXACT`/`_API_GET_PREFIX`, `_API_POST_EXACT`/`_API_POST_PREFIX`) dispatched by a single `_dispatch` (exact match → ordered prefix + optional suffix → 404); each endpoint is now a small `_get_*`/`_post_*` handler (body endpoints share a `_guard_and_read_body` preamble), so a new endpoint is one table entry + one function. HTTP contract unchanged — the web/e2e suites pass without touching expectations (#427).
- Added a contract test (`tests/test_web_api_contract.py`) reconciling the endpoints documented in `docs/api.md` (the `## `METHOD /path`` sections) with the actual routes read from `_Handler`'s declarative tables (#427) plus the special `GET /`: it fails on a route with no doc section OR a doc section with no route, guarding the 21-endpoint HTTP surface against drift with no network or extra dependencies (#439).
- `web/static/app.js` (2673 lines) split into ES modules — `core.js` (primitives/state/editor + nav-hub via a section-hook registry), `grade.js`, `sandbox.js`, `trace-player.js`, `content.js` (glossary/rules/insights), `downloader.js` — leaving `app.js` as the entry that wires listeners + bootstrap; `web/server.py` now builds the static text-route allowlist by scanning `static/*.js` at import (still an exact-key allowlist, no path-traversal surface) so a new module serves without editing the list, and the frontend source-regression tests grep the new `_STATIC_JS_SOURCES` (all modules). UI behavior unchanged — verified by the existing e2e/web suites (#426).
- Pre-commit `ruff` hook bumped `v0.11.13` → `v0.15.21` to match `pyproject.toml` (`ruff>=0.15.19`) and CI; dropped the now-obsolete `--unsafe-fixes` (added only for the since-removed `UP038` rule) that silently rewrote `core/tracer.py` hot-path `isinstance`-tuples to the slower PEP 604 form, and renamed `id: ruff` → `ruff-check` (legacy alias in 0.15.x) (#467).
- Pre-commit whitespace hooks (`end-of-file-fixer`/`trailing-whitespace`/`mixed-line-ending`) now skip the generated vendored CodeMirror bundle (`vendor/*.mjs`) so `pre-commit run` can't mangle the esbuild artifact; pre-existing trailing-whitespace/EOF drift in `docs/` and workflow files was normalized so the run is a clean no-op (#467).
- `scripts/check_contrast.py` + `tests/test_contrast.py` enforce WCAG contrast of design-token pairs (button, badges, muted, placeholder, active states) in both themes, so a token regression fails the test suite (#424).
- CI matrix now covers Windows + Python 3.14 (experimental, non-blocking) — `requires-python = ">=3.12"` promises 3.14 but it was only exercised on ubuntu, leaving the Job Objects sandbox backend and the project's main desktop platform unverified there (#456).
- CI matrix extends Python 3.14 to macOS too (experimental, non-blocking), so all three OSes exercise 3.14 and the `sandbox-exec` backend is verified on the newest Python (#456).
- CI now exercises the Linux `bwrap` sandbox backend for real: a dedicated `sandbox-linux` job runs the full `test_sandbox_runner.py` (FS isolation, real network isolation, memory, output-size, timeout) inside a privileged container — GitHub Actions forbids unprivileged user namespaces (uid-map/netns), so a privileged container is the only way to run it on GHA; `_linux.py` coverage is merged into the combined report (fork-bomb deselected as its ucounts containment is nested-userns-dependent) (#420).
- Grading ranking centralised in `core/microbench_runner`: reference ranking moved out of `web/viewmodels`, benchmark verdict unified to `MUCH_SLOWER`, and `run_tests` now carries per-case `stdin` so the web path no longer re-reads test cases (drops the mode-1 `zip(strict=True)` fragility from #422) (#397).
- `run_single_test` decomposed into `_prepare_run_spec` (strategy/wrapper → `RunSpec`) and the pure `_map_outcome_to_result` (`RunOutcome` → verdict dict), so every verdict branch (RE/TLE/CANCELLED/SANDBOX_VIOLATION/AC/WA) is unit-tested in isolation without a subprocess (#406).
- Codified mypy strictness in `[tool.mypy]` (`disallow_untyped_defs`, `disallow_incomplete_defs`, `check_untyped_defs`, `warn_return_any`, `ignore_missing_imports`) so the achieved level is enforced by config, not a CI flag; the type-check command is now just `mypy src/stepik_grader`, and the 10 remaining `no-any-return` sites (normalizers/cli.options/tracer/stepik_client/sandbox._windows/diagnostic_stepik) were fixed. `warn_unused_ignores` deliberately left off — platform-specific ignores would false-positive per-OS (#441).
- `CaseResult` TypedDict formalizes the `run_single_test` case-result contract (`core/result.py`) and flows through `_fail_result`/`_map_outcome_to_result`, `reporter.print_case_verbose`, `pytest_plugin` and `web.viewmodels._case_view`; `TestResult.from_dict` widened to `Mapping`. Runtime dict shape and JSON output unchanged (#442).
- Narrowed `Path | str` path signatures to `Path` (`oauth_flow`, `ide.write_vscode_tasks`, `glossary.json_provider`) and dropped the defensive `pathlib.Path(...)` wrappers per issue #73; `diagnostic_stepik` dicts typed (`dict → dict[str, Any]`, 8 `type: ignore[type-arg]` removed) (#407).
- Expanded the ruff lint select (`PTH` / `SIM105` / `SIM115` / `RUF022` / `RUF100` / `D101`-`D103`) to enforce the "pathlib only" and "public-API docstrings" CLAUDE.md invariants in CI; ignores `RUF001`-`003` (Cyrillic is legitimate) + `PTH201` (`Path(".")` stays), and D1 is scoped to `src/` (tests/scripts/conftest exempt). Cleared every resulting violation — `os.unlink`/`os.chmod`/`os.replace`/`os.readlink`/`os.path.basename`/`open()` → `Path.*`, `try`/`except`/`pass` → `contextlib.suppress`, intentional `NamedTemporaryFile(delete=False)` marked `# noqa: SIM115`, dead `noqa` removed, `__all__` sorted, and missing public-API docstrings added (#443).
- Hot-path glossary reads stop rebuilding derived structures per request: `GlossaryCard.search_terms` became a `cached_property`, `glossary_get`/`code_terms` share an mtime-keyed id/concept index (`_INDEX_CACHE`) instead of an O(n) scan / per-call `sorted()` rebuild, and `viewmodels._known_glossary_terms` memoizes the store's terms by mtime (no full ~1.3 MB reparse per RE case); the Stepik client's application-level retry loop was dropped in favor of the single transport `urllib3.Retry` (one source of backoff/`Retry-After`), keeping `raise_for_status` + diagnostic logging (#404).
- First property-based tests (hypothesis, new `[dev]` dep): `parse_testblock_file` block-count-equals-marker-count invariant and `normalize_floats` never-raises / idempotent / line-count-preserving properties; the tautological monkeypatched-`float` test was replaced with real overflow (`1.0e999 → inf`) and idempotency inputs, and the unreachable defensive `except ValueError` branch marked `# pragma: no cover` (#405).
- Added a soft `docs/versions.md` release-column guard to `check_version_consistency.py`; archived `claude-handoff.md`, stamped `audit-2026-07.md` as implemented, fixed the stale CHANGELOG policy in GitHub PR/issue templates and the #163 contradiction in CLAUDE.md (#386).
- Added a global 120s per-test deadline (`pytest-timeout`, `thread` method) so a hung subprocess/thread fails one test instead of hanging every CI matrix job (#444).

### Fixed
- Fixed 5 non-reproducing `ready` glossary examples surfaced by `glossary_draft_pipeline check`: the hardcoded `os.chdir('/home/user/workspace')`, the `min([])`/`int('3.14')` error demos (now `try/except`), a misplaced `dir()[:5]` slice, and an invalid f-string align spec (`{width}^`→`^{width}`); `check` on Python 3.13 now reports `error=0` (#504).
- `normalize_floats` no longer mangles dotted numbers — versions (`3.10.5`) and IPv4 (`1.2.3.4`) survive (regex lookbehind/lookahead), while plain floats still round to 9 places (#410).
- `parse_testblock_file` skips `# INPUT DATA:` only as the file header (before the first `# TEST_N:`); the same line inside a block is now kept as real case data instead of being silently dropped (#410).
- Security: the diagnostic logger redacts secrets in exception tracebacks (`exc_info`) and `stack_info`, not just the message — a token in an exception text no longer leaks to `grader.log` (#410).
- Async-run TTL is measured from completion (`Job.completed_at`, stamped on the transition to a terminal status) instead of from queue time, so a bench/microbench that runs longer than the 15-minute TTL stays retrievable after it finishes — `GET /api/v1/runs/{id}` no longer 404s a just-finished long run; and `storage.save_json_file` now writes atomically (unique temp in the same dir + `fsync` + `os.replace`, preserving existing perms), so an interrupted/concurrent write to the grader cache / `meta.json` / downloader config can't leave a truncated file (#408).
- Microbench now actually applies `WARMUP_RUNS` (previously a dead constant covered only by an existence test): it runs that many untimed dry executions of the statement before `timeit.repeat` and outside `tracemalloc`, so cold-start (imports/lazy init/caches) no longer inflates the reported min/median or peak memory. The bench-script build is extracted to a testable `_build_bench_script`, and the constant tests now assert the warmup is wired into the script (#412).
- Web code editors now use a theme-driven CodeMirror syntax highlight style built on `--cm-*` CSS variables (≥4.5:1 in light and dark) instead of the light-only `defaultHighlightStyle` that rendered keywords at ~1.85:1 on the dark editor background; the vendored bundle was rebuilt to export `HighlightStyle`/`tags` (#425).
- Web dark/light theme contrast now meets WCAG AA: primary-button text decoupled via `--color-on-primary`/`--color-primary-btn`, brighter dark `--color-text-muted`/`--color-primary`/`--color-error` (+ darker highlights), new `--color-text-placeholder`, and `.section-heading`/`.term-card-kind` moved off `--color-text-faint` (#424).
- Web `.hint` help text now has its own muted style instead of rendering as body text, code editors expose an `aria-label` to screen readers (`.cm-content`), and the mobile sidebar keeps its group divider across all 7 sections (#409).
- CLI reporter width now adapts to the terminal (`min(terminal, 200)`) so correctness/benchmark tables and separators no longer wrap or bloat on 80–120-column terminals (#409).
- Interactive menu no longer crashes when `.grader_settings.json` can't be written (read-only cwd / full disk): the history toggle degrades to session-only; menu docs now show items 6/7 and the `[0-7]` prompt; the recursive `_print` fallback in `downloader_config` was fixed (#445, #430, #433).
- Web OAuth hardening (#402 review): `/api/auth/status` survives a malformed `expires_at` instead of 500-ing, re-auth preserves an existing `refresh_token`, `redirect_uri` must be loopback, and the browser wizard stops polling on a vanished job; the CLI wizard no longer writes an empty `secrets.json` (#402, #433).
- `--sandbox` on Linux now binds `/usr` read-only so the interpreter's ELF loader (`ld-linux`) and `libc` are reachable even when Python lives outside `/usr` (Docker `python` images, CI hostedtoolcache, pyenv) — previously bwrap failed with `execvp: No such file` on such interpreters (#420).
- Mode-4 microbench "much slower" solutions are now classified as slow in «Подучить» insights — the verdict was emitted as `"MUCH SLOWER"` (space), which `insights._BENCH_SLOW` (underscore-only) silently skipped (#397).
- Web «Функции в коде»: inventory-driven builtin/method detection (stdlib inventory instead of a narrow hardcode), syntax-construct detection (comprehensions, lambda, slice, f-string, unpacking, ternary, walrus, decorator, with, try), and `os.path.join` no longer misdetected as the `str.join` method (#367).
- CLI modes 1–4 no longer crash with `ValueError` on a relative solution path — `reporter._safe_rel`/`cli._rel` fall back to the raw path when path and base have different anchors (#440).
- Runner no longer hangs or leaks CPU-orphans on TLE/cancel: kills the whole solution process group (POSIX `killpg` + psutil tree, bounded reap), writes stdin off-thread (stdin-deadlock fix), and returns partial stdout/stderr on timeout — mirrored in the sandbox POSIX path (#418, #419, #421).
- SQLite history survives concurrent first-time init (idempotent `CREATE ... IF NOT EXISTS` — no more silently lost records) and closes the connection if `_connect` fails mid-setup (#393).
- Re-downloading a task clears `tests/` first, so stale cases from a previous download can't produce a silent wrong verdict (#394).
- Web: cancelling a tests/trace job reports `cancelled` instead of a `zip()` error or `done`; non-UTF8 solution files return a JSON error instead of a 500 on `/api/source`, `/api/code-terms`, and bench reference (#422, #423).
- Security: OAuth tokens are written with `save_secrets` (atomic, `0600`) instead of `save_json_file` (`0644`), closing a world/group-readable gap that bypassed #243 (#400).
- Security: the CSRF guard now rejects `Sec-Fetch-Site: cross-site` requests (Fetch Metadata), covering a cross-site request even when Origin/Referer are absent; non-browser clients are unaffected (#399).
- Security: `POST /api/download` now confines its `root` (download target) to the workspace — an out-of-root `root` is rejected 403 instead of letting `download_task` `mkdir` arbitrary directories (#401).
- Security: the microbench (mode-4 stdin) path now runs its bench script through the active Runner instead of a bare `python -c`, so `--sandbox` actually isolates it — the `--sandbox --mode 4` bypass is closed (#417).
- Sandbox: the Linux bubblewrap backend recreates the top-level usrmerge symlinks (`/lib64` → `/usr/lib64`, …) inside the jail, so the solution's ELF loader resolves — `--sandbox` was unusable on usrmerge systems like modern Ubuntu (#420).

### Documentation
- Added four retroactive ADRs recording already-implemented decisions (#410, D3): ADR-0004 (src-layout, #35), ADR-0005 (dynamic versioning via `setuptools-scm`, #162), ADR-0006 (`Runner` execution abstraction, #140), ADR-0007 (opt-in OS sandbox backends, #157/#266) — closing the "no ADR for setuptools-scm/src-layout/sandbox/Runner" audit gap (#410).
- `CONTRIBUTING.md` «Процесс внесения изменений» gains an explicit "update `CHANGELOG.md` under `[Unreleased]` in every PR" step (#410).
- Added ADR-0003 (AI integration strategy): BYOK OpenAI-compatible over `requests` — one code path for cloud providers and local ollama, opt-in, no new dependencies, secrets redacted via `diag_log`; unblocks `--ai-hints` (#435) and the glossary LLM pipeline (#438) (#468, #434).
- ADR-0001 (server-mode direction) status corrected `Proposed` → `Accepted` (phases 1–2 are implemented: Runner layer #140, result-contract #116; full server mode stays an open v2.0 backlog); `docs/trace-format.md` now documents the `type` field every heap object carries — including the depth-limit `{type, repr}` and cycle-break `{type}` cases — that `core/tracer.py` has always emitted (#411).
- Synced the sandbox contract with the code after #396: `--serve --sandbox` isolates web execution (CLAUDE.md invariant 4, README, `docs/configuration.md`, `docs/web-current.md`, `docs/grader-workflow.md` claimed the opposite and pointed at the closed #351) (#453).
- Drift-proofed the agent-facing metric/glossary counts: CLAUDE.md's metrics table and coverage note now name the README badges as the living source (neutral "1600+ tests / ~92% single-OS / ~95% combined / 600+ `ready` cards ≈1388" instead of the stale "1317 / 93%"), CHECKPOINT.md's line is explicitly labeled a v1.8.0 release snapshot, and `docs/README.md` drops the drifting "832 draft cards" count — README/`web-current.md`/`glossary.md` already carried the correct 601-ready/≈1388 figures (#398).
- Marked Wave 2 done (#450/#451/#452) in `docs/claude-handoff.md`, refreshed the open-issue count to 46 and named Wave 3 as the entry point (#453).
- Added `docs/web-glossary-optimization-2026-07.md` — owner-requested plan
  (2026-07-14) with verified `file:line` coordinates covering (1) promoting all
  832 autodraft glossary cards to `ready` in six prioritized waves and (2) the
  UX optimization of the web "Проверка решений" section (owner items а–к):
  sidebar cleanup with a real «Настройки» section, removing the History block
  and the always-empty reference tab, merging the Details and Log tabs, a
  per-mode config panel without tabs, wider "Functions in code" detection
  (inventory-driven sets, constructs, `os.path.join` mismatch fix), and a
  stacked layout with CLI-aligned tables for modes 3/4. Filed as epics #362
  (web UX, children #364–#370) and #363 (glossary, pilot wave #371).
- Rotated CHANGELOG releases 1.1.0–1.5.0 into `docs/changelog-archive.md`; added
  a "one line per change, keep the 3 latest MINOR" policy (CLAUDE.md /
  CONTRIBUTING.md) and a CI version-count guard in `check_docs_guardrails.py` (#373).
- Docs audit after v1.8.0 (epic #381): closed the release gap — `versions.md` gains a v1.8.0 column, CHECKPOINT/CONTRIBUTING/installation refreshed (#382).
- Synced docs and code locales to the #342 wave — menu 0–5, six web sections, `insights_*` config keys, README feature list/security (#383).
- Added `docs/audit-2026-07-15.md` — multi-role audit (8 roles + fact-check of the external v1.8.0 audit template), verified by live repros; ships a 39-issue backlog in 7 epics under one umbrella epic. Role appendices under `docs/audit-2026-07-15/`.
- Fixed factual doc errors (logging `--verbose`, server-mode `cancelled`, `stepik-python-grader[watch]` package name) and marked the result `lint` field as schema-only until wired in #346 (#384).
- Refreshed `architecture.md`/`project-structure.md` — 5 missing modules, DAG edges, false-leaf/phantom-edge fixes (#385).
- Added `docs/issue-audit-2026-07-15.md` — full audit of all 253 issue against code (208 DONE, 98% closed honestly); filed epic #413 for the two real loose ends (#411 docs, #412 dead `WARMUP_RUNS`).
- Added `docs/audit-2026-07-14.md` — multi-agent 8-role deep audit of v1.8.0+[Unreleased]: reproduced SQLite history migration race + connection leak, empty web «Подучить» (history is CLI-only), web-without-sandbox, diffuse web↔core boundary, 3 web-boundary security gaps, glossary hot-path perf, doc metric drift; filed as epic #392 with 18 prioritized child issues (#393–#410).
- Added `docs/roles.md` — canonical 13-role response template; CLAUDE.md gains a compact «🎭 Режим ответов (роли)» trigger block so the roles apply on every request without bloating the file.
- Reactivated `docs/claude-handoff.md` with a wave-ordered work plan for all open issues — 6 waves + background, hard/soft dependency edges, code-verified corrections, and epic-dedup guidance; synced the CLAUDE.md § Открытая работа pointer (#447).
- Marked Wave 1 («стоп-краш/хэнг») done in the `docs/claude-handoff.md` work plan — all 9 issues (#444/#440/#418/#419/#421/#393/#394/#422/#423) closed via #448; open-issue count updated (#449).
- Server-mode sandbox-backend design (no code): `docs/server-sandbox-design.md` + ADR-0008 pin the backend *class* (OS container — namespaces + cgroups v2 + netns + seccomp, rootless with a privileged fallback) and map every #157 requirement onto Linux primitives; the concrete OCI runtime stays a deployment choice (#153).
- Server-mode data-model design (no code): `docs/server-data-model.md` + ADR-0009 specify one domain model with two storage backends — the PostgreSQL server schema is a superset of the local SQLite history (tenancy columns + native types, no duplicate model) — plus the accounts/workspaces/courses/tasks hierarchy, RBAC, and the SQLite→PG migration strategy (#154, #155).
- Dropped the stale "lint not wired yet" note from `docs/rules-insights.md` and `docs/result-contract.md` — the lint→history contour is closed (#403), the «lint» learning-card category is reachable (#495).
- Completed the `docs/configuration.md` reference: documented the `max_active_runs` + five `ai_*` config keys and the `CANCELLED`/`SANDBOX_VIOLATION` verdicts already declared by `core/result.py` (#497).
- Brought `docs/architecture.md` (module table + DAG) and `docs/project-structure.md` (tree) up to date with 6 shipped modules (`core/{ai_hints,history_recording,progress_export,user_settings,stepik_reference}`, `web/reference_adapter`) and the `core/locales/` directory (#496).
- Re-framed `docs/claude-handoff.md` as an archive: the 2026-07-15 wave plan is now a historical snapshot (all waves shipped), fixed the open-issue counter (8, not 46), dropped the "Wave 3 entry point", and marked #153/#154/#155 closed (#484) and #55 implemented (#498).
- Dropped the stale "roadmap" labels from the `--output csv/markdown` and `--watch` sections of `docs/grader-workflow.md` (both shipped) and documented the `--import-reference`/`--import-top` and `--export-progress` flags (#499).
- De-duplicated the incomplete `/api/*` route list in `docs/architecture.md` down to a pointer to the `docs/api.md` canon, and linked the `docs/grader-workflow.md` threat-model one-liner to its `docs/configuration.md` canon (#502).
- Re-framed the source-driven coverage source and the `/api/glossary*` endpoints in `docs/glossary.md` from "future/planned" to implemented (#195–#198 and #125/#129 are closed; modules and endpoints exist) (#501).
- Synced `docs/web-design.md` with the shipped web UI: marked web-OAuth (#402) and the reference-import button (#55) implemented (dropped the risky "don't touch until #55" directive), and added the Rules/Insights/Sandbox/Settings sections plus `#/rules`/`#/insights` deep-links to the status tracker (#500).
- Stamped the five one-off snapshot audits (`docs/audit-2026-07*.md`, `docs/issue-audit-2026-07-15.md`, `docs/web-glossary-optimization-2026-07.md`) and their `audit-2026-07-15/role-*.md` appendices as archives (work closed, read as history), and flagged them «(архив)» in the `docs/README.md` index; §9 wireframes kept, links intact (#503).
- Fixed cosmetic doc drift: `ADR-0003` field name (`ai_api_key`→`ai_api_key_env`), `ADR-0001`/`CHECKPOINT.md` #153-155/#55 status, the non-existent `MLE` verdict in `server-sandbox-design.md`, stale `logging.md` follow-up notes, `versions.md` setuptools-scm, `trace-format.md`/`server-mode.md` sandbox notes (#505, #506).
- CHANGELOG `[Unreleased]` hygiene: one-lined multi-sentence entries and merged the duplicate `### Internal`/`### Documentation` subsections (#373 policy) (#507).

## [1.8.0] - 2026-07-14

### Internal
- Replaced fixed `time.sleep` pauses in the async web-layer tests with a shared
  `wait_until(predicate, timeout, interval)` helper (`tests/_wait.py`, issue
  #357). The job-model and playground tests polled workers with hard-coded
  0.02–0.3s sleeps — timing-dependent and flaky on slow CI. They now wait on the
  actual condition (job status `running`, terminal status, pidfile appears,
  killed process reaped) with a deadline, so `test_runs.py`,
  `test_web_playground.py` and `test_web.py` no longer contain bare host-side
  `time.sleep` (the deliberate `time.sleep(30)` TLE fixtures inside solution
  bodies are untouched). Also faster — the suite returns as soon as the
  condition holds. Verified with 3 consecutive green web-suite runs.

### Refactored
- Unified the two glossaries behind one RE-hint resolver (issue #356): the CLI
  reporter and the web error card both resolved RuntimeError hints only from the
  compact `core/glossary.py` map (~28 exceptions), leaving the bundled JSON base
  (`glossary/data/`, ~140 exception cards) invisible to them. A new
  `core/error_glossary.py` (`resolve_error_hint`) now consults the bundled base
  first (by `id == exception name lowercased`) and fills empty fields from the
  compact map, so CLI and web show the same, richer card for covered exceptions.
  A single `card_url()` replaces the three previously divergent URL strategies
  (compact anchor, bundled `#e-<Name>`, empty). The bundled provider loads
  lazily and is mtime-cached; a broken/absent base degrades gracefully to the
  compact map. `core/glossary.py` stays a leaf, `core/reporter.py` still imports
  no web layer, and the DAG stays acyclic (`glossary/` does not import `core/`).
  Exception-name extraction is factored out into `exception_name_from_error`.
- Consolidated the CLI's two parallel i18n mechanisms into one (issue #355):
  the hardcoded `_MESSAGES` dict in `cli/__init__.py` (48 keys) is merged into
  the JSON locale catalog (`core/locales/{ru,en}.json`), and `_t()` is now a
  thin wrapper reading straight from `_LOCALE_MESSAGES` — no static fallback.
  New CLI strings now flow through the same JSON catalog as the web layer, so
  the ru/en parity guardrail (`scripts/check_locale_guardrails.py`) covers them
  too. The `_MESSAGES` keys did not overlap the existing web catalog, so the
  merge is a pure addition (35 → 83 keys per locale). `--lang`/auto-detection
  behaviour is unchanged; `test_i18n.py` is rewritten for the single-catalog
  model.
- Small code-style hygiene from the 2026-07 audit (issue #354, behaviour
  unchanged):
  - Added `__all__` to the 8 modules that lacked it (project checklist requires
    it): `downloader`, `diagnostic_stepik`, `pytest_plugin`,
    `core/{executor,stepik_client,storage,microbench_runner,parsers}`.
  - Bare `print()` → an `_console`-with-`print()`-fallback helper (rich,
    graceful, `markup=False`) in the three CLI-ish modules that used bare
    prints: `downloader.py` (19), `diagnostic_stepik.py` (20),
    `downloader_config.py` (8) — following the leaf-local `_console` pattern of
    `glossary/coverage.py`.
  - `os.path.relpath` → `Path.relative_to(other, walk_up=True)` (Python 3.12+,
    "paths are pathlib"): `cli/commands.py` (a new `_rel()` helper, 5 sites),
    `core/reporter.py` (4), `core/test_loader.py` (1, collapsing a
    try/relative_to/except-relpath into one line). `import os` dropped where it
    became unused.
  - `run_single_test` (`core/grader_core.py`): 7 near-identical early-return
    error dicts → a `_fail_result()` factory.
  - `main()` (`cli/__init__.py`): the duplicated watch/no-watch branches of
    modes 1 and 2 → a shared `_dispatch_with_watch()` helper.
  - Deduplicated the per-solution `test_dir` resolution shared by modes 2/3
    (`cli/commands.py`) into `_resolve_individual_test_dir()`.
- `downloader.py` SRP split (issue #302): the 32 KB module mixing config,
  HTML parsing, ZIP/GitHub download, format writing and interactive prompts
  is now a ~13 KB coordinator (`build_task_directory`, `save_task_files`,
  `process_step_url`, `main`). Extracted, each a focused module: HTML text
  parsing → `core/task_page_parser.py`, test-format writing →
  `core/tests_writer.py`, ZIP/GitHub fetching → `core/test_source_fetcher.py`,
  Stepik API/URL parsing → `core/step_content.py` (all leaf/near-leaf, no
  back-import of `downloader`), and config + interactive prompts →
  `downloader_config.py`. All previously public names remain importable from
  `stepik_grader.downloader` via re-export (back-compat verified by test); the
  duplicated Format-3 (`# TEST_N:`) writing in the two download paths is now a
  single `write_testblock_tests`. Behaviour and on-disk formats unchanged;
  downloader tests re-split across per-module test files.

### Fixed
- Documentation drift found by the 2026-07 audit (issue #353), all corrected
  against the actual code:
  - CLAUDE.md invariant #4 was titled "Нет sandbox", contradicting its own
    metrics footnote and the `core/sandbox/` backends — reworded to
    "sandbox is opt-in" (`--sandbox`/#266); the "no isolation by default" body
    is kept.
  - The diagnostic-logging epic #146 (`core/diag_log.py`, #341) was still listed
    as open in CLAUDE.md, CHECKPOINT.md and docs/claude-handoff.md — moved to
    "implemented".
  - docs/project-structure.md gained the three missing modules
    (`core/diag_log.py`, `core/tracer.py`, `web/playground.py`) and dropped the
    circular "core/ tree lives in CLAUDE.md" cross-reference.
  - Web docs: web-current.md § Layout no longer claims draggable splitters or a
    3-column detail panel (`.split-pane` is a fixed `1fr 1fr`; "Детали" is a
    tab); the "two sections / three blocks" framing is now four sections
    (Песочница/#314). web-design.md marks glossary deep-linking `#/glossary/<id>`
    as implemented (#329). api.md fixes the self-contradictory cancel response
    (200 for an existing job, 404 only when absent) and drops the non-existent
    `mode=file`. The `state.section` comment in app.js now lists all four values.
  - Metric drift (test count 1179 → 1308) is intentionally deferred to the
    v1.8.0 release (issue #358).
- File-write races in two best-effort stores (issue #352): stats rotation
  (`core/stats.py`, the read-modify-write in `_rotate_if_needed`) and the
  glossary "missing" queue (`glossary/json_provider.append_missing_entries`,
  a whole-file load→merge→save) had no locking, so the web layer's
  `ThreadPoolExecutor` could interleave concurrent writers and drop entries.
  Both critical sections are now serialized by a module-level `threading.Lock`.
  This covers the single-process, multi-thread (web) model; cross-process races
  (CLI and web at once) are explicitly out of scope and will be closed by the
  SQLite/WAL history layer (issue #344). Best-effort semantics are unchanged (an
  `OSError` still never breaks grading). Adds concurrent-writer regression tests
  for both stores.
- `--sandbox` was silently ignored when combined with `--serve` (issue #351):
  the `--serve` branch returns before `set_runner()`, so the web server always
  executed code with the plain `LocalRunner` even when the user explicitly asked
  for the sandbox — a false sense of isolation. `stepik-grader --serve
  --sandbox` now fails fast with an explicit, localized error (`ru`/`en`)
  instead of starting an unprotected server. Wiring `SandboxRunner` into the
  web layer remains a separate task (outside issue #266).
- Broken `TYPE_CHECKING` import in `core/reporter.py` (issue #350): the
  annotation-only import read `from core.grader_core import TestCase` — a path
  missing the `stepik_grader.` prefix. Under `TYPE_CHECKING` it never failed at
  runtime, and `mypy --ignore-missing-imports` stayed silent, so `TestCase` in
  every reporter annotation was effectively `Any` and went unchecked. Fixed to
  `from stepik_grader.core.grader_core import TestCase` (which re-exports it
  from `test_loader`); mypy still passes, confirming no hidden type errors were
  masked behind the `Any`.
- Small web UI inconsistencies from the audit (issue #331). Case-verdict
  badges now recognize `CANCELLED` (neutral) and `SANDBOX_VIOLATION` (error)
  from `docs/result-contract.md` instead of silently falling back to a
  label-less neutral badge. The mode-1/2 "Параметры" config tab now ships
  markup consistent with the default mode (tests → disabled, `aria-disabled`
  = `true`) so there's no flash of wrong state before JS initializes. (The
  third item — cyclic `switch_section` over all four nav sections instead of a
  two-way check↔glossary toggle — already landed with the sandbox in #317; a
  regression test is added here.)
- Documentation drift against the actual web UI (issue #330): the
  "two modes" phrasing in `grader-workflow.md`, the outdated topbar
  segment-control navigation diagram and the `command bar` mode-switcher
  wording in `web-current.md`, the self-contradictory J0 step describing an
  *automatic* post-download hand-off (it's a manual "Перейти к проверке"
  button), and stale references to a single `web.py` module (now the `web/`
  package) in `core/glossary.py` are all corrected to match the shipped
  four-section UI.
- Web UI accessibility for grading results (issue #298, WCAG 2.1 AA):
  results were announced silently to assistive tech. Added a polite
  `aria-live` region (`#result-announce`) that speaks a one-line outcome
  summary on completion ("task_1.py — OK, 12 из 12" / "Бенчмарк завершён: N
  решений" / error/cancel text) — not on every progress tick, to avoid
  noise. The progress bar now carries `role="progressbar"` +
  `aria-valuemin/valuemax/valuenow`, and focus moves to the results panel
  when a run finishes. Verdict badges already conveyed meaning as text (not
  colour alone) — now pinned by a regression test. The dark-theme
  `--color-warning` token was lightened from `#bb653b` to `#d98a5c` so the
  SLOWER verdict / warning text clears the 4.5:1 contrast minimum on dark
  surfaces (was ~3.5–4.4:1); light theme already passed and is unchanged.

### Changed
- The web glossary source (`glossary_adapter._all_cards`) is now memoized in
  memory, keyed by the source's mtime (issue #339). Previously every
  `/api/glossary`, `/api/glossary/<id>` and `/api/code-terms` request re-read
  and re-parsed the whole bundled base (~1.2 MB, ~1400 cards) — on the
  debounced "Функции в коде" panel that meant parsing 1.2 MB on every few
  keystrokes. Now it parses once and reuses the result (~185× faster on the
  cache hit locally: 40 ms → 0.2 ms); editing a configured `glossary_store`
  bumps its mtime and transparently invalidates the cache. Cards are read-only
  to consumers, so the shared list is safe across request threads. No new
  dependency, no SQLite.
- Mode 1 (single-file correctness) in the web UI no longer saves to disk
  before grading (issue #297). "Проверить" now runs one
  `POST /api/v1/runs` with `mode="tests"` and the editor's `code` in the
  request body — the solution executes from a temp file and the target file
  on disk is never touched, closing the save→grade race two windows on the
  same folder could hit. Saving is now a separate explicit "Сохранить"
  button (`POST /api/save-solution`), with an unsaved-changes indicator on
  the editor and minimal optimistic locking: `save_solution`/`read_source`
  return the file `mtime`, and saving over an existing file whose on-disk
  `mtime` drifted from the loaded baseline is refused with
  `{"ok": false, "conflict": true, message_id: file_changed_on_disk}` (a
  second save overwrites). `POST /api/v1/runs` accepts `mode="tests"` in
  addition to `bench`/`microbench`. `GET /api/grade` is unchanged and still
  serves mode 2 (folder grading).

### Added
- Opt-in diagnostic logging for the Stepik network/OAuth/download layer (epic
  #146, issues #147/#148/#149). A new stdlib-only `core/diag_log.py` provides a
  single logger (`get_logger`, `configure_diagnostics`, `register_secret`,
  `redact`) that is **silent by default** and enabled explicitly via the
  `--diagnostic` CLI flag, `STEPIK_GRADER_LOG=debug|info|off`, or
  `python -m stepik_grader.diagnostic_stepik`. When on, it writes a
  human-readable `stepik_diagnostics/grader.log` with a **mandatory redaction
  filter**: `Bearer` tokens, `access_token`/`refresh_token`/`client_secret`/
  `code` in URLs, headers and JSON bodies, plus any runtime-registered secret
  value, are replaced with `***redacted***` before anything is written
  (docs/logging.md, SECURITY.md). `downloader` (URL parse + which of the 4
  test-case sources matched), `stepik_client` (HTTP GET requests with sanitized
  URLs, token refresh, code exchange) and `oauth_flow` (auth branch decisions)
  are instrumented; the normal `_console` output is unchanged. No new
  dependency (stdlib `logging`). New `tests/test_diag_log.py` covers redaction,
  opt-in, levels and env activation.
- "Функции в коде" glossary integration for check modes 1/2 (epic #315,
  issues #322/#323/#324). The dead "Связанные термины" placeholder in the
  mode-1/2 config panel is replaced by a real mini-card list of the functions
  used in the current solution — mode 1 updates from the editor (debounced),
  mode 2 from the selected solution file; clicking a card opens its full
  glossary card. The panel is hidden in benchmark modes 3/4 (#324). Backed by
  an extended `POST /api/code-terms` (#322): it now also accepts `{path}`
  (confined, read from the workspace), recognizes everyday builtins and
  builtin-type method calls (`s.split()` → `str.split`, `confidence="low"`
  since the receiver type isn't known statically), and returns **all**
  recognized concepts with a `has_card` flag (uncovered ones render dimmed).
  On a `{path}` request, notable uncovered functions are appended to the
  "Недостающее" queue (practice-driven AST channel). New `scan_code_concepts`
  wider set + method heuristic in `glossary/detector.py`; unit + HTTP + e2e
  tests. The narrow `DEFAULT_NOTABLE_BUILTINS` (missing-queue detector) is left
  untouched, so grading-side detection keeps ignoring `print`/`len`.
- Glossary integration in the sandbox (issue #321, epic #314). A "Функции в
  коде" panel lists mini-cards for the functions/constructs detected in the
  editor (debounced on edit) — clicking one opens the full card in the
  Glossary section. New `POST /api/code-terms` endpoint (`{code}` →
  `{terms: [...]}`) backs it: `glossary_adapter.code_terms` over a new public
  `glossary.detector.scan_code_concepts` (notable builtins, imported
  functions, `match/case`), matched to bundled cards by id/alias (and the tail
  after a dot, so `math.sqrt` → the `sqrt` card); everyday builtins like
  `print`/`len` are intentionally not surfaced as noise. On a runtime error
  the sandbox output now shows an error card with the exception type and a
  working deep-link to its glossary card. New unit tests (`code_terms` +
  endpoint) and a Playwright e2e journey (mini-card open + error-card
  deep-link).
- Variable-relationship diagram in the sandbox step player (issue #320, epic
  #314). A "Таблица / Диаграмма" toggle in the player's variable panel switches
  to a Python-Tutor-style memory graph: stack frames on the left, heap object
  nodes (list/tuple/set/dict/class instances) on the right, with SVG arrows
  from each reference variable to its object. Aliasing is visible as two arrows
  into one node (nodes are keyed by heap id), nesting as node→node arrows;
  primitives stay inline in the frame. Changed variables are highlighted, the
  diagram re-renders on each step, and it degrades to the table with a note
  past a node cap. Pure vanilla-JS SVG (no external libs, per the vendored-only
  policy), boxes measured in the DOM to route the arrows. Frontend-only
  (`web/static/`) over the #318 trace; new Playwright e2e journey (aliasing +
  nesting).
- Step-by-step trace player in the sandbox (issue #319, epic #314). A
  "Пошагово" button traces the code (`mode="trace"`, #318) and opens a
  Python-Tutor-style player in the output panel: ⏮ ◀ ▶ ⏭ controls + a step
  slider + ← → keyboard navigation ("шаг N из M", announced via an
  `aria-live` region). Each step highlights the active line in a read-only
  code snapshot (the vendored CodeMirror bundle exports no `Decoration`, and a
  frozen snapshot is sturdier than decorating the live editor), lists stack
  frames (globals + each frame's locals, changed variables highlighted, values
  rendered from the heap refs), shows the program output grown to that step
  (sliced by `stdout_len`), and on an exception step paints the culprit line
  red with a deep-link to the matching glossary card. Truncated traces show a
  "показаны первые N шагов" note. Frontend-only (`web/static/`) over the #318
  API; new Playwright e2e journeys (loop stepping + keyboard nav, function
  frame appearance); the e2e `browser` fixture now honors
  `PLAYWRIGHT_EXECUTABLE_PATH`.
- Step-by-step execution tracer for the sandbox (issue #318, epic #314).
  `core/tracer.py` runs code in a subprocess under `sys.settrace` and collects
  a Python-Tutor-style JSON trace — one snapshot per line/call/return/exception
  step, each carrying the stack frames (with locals) and a heap of objects
  referenced by id so the frontend can show aliasing (two names → one object)
  and nested structures. Values are safe-encoded (string/container/depth caps;
  non-finite floats and huge ints degraded to keep the JSON valid). Wired as a
  new async job `mode="trace"` on `POST /api/v1/runs` (`{code, stdin}`, no
  `path`); execution is bounded by `max_steps` (1000) + timeout. Format is
  documented in [`docs/trace-format.md`](docs/trace-format.md). The step-player
  UI consuming it lands in #319. No OS sandbox (CLAUDE.md invariant 4).
- Sandbox / playground section in the web UI (issue #317, epic #314): a
  fourth nav section that runs arbitrary code with arbitrary stdin and shows
  the program's output — not grading against tests. A separate CodeMirror
  editor plus a stdin field feed `web/playground.run_playground`, which
  executes via the same `core.runner.LocalRunner` subprocess path (shared
  wall-clock timeout, best-effort memory cap, cooperative cancel) and returns
  `{status: OK|RE|TLE|CANCELLED, stdout, stderr, exit_code, duration_ms,
  truncated}`. Runs go through the async job model (`POST /api/v1/runs` with
  the new `mode="playground"`, `{code, stdin}`, no `path`) so a runaway
  `while True: pass` is cancelable and the UI stays responsive; output is
  clipped to 100k chars. The command-palette "switch section" now cycles all
  four sections (was check↔glossary only). Step-through execution and variable
  visualization land in follow-ups (#318/#319/#320). No OS sandbox (CLAUDE.md
  invariant 4).
- Draft cards auto-generated from the official Python docs (issue #328). A new
  offline generator (`scripts/generate_draft_cards.py`) introspects every
  inventory entity still missing a card and emits a `status="draft"`
  `GlossaryCard` from the live stdlib: signature via `inspect.signature` (or
  the docstring's first line), body from `inspect.getdoc`, a templated
  `docs.python.org` link, and a section mirroring the imported base so the
  drafts fall under the same section chips. Drafts ship as
  `glossary/data/drafts.json` (832 cards) — this brings the bundled base to
  ~100% coverage of the offline stdlib inventory. In the web glossary drafts
  are muted in the list, badged "черновик" in the detail, and filterable via a
  new status select (Все / Готовые / Черновики); id = full qualname so a
  method draft (`str.split`) also closes its coverage gap. The generator is
  idempotent and never overwrites existing (hand-edited) cards.
- Built-in type methods in the stdlib coverage inventory (issue #327).
  `build_stdlib_inventory` now also enumerates the public callable methods of
  the notable built-in types (`NOTABLE_BUILTIN_TYPES`: str/list/dict/set/
  tuple/bytes/int/float/…) as `kind="method"` items with `str.split`-style
  qualnames — the beginner-facing layer the builtins scan missed (it only saw
  the classes). Coverage gains a `methods` category; a method counts as
  covered only on a full-qualname match (`str.split`), never the bare method
  name, so one `split` card can't falsely cover every type's method. In the
  missing queue a method maps to `kind="function"` (MissingKind is unchanged;
  the full context lives in `module`/`qualname`).
- Glossary section filters, sort and deep-linking (issue #329). The web
  "Глоссарий" section gains a filter toolbar: quick section chips —
  **Строки / Списки / Кортежи / Словари / Множества** kept as *separate*
  chips (never merged, unlike the upstream Glossary-Python), a section
  dropdown, a `kind` filter and a sort (A–Я / by section / by version) — plus
  a "Показано N из M" counter. Facets combine server-side: `GET /api/glossary`
  now accepts `section`, `kind`, `status` and `sort` alongside `q`. Cards are
  reachable by deep-link `#/glossary/<id>` (shareable direct links; the same
  route backs error-card jumps). `glossary_search` gained the matching
  keyword-only params.
- Curated WA hint for non-UTF-8 output (issue #301): a solution that writes
  raw bytes to stdout (`sys.stdout.buffer.write(b"\xff...")`) is decoded with
  `errors="replace"`, so its diff shows `�` (U+FFFD) with no explanation.
  `web/viewmodels._wa_suggestion` now detects `�` in the actual output and
  returns a `message_id="output_invalid_utf8"` hint (ru/en) pointing at the
  likely cause (printing raw bytes / wrong encoding), taking priority over
  the trailing-whitespace hint. The runner's decode strategy is unchanged
  (still `errors="replace"`, a deliberate non-goal).
- Bundled glossary base (issue #326): 581 cards imported from Glossary-Python
  now ship in the wheel at `stepik_grader/glossary/data/*.json` (one file per
  colour-group). The web "Глоссарий" section serves them as the zero-config
  default when `CONFIG.glossary_store` is unset, turning the section from a
  ~28-exception fallback into a full reference; the compact `core/glossary.py`
  fallback remains for when the bundled dir is absent/broken. A reproducible,
  offline importer (`scripts/import_glossary_python.py`) does the one-time
  conversion (`name→title`, `docs→docs_url`, `version` null→`""`, exception
  ids lowercased to match the anchor convention). `stdlib` coverage
  (`python -m stepik_grader.glossary.coverage --cards …`) rises from 0 to
  ~190+ covered (builtins 94%).
- `GlossaryCard` gains four optional fields (issue #325): `syntax`
  (signature/usage template), `docs_url` (link to official docs.python.org;
  `docs` accepted as an alias, mirroring `hint`→`summary`), `version`
  (minimum Python version, e.g. `3.10`; JSON `null` normalises to `""`), and
  `subcat` (subcategory within `section`, for the glossary section's
  filters). All are backward-compatible — existing JSON bases without them
  still load — and the web glossary card now renders syntax, examples, a
  Python-version badge and a docs.python.org link. Foundation for the
  glossary content epic (import from Glossary-Python #326, redesign #329).
- `POST /api/v1/runs` job status gets a fifth, additive value: `"cancelled"`
  (issue #296), alongside `queued`/`running`/`done`/`error`. Previously a
  user-cancelled job reported `status="error"` with
  `message_id="run_cancelled"` — semantically a cancellation is not a
  failure of the solution or the grader, and future clients (server mode,
  an IDE extension) would otherwise have to parse `message_id` just to tell
  "user changed their mind" apart from "grader crashed" (e.g. to decide
  whether a retry makes sense — it does for `error`, never for
  `cancelled`). `message_id="run_cancelled"` is still set on the terminal
  status either way. The web UI now renders a cancelled run with a neutral
  tone (`.msg-neutral`) instead of the error-red `.msg`. Landed before the
  `/api/v1/*` contract freeze (issue #156) while the change is still cheap.

### Fixed
- Empty/missing `tests/` no longer reported as `FAIL 0/0` (issue #299): both
  the web `grade_path()` row status and the CLI correctness table
  (`core/reporter._correctness_status`) now return `"NO TESTS"` when
  `total == 0` — matching the contract already documented in
  `docs/result-contract.md`, which the code had drifted from. Previously a
  solution folder with an existing-but-empty `tests/` dir looked identical to
  a genuinely wrong solution.
- `core/runner._measure_peak_memory`'s "peak memory measurement unreliable"
  `UserWarning` no longer floods the console during batch grading (mode 2)
  or a long-running `--serve`: the message used to interpolate the child
  `pid`, so every occurrence was a distinct string that defeated Python's
  own "default" warning filter dedup (which keys on the exact rendered
  text) — one warning per trivially-fast solution (`print(1)` and similar,
  common Stepik exercises) instead of once per process. Message text is now
  constant; the stdlib filter shows it once per interpreter session.

### Docs
- New `docs/audit-2026-07.md`: one-off deep multi-role project audit
  (architecture, code quality, tests/CI, product, UX, docs drift) snapshotted
  at v1.7.0+49 with all quality gates re-run (1308 passed, ruff/mypy clean).
  Includes the design proposal for the upcoming "Правила/PEP" and "Подучить"
  (frequent mistakes) sections: opt-in SQLite history (epic #130), ruff-based
  lint integration behind a `[lint]` extra, run-count-based card decay.
  Registered in the `docs/README.md` index; follow-up epics/issues are opened
  from the audit's findings.
- `docs/README.md` navigation index now lists `docs/changelog-archive.md`
  (issue #300) — it existed in `docs/` since the CHANGELOG split but was
  never added to the index.
- New CI guardrail (`scripts/check_docs_guardrails.py`): every `docs/*.md`
  file must be referenced from `docs/README.md`, or the check fails — makes
  the class of drift behind issue #300 impossible to reintroduce silently.
  `docs/adr/*.md` is exempt (cataloged by its own `docs/adr/README.md` index).
- `core/sandbox/__init__.py`, `core/sandbox/_linux.py`, `core/runner.py`
  docstrings no longer describe `nsjail` as an implemented Linux fallback
  backend (issue #293): `bwrap` is the only Linux `--sandbox` backend in this
  MVP, matching what `SECURITY.md`/`docs/server-mode.md` already documented
  correctly — only the code docstrings had drifted.

### CI
- Per-OS coverage margin (issue #294): each CI matrix OS job's own
  `--cov-fail-under=85` gate used to count the OTHER two platforms'
  `core/sandbox/` backend files as permanently uncovered (structurally
  unreachable on that OS), leaving as little as ~1.1pp margin on ubuntu. New
  `scripts/generate_ci_coveragerc.py` generates a CI-only `.coveragerc.ci`
  that additionally omits, for each job, only the backend files unreachable
  on its own OS; the `coverage-combine` cross-OS aggregate job (
  `--fail-under=90`) is untouched and still sees every file from whichever
  job(s) can actually exercise it — no file is omitted everywhere at once.
  `fail_under = 85` itself is unchanged; local `pytest` runs are unaffected
  (this mechanism is CI-only).

### Docs
- `docs/installation.md`: new troubleshooting note for `stepik-grader ...`
  failing with `ModuleNotFoundError: No module named 'stepik_grader'` even
  though the command itself resolves — root cause is a stale global editable
  install (commonly predating the project's src-layout migration, issue #35)
  shadowing the working `.venv` install. Covers diagnosis
  (`Get-Command`/`which stepik-grader`) and cleanup (`pip uninstall`, plus
  manual removal of orphaned `.dist-info`/`.pth`/`_finder.py` files when pip
  can't find a RECORD to uninstall from).

### Refactored
- CodeMirror 6 frontend vendoring (issue #295): the 8 separate esm.sh
  per-package bundles + import map + 4 Node.js browser-compat polyfill files
  (issue #265) are replaced by a single self-contained esbuild bundle,
  `static/vendor/codemirror-bundle@6.mjs`. `app.js` now imports it directly
  by URL instead of via bare specifiers resolved through an import map.
  Building from the real npm packages (not esm.sh's per-package re-bundles)
  lets tree-shaking eliminate the optional debug/tracing code path that
  needed the Node shims in the first place — none are needed anymore
  (verified: no `events`/`tty`/`process`/`async_hooks` references in the
  output). ~12 HTTP requests for the editor down to 1; bundle is smaller than
  the sum of the files it replaces. No build tooling added to the repo or CI
  — the bundle is built once outside the repo and committed as a finished
  artifact, same philosophy as before (see `static/vendor/VERSIONS.md` for
  the reproducible build recipe and full pinned dependency list, now
  including previously-undocumented transitive deps `@codemirror/autocomplete`,
  `@lezer/python`, `style-mod`, `w3c-keyname`, `crelt`).

## [1.7.0] - 2026-07-12

### Added
- Opt-in OS-level sandboxed execution (issue #266): new `--sandbox` flag
  routes `--mode 1/2/3/4` through a new `SandboxRunner` (`core/sandbox/`)
  instead of the plain-subprocess `LocalRunner` — bubblewrap (`bwrap`) on
  Linux, `sandbox-exec` (Seatbelt) on macOS, Job Objects (ctypes, no
  `pywin32`) on Windows. Backend is selected once at CLI startup by OS; if
  unavailable (missing `bwrap`/`sandbox-exec`, or the Job Object API check
  fails), the command exits with a clear error — never a silent fallback to
  `LocalRunner`. Guarantees deliberately differ by OS (documented in
  `SECURITY.md`, not a bug): Linux gets full kernel-enforced isolation
  (network/fs/memory/CPU/process-count via namespaces + `RLIMIT_*`); macOS
  isolates network/fs/CPU via Seatbelt but approximates memory via psutil
  polling (`RLIMIT_AS` doesn't work on Darwin, bpo-34602) with a weaker
  process-count budget (no user-namespace equivalent); Windows gets
  kernel-enforced memory/CPU/process-count via Job Objects (memory limit is
  commit-charge-based and in practice faster than POSIX `RLIMIT_AS`) but has
  **no network isolation** and only soft (`cwd`-relative) filesystem
  containment in this MVP — both named, not silent, gaps (AppContainer and
  `CreateProcessAsUser`+restricted-token respectively were judged
  disproportionately complex/risky for a first cut). New additive verdict
  `SANDBOX_VIOLATION` (`RunOutcome.sandbox_violation`, additive to
  AC/WA/RE/TLE/CANCELLED) fires only for violations the runner proactively
  detects and kills itself — memory (RSS/commit threshold), `output_size`
  (stdout+stderr over `sandbox_max_output_bytes`), `cpu` (`SIGXCPU` on
  POSIX); network/filesystem/process-count violations are rejected by the
  kernel *inside* the sandbox and correctly surface as an ordinary `RE`
  instead (the runner doesn't parse a child's traceback to relabel it). New
  `grader_core.set_runner()` fulfills the injection point the codebase had
  already reserved for this. Three new `[tool.stepik-grader]` quota fields:
  `sandbox_max_cpu_seconds` (10.0), `sandbox_max_processes` (32),
  `sandbox_max_output_bytes` (10 MiB). Known MVP limitation on all three
  platforms: only the interpreter + stdlib are bound into the sandbox, not
  the grader's own venv site-packages, so solutions depending on third-party
  packages aren't supported under `--sandbox`; Linux's nsjail fallback
  (mentioned in the original design) also isn't implemented, `bwrap` is the
  only Linux backend. New `tests/test_sandbox_runner.py`: platform-
  independent unit tests plus `pytest.mark.skipif`-gated real-backend
  escape-matrix tests (write outside tmp, network, fork bomb, memory/output
  overruns, TLE) and a golden AC/RE/TLE comparison against `LocalRunner`,
  each executing for real only on its native OS.
- Opt-in local run statistics (issue #268): `--stats`/`--no-stats` (or
  `[tool.stepik-grader] record_stats = true`) appends one JSON-Lines record
  per grading run — mode, verdict tallies (AC/WA/RE/TLE for modes 1/2,
  SIMILAR/SLOWER/MUCH_SLOWER/ERR for modes 3/4), OS, and total time — to a
  new `.grader_stats.jsonl` in the current directory (added to `.gitignore`
  and CLAUDE.md's forbidden-commit list). No network calls anywhere — the
  file never leaves the machine, and off by default. New `--stats-summary`
  prints an aggregated view (total runs, by mode, by OS, by verdict, total
  time) via a new `core/reporter.print_stats_summary()`, rich table with a
  plain-text fallback like the existing correctness/benchmark tables. New
  leaf module `core/stats.py`: JSON Lines (not a single JSON object like
  `GraderCache`, issue #56) so an interrupted write can only lose the last
  line, not corrupt the whole file; size-based rotation keeps the newest
  half of lines past 1 MiB; both `record_run()`/`read_summary()` are
  best-effort and tolerate a missing/corrupt file or individual malformed
  lines, same principle as `GraderCache`. The interactive menu resolves
  `CONFIG.record_stats` directly (no argparse there) at all 4 mode choices,
  unlike the cache toggle which the menu never exposes today.
- `docs/configuration.md` documents the new `record_stats` config field
  with an explicit privacy paragraph ("data never leaves the machine").

### Changed
- **Breaking (issue #73):** public API functions/methods that take or return
  a filesystem path now use `pathlib.Path` instead of `str`, across
  `core/test_loader.py` (`resolve_test_dir`, `find_all_solution_files`,
  `collect_grouped_files`, `load_text_lines`, `load_test_cases`),
  `core/grader_core.py` (`run_single_test`, `run_tests`, `run_benchmark`,
  `run_microbench_mode`), `core/reporter.py` (table formatters),
  `core/cache.py` (`GraderCache`, `hash_solution`, `hash_tests`),
  `core/runner.py` (`RunSpec.path`), the CLI (`--file`/`--dir`/`--root` now
  parse to `Path` via argparse), and the `web/` adapter layer
  (`grade_path`/`grade_benchmark`/`grade_microbench`/`list_solutions`/
  `read_source`/`save_solution`/`submit_job`/glossary store paths). External
  code calling these with a bare `str` must now pass a `Path` (or wrap with
  `pathlib.Path(...)`) — the functions no longer defensively re-wrap `str`
  input. JSON-facing response fields (e.g. `"base"`, `"path"`, `"file"`) are
  unaffected — those remain plain strings. `grader.py`'s `__all__` also gains
  `resolve_test_dir`, closing a pre-existing facade gap (it was reachable as
  `grader.resolve_test_dir` via the wildcard re-export but wasn't listed).

### Docs
- Pre-release accuracy audit ahead of v1.7.0: `docs/project-structure.md`
  and `docs/architecture.md` now mention `core/sandbox/` (issue #266),
  `web/runs.py`/`web/i18n.py` (issue #262/#264), `core/stats.py` (issue
  #268), and `core/i18n.py`, plus the DAG/layer diagrams for all of them —
  `architecture.md` previously still called `SandboxRunner` "future work
  (issue #157)" after it had already shipped. `docs/configuration.md`
  gained the missing `glossary_store`/`glossary_missing_queue` rows.
  `docs/grader-workflow.md` gained a `--stats`/`--stats-summary` section
  (previously undocumented outside `configuration.md`).
  `docs/installation.md`'s pinned `ruff>=0.4` corrected to `>=0.15.19`
  (matching `pyproject.toml`). `docs/server-mode.md`'s unconditional "network
  unreachable" `SandboxRunner` guarantee now flags the Windows exception
  inline, not just in a separate paragraph below it. `SECURITY.md` gained a
  dedicated section naming the Host/Origin guard and path-confinement
  (`--root`/`--no-root-confinement`) mechanisms explicitly, cross-linked to
  `docs/api.md`. `CLAUDE.md`'s metrics table test count corrected
  (967 → 784, matching `CHECKPOINT.md`/`docs/versions.md` for the same
  v1.6.0 snapshot) and Python 3.14 now marked experimental/ubuntu-only
  there and in the README badge, matching `docs/grader-workflow.md`'s
  existing wording.
- New `docs/api.md` (issue #267): canonical HTTP API reference for
  `--serve` — every endpoint's method/path, params, limits, response codes,
  and a curl example, sourced from a full audit of `web/server.py`.
  `_Handler`'s docstring is trimmed to a short pointer instead of
  duplicating the endpoint list.
- `docs/web-mvp.md` split into `docs/web-current.md` (what's actually
  implemented) and `docs/web-design.md` (design-only/deferred/rejected
  ideas, including the `## MVP vs v1 vs later` status tracker) — the old
  file mixed both, making it hard for a new contributor to tell what's
  real without reading the code. All ~15 files with markdown links to the
  old file repointed to whichever new file actually covers that section.
- `CHANGELOG.md`'s 10 pre-versioning "pseudo-Unreleased" snapshots (dated
  June 2026, format `## [unreleased] / <date>`, predating git-tag-based
  versioning — issue #162/#183) moved verbatim to new
  `docs/changelog-archive.md`, cross-linked with `docs/history.md` (same
  period, different granularity/language). Live `CHANGELOG.md` now holds
  only the current `[Unreleased]` + real releases `[1.1.0]`...`[1.6.0]`.
- New troubleshooting section in `docs/installation.md` (issue #270):
  `test_pytest_plugin.py` failing with `unrecognized arguments:
  --grader-mode`, and `test_packaging.py::test_license_is_mit_in_metadata`
  failing with `License-Expression: None`, share the same root cause — a
  stale editable install whose `.dist-info/entry_points.txt` predates
  `pyproject.toml` changes to `license`/`entry-points` — fixed by
  `pip install -e ".[dev]" --force-reinstall --no-deps`. Also documents the
  unrelated `PermissionError` on a stale `%TEMP%\pytest-of-<user>` from a
  prior pytest run under different Windows permissions, with the
  `--basetemp` workaround. No code changes — all three findings from the
  2026-07-10 audit were confirmed non-reproducing once the install was
  refreshed (verified live: full suite green, `--grader-mode` plugin
  resolves correctly, with both default and deeply-nested custom
  `--basetemp` paths).
- `docs/versions.md`'s fork-vs-original comparison table condensed from
  ~24 single-feature rows down to 5 grouped-by-theme rows (correctness,
  benchmark/microbench, Stepik integration, web UI/IDE, engineering
  baseline) — the granular list had grown hard to scan and, worse, had
  drifted: it never mentioned the local web UI (`--serve`) or IDE
  integration at all. Now covers both, and names IDE integration
  correctly as VS Code (`--init-vscode`) **and** PyCharm (documented
  External Tool recipe, `docs/grader-workflow.md § Интеграция с IDE`) —
  a prior mention of only VS Code was an omission fixed here. Per-item
  detail is unchanged in `CHANGELOG.md`/`docs/history.md`, linked from
  the section for anyone who wants it. The `v1.4.0` row in the
  version-evolution table below got the same PyCharm correction.

### Fixed
- README badges (`Version`, `Coverage (ubuntu)`, `Coverage (all OS
  combined)`) now pass `&cacheSeconds=300` to shields.io's endpoint-badge
  API — the shortest TTL shields.io honors. Without it, GitHub's camo image
  proxy and shields.io's own edge cache could each hold a stale render for
  hours after the underlying `.github/badges/*.json` changed (as happened
  right after #289 landed), with no way for a reader to tell the badge was
  just out of date rather than the fix not having worked.
- CI (issue #289): the two coverage badges (`coverage.json`, single-OS
  ubuntu view; `coverage-combined.json`, cross-OS combined, issue #283)
  rendered with an identical `"coverage"` label baked into the badge image
  itself — shields.io draws `label` on the picture, not just in markdown
  alt-text, so both badges looked the same except for the percentage.
  `generate_coverage_badge.py` gained a `--label` flag (default `"coverage"`
  for backward compat); CI now passes `"coverage (ubuntu)"` and
  `"coverage (all OS)"` respectively.
- CI (issue #286): both badge-update steps (`test` job and `coverage-combine`
  job, issue #283) used plain `git diff --quiet -- .github/badges/` to decide
  whether to commit — which only looks at already-tracked files. This never
  once committed `coverage-combined.json` (a brand-new file as of #283): the
  script correctly computed the percentage every run, but the untracked file
  never showed up as a "change", so the commit step always took the "Badges
  unchanged" branch. Left the README's second coverage badge pointing at a
  file that was never actually in the repo (404). Fixed by `git add` before
  the check and diffing `--cached` instead, in both steps.

### Internal
- CI: the `Update badges (main only)` step now retries (up to 3 attempts)
  instead of failing the job outright when two pushes to `main` land close
  together. Two workflow runs racing to commit+push `.github/badges/*.json`
  is harmless in itself (the loser's `git pull --rebase` conflict is caught
  *before* `push`, so `main` never actually gets corrupted), but it did leave
  a spurious red CI run. On conflict, the step now aborts the rebase, resets
  to fresh `origin/main`, and regenerates the badges from that HEAD — which
  typically now matches what the other run already pushed, so the retry
  cleanly resolves as "Badges unchanged." A final failure after 3 attempts
  is a `::warning::`, not a job failure — a later push will catch the
  badges up regardless.
- CI: cross-OS combined coverage (issue #283). Since issue #266
  (`SandboxRunner`), `core/sandbox/_linux.py`/`_macos.py`/`_windows.py` are
  OS-specific backends — any single CI job/local machine only ever exercises
  one of the three, permanently reading the other two as 0% and capping
  single-job coverage at ~86-90% regardless of test quality (this is what
  dropped the badge from ~95% to 86.1% right after #266/#281 merged, not a
  real regression). New `coverage-combine` job merges the three OS matrix
  jobs' `.coverage` data (`coverage combine`, with `[tool.coverage.paths]`
  aliasing in `pyproject.toml` to reconcile each OS's different absolute
  checkout path) into one report gated at `--fail-under=90`, separate from
  the existing per-OS `fail_under = 85` in `pyproject.toml` (left unchanged
  — raising it globally would make every contributor's single-OS local
  `pytest` run fail on the two backends their machine can never see).
  README now shows both numbers as two distinct badges — single-OS
  (`coverage.json`, as before) and cross-OS combined (new
  `coverage-combined.json`) — rather than collapsing to one figure that
  would either overstate or understate reality.

### Refactored
- Web API `message` strings are now rendered server-side from a locale
  catalog instead of being Russian literals baked into `web/viewmodels.py`/
  `web/server.py` (issue #264). Every error/status response that carries a
  human-readable `message` gained two sibling fields: `message_id` (the
  catalog key, e.g. `"path_not_found"`) and `message_params` (the dict of
  values interpolated into it — empty if none). `message` itself is
  unchanged for existing callers: default locale is still `ru`, rendered
  byte-for-byte identical to the old hardcoded text. New `web/i18n.py`
  (`render_message()`/`message_fields()`/`resolve_lang()`) is a thin
  web-layer renderer built on top of `core/i18n.load_locale_messages()`
  (issue #144) — `core/i18n.py` itself stays a stdlib-only leaf, per
  CLAUDE.md's architectural invariant; the catalog and `message_params`
  interpolation are an application-layer concern, not core infra.
  `core/locales/ru.json`/`en.json` (previously empty placeholders from
  issue #144) are now populated with the actual web-layer message strings
  and their English translations. Locale is selected via a new `?lang=`
  query parameter on `/api/*` GET/POST endpoints (`ru`/`en`; anything else,
  or the param's absence, falls back to `ru` — no UX change for existing
  callers). New CI-wired guardrail `scripts/check_locale_guardrails.py`
  (modeled on `scripts/check_docs_guardrails.py`) checks that every
  `message_id` referenced in `web/*.py` exists in `ru.json`, and that
  `ru.json`/`en.json` have exactly the same key set. New
  `tests/test_i18n_guardrails.py` AST-parses `web/viewmodels.py`/
  `web/server.py` and fails on any string literal containing Cyrillic
  characters outside docstrings — the regression guard for "no hardcoded
  Russian message text left in the web layer." `docs/result-contract.md`'s
  Run result field table documents `message_id`/`message_params`.

### Added
- Mode 1's code editor (`--serve`) is now CodeMirror 6 instead of a plain
  `<textarea>` (issue #265): Python syntax highlighting, line numbers, and
  Tab-to-indent, themed via the existing `app.css` design tokens (follows
  light/dark automatically — no separate CodeMirror theme object per mode,
  just `var(--color-*)` references in one `EditorView.theme()`). Vendored,
  not CDN-loaded (same "everything offline" rule as issue #260's fonts):
  8 CodeMirror/Lezer sub-packages (`@codemirror/state`/`view`/`language`/
  `commands`/`lang-python`, `@lezer/common`/`highlight`/`lr`) plus 4 tiny
  Node browser-compat shims `@lezer/lr` needs for an unused debug path,
  each fetched pre-built from esm.sh with every *other* package in the set
  marked `external` so they all share one copy of `@codemirror/state`/
  `view`/`language` — CodeMirror's extension system works by object
  identity, so duplicate copies would have silently broken cross-package
  extensions. New `static/vendor/` (`LICENSE`, `VERSIONS.md` with the exact
  fetch recipe and a note on a self-exclusion bug hit once during
  development), wired into `index.html` via a `<script type="importmap">`
  and `web/server.py`'s static routes; `app.js` is now `type="module"`
  (no inline scripts/`on*=` handlers depended on it staying classic).
  `pyproject.toml`'s `package-data` gained `web/static/vendor/*`. The old
  `$("#solution-editor").value` read/write call sites became a small
  `getEditorCode()`/`setEditorCode()` API backed by CodeMirror's document
  state; focus visibility (accessibility) uses `#solution-editor:focus-
  within` since the actual focusable node is CodeMirror's own nested
  `.cm-content`, not the outer container `:focus` never fires on directly.
  `tests/e2e/test_journeys.py`'s mode-1 edit/save/run journey (issue #263)
  updated to type into `.cm-content` via real keyboard events instead of
  `.fill()`/`.input_value()` on a textarea — still green.
- Async job model for bench/microbench in `--serve` (issue #262): new
  `POST /api/v1/runs` (body `{"path"|"code","mode","params"}`) queues a job
  and returns `202 {"run_id","status":"queued"}` immediately instead of
  blocking the request for the whole benchmark; `GET /api/v1/runs/{id}`
  polls `{"status":"queued"|"running"|"done"|"error","progress":
  {"done","total"},"result"}`; `POST /api/v1/runs/{id}/cancel` is a
  best-effort cancel that actually terminates the running child process
  (not just flips a status flag). New `web/runs.py` — in-memory job
  registry (`threading.Lock`-guarded dict) + `ThreadPoolExecutor` (size
  configurable via new `GraderConfig.job_workers`, default 2), lazy
  TTL-based cleanup of finished jobs (15 min) on each registry access, no
  extra background thread. `core/runner.py`'s `LocalRunner.run()` gained an
  additive `RunSpec.cancel_event: threading.Event | None` — `None` (CLI,
  sync `/api/grade`) keeps the exact prior single blocking
  `proc.communicate()` call; when set, a 100ms poll loop with concurrent
  stdout/stderr drain threads checks it and kills the child early
  (`RunOutcome.cancelled`). `core/grader_core.py`'s `run_tests`/
  `run_benchmark`/`run_microbench_mode`/`run_single_test` gained matching
  optional `progress_callback`/`cancel_event` kwargs (both default `None`,
  CLI behavior unchanged) and a new additive case verdict `CANCELLED`
  (distinct from `TLE` — a cancelled run is not "your solution timed
  out"). `web/viewmodels.py`'s `grade_benchmark`/`grade_microbench` forward
  both through their per-solution loop, plus a new `estimate_run_count()`
  helper that cheaply pre-computes a job's total step count (file I/O only,
  no subprocess) for the progress bar's denominator. `POST /api/v1/runs`
  also accepts an optional `code` field (writes to a temp `.py` file next
  to `path`, graded instead of what's on disk — the same "editable code
  window without saving" scenario mode 1's `/api/save-solution` already
  supports, just for bench/microbench). Frontend (`static/app.js`): modes
  3/4 now POST + poll (600ms) with a new progress bar (`#bar`) and Cancel
  button (`#cancel-run`) instead of a single blocking fetch; modes 1/2
  (plain tests) are unaffected, still on sync `/api/grade`. `/api/grade`
  itself is unchanged and documented as deprecated (not removed) for
  bench/microbench in `server.py`'s docstrings — see
  `docs/server-mode.md § Контракт API удалённого исполнения` for how this
  local MVP intentionally deviates from that section's speculative future
  network-API contract (inlined `result`, no `failed` status). New tests:
  `tests/test_runs.py` (job-lifecycle, no HTTP), `tests/test_web.py`'s
  `TestRunsApi*` (golden comparison against sync `/api/grade`, real-process
  cancellation via a PID-file + `psutil.pid_exists()` check, two concurrent
  jobs not mixing results, path confinement/input validation/Host-guard
  reuse), plus new `cancel_event` scenarios in `tests/test_runner.py`/
  `tests/test_grader_mock.py`.
- Playwright e2e smoke suite for the web UI, `tests/e2e/` (issue #263): 4
  user journeys against a real `--serve` instance (mode 2 folder grading +
  detail tab, mode 1 file picker with an editable code window + save + run,
  glossary search + card, command palette open/execute) plus an XSS
  regression test asserting `app.js`'s `esc()` escaping (hardened in issue
  #214) neither executes an injected `<img onerror=...>` payload nor renders
  it as a live element anywhere across its ~41 `innerHTML` call sites. New
  opt-in dev-extra `[project.optional-dependencies].e2e` (`playwright>=1.40`)
  in `pyproject.toml` — **not** a runtime dependency, only installed via
  `pip install -e ".[e2e]"` + `playwright install chromium`; the issue itself
  explicitly authorizes this dev-only addition. `tests/e2e/` is excluded from
  the default `pytest`/`pytest tests/` sweep via a new `norecursedirs`
  pytest.ini_options entry (explicit `pytest tests/e2e/` still collects it).
  New separate `e2e` CI job (Linux-only, `.github/workflows/ci.yml`) with
  Playwright browser caching, deliberately not folded into the main `test`
  matrix — issue #263 explicitly authorizes touching the workflow for this.
  README/CONTRIBUTING.md document how to run the suite locally.
- `--serve` gained workspace root confinement (issue #261): all request
  paths (`/api/grade`, `/api/source`, `/api/solutions`, `/api/save-solution`
  — both `folder` and an optional target `path`) are now resolved and
  checked against a server workspace (new `_GraderServer` — a
  `ThreadingHTTPServer` subclass carrying `workspace`/`confine`, and
  `_resolve_within_root()`/`_Handler._confined_path()` in `server.py`) —
  `Path.resolve()` runs before the containment check, so `../` traversal
  and symlinks pointing outside the workspace are caught, not just literal
  absolute paths. A request outside the workspace gets `403`
  (`{"kind": "error", "message": ...}`) instead of silently reading/writing
  anywhere on disk (previously confirmed live: `/api/source?path=/etc/
  hostname` read arbitrary files). New CLI flags: `--root <dir>` sets the
  workspace (default: cwd at `--serve` launch, also used for
  `__DEFAULT_PATH__` in `index.html`, replacing the old raw `os.getcwd()`);
  `--no-root-confinement` is an explicit opt-out back to the old
  unconfined behavior, reflected in the server's startup message.
  `/api/download`'s `root` (where to download a task *to*) is a separate
  concern and isn't confined by this change.

### Changed
- Web UI fonts (JetBrains Mono/Inter) are now vendored locally instead of
  loaded from the Google Fonts CDN (`fonts.googleapis.com`/
  `fonts.gstatic.com`), issue #260: `static/index.html`'s CDN `<link>`s are
  gone, `app.css` declares local `@font-face` rules (latin + cyrillic
  subsets, one variable woff2 file per subset covering the full weight
  range each family needs — Google itself serves the same file for every
  requested static weight of these two families) pointing at new
  `static/fonts/*.woff2`, served via a new `_STATIC_BINARY_ROUTES` map in
  `server.py` (`Content-Type: font/woff2`). Fixes the contradiction with
  the module's own "no external dependencies" docstring claim, restores a
  working offline UI (previously degraded to fallback fonts with no
  network), and stops leaking the fact that the tool is running to a
  third-party host on every page load. Fonts are OFL 1.1 (`static/fonts/
  LICENSE`); `pyproject.toml` `package-data` gained a `web/static/fonts/*`
  entry (`web/static/*` doesn't recurse into subdirectories).

### Fixed
- Web API had no limits on request size or numeric query params (issue
  #259): a `POST` body of unbounded size was read fully into memory before
  any validation, and `GET /api/grade?mode=bench&repeats=999999999` (or
  `mode=microbench&number=...`) passed the raw value straight through to
  the benchmark runner — a single request could burn arbitrary CPU/memory
  (local DoS). `do_POST` now rejects a `Content-Length` over 1 MiB with
  `413` (draining a bounded amount of the still-incoming body first —
  otherwise Windows resets the connection before the client can read the
  413 response) and a missing/negative/non-numeric `Content-Length` with
  `400`; `repeats`/`number` are clamped to `[1, 1000]`/`[1, 1_000_000]` via
  a new `_clamp()` helper instead of passed through unbounded.
- `config.py::load_config()` resolved `pyproject.toml` relative to the
  installed package's own `__file__` (`src/stepik_grader/` → repo root),
  so a `pipx`/wheel install pointed inside the venv where no
  `pyproject.toml` exists — `[tool.stepik-grader]` was silently never
  read and every user got hardcoded defaults regardless of their config.
  `load_config()` now resolves the path via a new
  `_resolve_pyproject_path()`: `STEPIK_GRADER_CONFIG` env override (if it
  points at an existing file) → search upward from `cwd` (pip/ruff
  pattern, new `_find_pyproject()`) → legacy `__file__`-relative fallback
  (preserves behavior when tests run from the repo root) → defaults. An
  invalid `STEPIK_GRADER_CONFIG` value no longer raises — resolution just
  continues to the next source (issue #258).

### Added
- Editable code window for mode 1 in the web UI (issue #125): the
  file-picker panel's read-only source preview is now a persistent,
  editable textarea. Picking an existing solution loads its code into it
  for editing; leaving nothing picked lets you type a new solution from
  scratch. Running now saves the editor's content to disk first — to the
  picked file if one was selected, otherwise to a new file whose name
  extends the folder's existing `task<N>_<M>.py` numbering series (or
  starts at `task_1.py`) — via new `web/viewmodels.py::save_solution()`
  and `POST /api/save-solution`, then grades the saved path as before.
- Microbench (mode 4) in the web UI (issue #187): the "Режим 4 · Microbench"
  button is no longer a disabled placeholder — it runs the real
  `timeit`-based microbenchmark with a calls-per-run profile selector
  (fast/normal/thorough/deep/hard/custom, mirroring `cli/interactive.py`'s
  `_MICRO_PROFILES`) and a results table (Min/Median/Mean/Max/StdDev in µs,
  relative %, verdict, Py-heap). New `web/viewmodels.py::grade_microbench()`
  groups solutions by subfolder via `core/test_loader.py::collect_grouped_files`
  before calling `core/grader_core.py::run_microbench_mode` once per group —
  required because that function ranks all files passed to a single call
  against each other, so per-file calls (like `grade_benchmark`'s) would make
  every result trivially "SIMILAR". A folder with more than one solution
  group only benchmarks the first (sorted) group in this MVP; the rest are
  named in an `other_groups` hint above the table. New `mode=microbench`
  branch in `server.py`'s `/api/grade` routing (`number=` query param).
- Downloader workflow in the web UI (issue #186): a new, full sidebar
  section "Загрузчик задач" (symmetric with "Проверка решений"/"Глоссарий" —
  the owner confirmed a dedicated section over the design doc's original
  "workflow-block inside Проверка решений" plan) lets you paste a Stepik
  step URL and download the task + tests without leaving the browser.
  `web/downloader_adapter.py::download_task()` is a thin adapter over
  `downloader.py::process_step_url` (no download logic duplicated); auth
  goes through a new `core/oauth_flow.try_create_session_without_browser()`,
  which only ever uses a valid token or a `refresh_token` exchange — it
  never opens a browser or blocks the request thread the way
  `create_user_session`'s third fallback would. Two small additive core
  changes support this: `save_task_files`/`process_step_url` now return the
  `(count, source)`/`task_dir` they already computed instead of `None`
  (`source` — zip/html_table/github_link/none — can't be reconstructed
  from disk after the fact, since the ZIP and GitHub-variant-A paths both
  produce an identical `tests/input.txt`+`output.txt`). New `POST
  /api/download` endpoint (the server's first `do_POST`). Verified
  end-to-end against a real Stepik step with an already-configured OAuth
  session on this machine.
- Runner Protocol abstraction (epic #136, issues #137-#139): new
  `core/runner.py` implements `docs/server-mode.md`'s already-designed
  Runner layer (#140) as real code. `Runner` (runtime-checkable Protocol),
  `RunSpec`/`RunOutcome` (raw subprocess result, no verdict), and
  `LocalRunner` (the existing `subprocess.Popen` + best-effort `RLIMIT_AS`
  + psutil RSS-polling logic, moved verbatim out of `run_single_test`).
  `grader_core.run_single_test()` now builds a `RunSpec` and delegates to
  `_RUNNER.run(spec)`; verdict/diff computation stays in `grader_core.py`.
  No behavior change — sets up a future `SandboxRunner` (#157) behind the
  same interface.
- Lazy `CONFIG` + JSON-locale i18n foundation (epic #141, issues
  #142-#145): `stepik_grader.config` no longer reads `pyproject.toml` as
  an import-time side effect — a module `__getattr__` (PEP 562) +
  cached `get_config()` defer the read to first access to `.CONFIG`, with
  every existing `from stepik_grader.config import CONFIG` call site
  unaffected. `load_config()` filters overrides via
  `dataclasses.fields(GraderConfig)` instead of the private
  `__dataclass_fields__` dunder. New `core/i18n.py` +
  `core/locales/{ru,en}.json`: an additive JSON-locale loader sitting in
  front of `cli.py`'s static `_MESSAGES` dict — `_t()` checks the JSON
  locale first, falling back to `_MESSAGES`; empty locale files today
  keep behavior byte-identical.
- Stepik client retry/backoff (epic #108, issues #109-#111):
  `make_session()` mounts an `HTTPAdapter` with a `urllib3.Retry` on
  http/https, so 429 (rate limit) and transient 5xx (500/502/503/504) are
  retried with exponential backoff (respecting `Retry-After`) for every
  request through the session, not just the call sites that already used
  `_get_with_retry()`. 4xx other than 429 still isn't retried.
- `TestResult` dataclass + `Verdict` Literal (epic #112, issues
  #113-#115): new leaf module `core/result.py` matching
  `docs/result-contract.md`'s case-result fields; `from_dict()`/
  `to_dict()` round-trip the same dict shape `run_single_test()` has
  always returned, so the public dict contract (CLI JSON, `run_tests()`/
  `run_benchmark()`, `/api/grade`) is unchanged.
  `core/reporter.print_case_verbose` now reads typed attributes instead
  of ad-hoc `dict.get()` calls with inline defaults; output is
  byte-identical.
- WEB workspace (issue #125, epic #123): split-pane layout (sidebar/result/
  detail panels), extended ErrorCard fields on `/api/grade`'s case results
  (`case_n`/`severity`/`stdin`/`expected`/`actual`/`stderr`/`exit_code`/
  `timeout_s`/`suggestions`/`glossary_ids`/`actions`), a command registry
  (`GET /api/commands`) driving action cards, scenario buttons, and a
  Ctrl+K/⌘K command palette from one shared filter, and a Glossary section
  (`GET /api/glossary`, `GET /api/glossary/<id>`, `GET /api/glossary/missing`)
  with search, card detail, and a J7 missing-concept backlog view. All
  additive on top of the existing `/api/grade` contract — no existing field
  renamed/removed. New `GraderConfig.glossary_store`/`glossary_missing_queue`
  fields configure the local card store and backlog file (both optional,
  default to a zero-config fallback). `core/grader_core.run_single_test`
  gained an additive `exit_code` field and `core/glossary.all_entries()`
  lists the compact curated glossary for that fallback.
- WEB UI redesign to match the epic #123 reference mask (`web-mvp-mask.html`
  attached to the issue): full design-token system ("Hydra" light/dark
  palette, Inter + JetBrains Mono), a grid-based `.app-shell` (fixed 220px
  sidebar navigation replacing the old topbar section-switcher and
  resizable dividers), a 4-button mode row (Compare/Tests/Bench/Microbench
  — the last a disabled placeholder for #187), and a 2-column split-pane
  with the ErrorCard detail panel moved into a "Детали" tab alongside new
  "Лог"/"Эталон" tabs. All #125 functionality (palette, action cards,
  scenario buttons, Glossary section with backlog) preserved unchanged,
  only re-skinned. New **Сравнение (Compare)** mode: `grade_benchmark()`
  gained an optional `reference` parameter (path or filename among the
  found solutions) — resolved, ranking is computed relative to it instead
  of the fastest solution, with `REFERENCE`/`FASTER` verdicts added
  alongside the existing `SIMILAR`/`SLOWER`/`MUCH_SLOWER`; unresolved
  (typo/foreign file) silently falls back to the normal ranking. Response
  gains additive `reference_source`/`reference_file` fields for the new
  tab. `core/microbench_runner.apply_relative_ranking` (shared with CLI)
  is untouched — the new ranking lives in a web-only
  `_apply_reference_ranking()`. Sidebar has a disabled "Загрузчик задач"
  placeholder for #186. Note: the Stepik-side reference-solution *import*
  (issue #55, reopened) is a separate, unrelated mechanism — this redesign
  only lets `#ref-input` point at an already-local file.

### Changed
- Corrected web UI modes 1/2 after owner feedback on #125: the redesign
  above had mistranslated the mask's "Режим 1" as a benchmark-style
  "Сравнение" (Compare) mode. Режим 1 is now the actual analogue of CLI
  mode 1 (single-file check): pick a folder, click "Найти решения" (new
  `GET /api/solutions?path=` — thin adapter over the already-used
  `find_all_solution_files`), choose one found file, see its source
  (new `GET /api/source?path=`), and run just that file — no comparison
  involved. The "Найти эталонное решение" button is a disabled placeholder
  for #55. Режим 2's "Параметры" tab is now visibly present but greyed
  out/non-clickable (tests mode genuinely has no parameters — `repeats`
  only applies to bench); Режим 1 hides that tab entirely. The bottom
  scenario-button bar (auto-shown run_again/toggle_theme/switch_section
  when nothing is selected) is removed app-wide — the command palette and
  the detail panel's action cards are unaffected. `grade_benchmark(reference=...)`
  and `_apply_reference_ranking()` from the previous entry stay in the
  code and under test, just unused by the frontend for now.

### Fixed
- Glossary exception-name detector (`_last_exception_name`) reduced false
  positives: plain text lines that happened to look like a capitalized
  identifier (e.g. an `exc.add_note()` note) were being reported as
  exception names. `_looks_like_exception_name()` now requires the
  `Error`/`Exception`/`Warning` naming convention or membership in the
  small set of builtins that don't follow it (issue #191).
- Web UI client-side `esc()` escaped `&`/`<`/`>` for text context but not
  quotes, so a value landing inside an HTML attribute (`errorCard()`'s
  `href="..."`) could still break out of it. Not exploitable today
  (`g.url` is server-controlled), but hardened ahead of more action/error
  cards being added the same way (issue #214).
- `scripts/version.py`'s logical `X.Y.Z` version (README `Version` badge)
  no longer double-counts CI's own `chore(ci): update badges [skip ci]`
  bot commits toward PATCH — it excludes them via `git rev-list
  --invert-grep` instead of `git describe --tags --long`'s raw commit
  count (issue #231).
- `core/microbench_runner.py::run_microbench()` left `stdin` unset on its
  `subprocess.Popen` call, so the child inherited the parent's stdin
  handle. Under pytest's output capturing that handle is a fake/invalid
  Windows handle, which intermittently raised `OSError: [WinError 6]`
  (invalid handle) when several microbenchmarks ran in one test session —
  found while adding tests for issue #187. Fixed by passing
  `stdin=subprocess.DEVNULL` (the child never reads real stdin — it swaps
  `sys.stdin` itself), matching the pattern already used in
  `core/runner.py`.
- **Security (High):** `downloader.py` no longer sends the Stepik OAuth
  Bearer token to third-party hosts. ZIP/GitHub test-case links extracted
  from a task's HTML text were previously fetched through the same
  authenticated `requests.Session` used for the Stepik API, leaking the
  access token to any domain a task's text happened to link to. New
  `core/stepik_client.py::external_download_get()` performs those fetches
  through a fresh, unauthenticated session, validated by
  `validate_external_url()` against an explicit host allowlist
  (`github.com`, `raw.githubusercontent.com`, `api.github.com`,
  `codeload.github.com`) with loopback/private/link-local IP literals
  rejected outright. `is_stepik_url()` still routes genuine `stepik.org`
  ZIP links through the authenticated session, since that's a first-party
  call, not a leak. `_download_github_tests()` no longer accepts a session
  parameter at all — GitHub is always third-party (issue #240, security
  audit finding F-01, part of #146/#97).
- **Security (Medium):** OAuth authorization-code flow (`authorize_via_browser()`)
  now sends a cryptographically random `state` (`secrets.token_urlsafe(32)`)
  in the authorize URL and requires the local callback server to receive the
  same value back before extracting the code. Previously the loopback
  callback server accepted the first `?code=...` it saw with no `state`
  check, so a page that lured the victim into hitting
  `http://localhost:<port>/callback?code=<attacker's code>` could bind the
  local app to the attacker's Stepik account (Login-CSRF).
  `wait_for_auth_code()`/`_make_oauth_handler()` now take a required
  `expected_state` parameter and reject a missing/mismatched `state` with a
  clear `RuntimeError` instead of ever returning a code (issue #241,
  security audit finding F-02, part of #146/#149/#97).
- **Security (Medium):** the local web UI's `/api/*` endpoints (`/api/grade`,
  `/api/download`, `/api/save-solution`, etc.) now validate the `Host` header
  against `127.0.0.1`/`localhost` and, when present, the `Origin`/`Referer`
  header against the same. The server only ever binds to loopback, but a page
  open in the user's browser could still trigger grading/download/save
  actions via a plain cross-site request (no CORS preflight for a simple
  GET) or DNS-rebinding (an attacker domain briefly resolving to
  `127.0.0.1`). A mismatched `Host` or `Origin`/`Referer` now gets a 403;
  requests with no `Origin`/`Referer` at all (non-browser clients) are
  unaffected — those headers can't be forged by page JS, unlike the request
  body/query. `/` and `/static/*` are unaffected (issue #242, security audit
  finding F-03, part of #151/#97).
- **Security (Low):** `core/storage.py::save_secrets()` now creates/rewrites
  `secrets.json` with owner-only permissions (`0600`) on POSIX, using
  `os.open(..., mode=0o600)` so the file never briefly exists with the
  process's default (usually wider) umask-based permissions between creation
  and a follow-up `chmod`. An existing `secrets.json` left over from an older
  version with wider permissions is also forced back to `0600` on the next
  save. `secrets.json` holds the OAuth access/refresh token and
  `client_secret`. On Windows `os.chmod` has no equivalent to the Unix
  group/other bits (NTFS uses ACLs, not mode bits), so the call is
  effectively a no-op there and the file's protection stays whatever the
  user's profile directory already provides (issue #243, security audit
  finding F-04, part of #149/#146/#97).
- **Security (Low):** `core/wrapper_builder.py::_build_function_wrapper()`
  (legacy function-mode wrapper) now imports `datetime`/`decimal`/`fractions`
  before `sys.path.insert(0, <solution dir>)` instead of after. Previously a
  same-named file next to the solution (e.g. a stray `datetime.py`) would
  land first in `sys.path` and shadow the real stdlib module once the
  wrapper's own `from datetime import ...` ran, breaking (or worse, silently
  altering) any test case whose input relies on that stdlib type. The other
  wrapper builder, `_build_call_wrapper()`, already did this correctly
  (issue #244, security audit finding F-05, part of #136/#97).
- `core/wrapper_builder.py::_build_function_wrapper()` — the generated
  wrapper resolved a function's positional arguments via
  `[locals()[_p] for _p in _sig.parameters]`. A list comprehension is its
  own scope, so `locals()` called inside it only ever saw the comprehension's
  own loop variable, not the module-level variables assigned from the test
  case's `input_data` — a `KeyError` on every parameter name except by
  accident on Python 3.12 (broken on 3.11 and on 3.13+, per PEP 667's
  tightened `locals()` semantics). Found while adding an end-to-end
  regression test for the F-05 fix above — that test is the first thing to
  ever actually execute this wrapper's generated code instead of just
  inspecting its source. Fixed by snapshotting `locals()` into a plain dict
  before the comprehension.
- **Security (Low):** `core/executor.py`'s module-level `EXECUTOR_TIMEOUT`
  parsing was a bare `int(os.environ.get("EXECUTOR_TIMEOUT", ...))` — a
  non-numeric value in that environment variable raised `ValueError` at
  *import time*, crashing the whole module (and, transitively, anything that
  imports it — the grader can't run at all until the env var is fixed or
  unset). New `_parse_executor_timeout()` catches the invalid value and
  falls back to `CONFIG.executor_timeout`'s default (issue #245, security
  audit finding F-06, part of #136/#97).
- `core/test_loader.py::load_test_cases()`'s Format 3 (`input.txt`/`output.txt`)
  parsing zipped `input_blocks`/`output_blocks` with `strict=False`, so a
  file pair disagreeing on the number of `# TEST_N:` blocks silently
  truncated to the shorter one — dropped test cases with no indication,
  risking a false-positive "all tests pass" from an incomplete set. It now
  warns (same `warnings.warn` pattern already used for the Format-1/3
  coexistence case just above it) when the block counts differ, naming both
  counts; the truncating behavior itself is unchanged — normal (matching)
  cases still load exactly as before (issue #246, security audit finding
  F-07, part of #97).

### Refactored
- `cli.py` decomposed into a package (`cli/`), epic #117 (issues #118-#122).
  `stepik_grader.cli` (`__init__.py`) stays the compatibility facade —
  `main()`, mode-handler/interactive-menu wrapper functions, and mutable
  i18n state (`_LANG`/`_MESSAGES`/`_LOCALE_MESSAGES`/`_t`, deliberately kept
  in place since `main()` mutates `_LANG` at runtime and moving it would
  turn the facade re-export into a stale snapshot). Four new leaf modules
  hold the actual logic, none importing `stepik_grader.cli`:
  `cli/options.py` (argparse parsing, #119); `cli/commands.py` +
  `cli/context.py` (mode handlers behind an explicit `CliContext`
  dependency-injection object, #120); `cli/rendering.py` (csv/markdown
  table output, #121 Phase 1); `cli/interactive.py` (menu/prompts,
  extending `CliContext`, #121 Phase 2). `tests/test_entrypoint.py` adds
  subprocess-level regression coverage for the `stepik-grader` console
  script and `python -m stepik_grader[.grader]` (#122). Across all five
  PRs, essentially no existing test files needed modification — the
  `CliContext` design was built specifically to keep
  `monkeypatch.setattr(cli, "...", ...)`-based tests passing unmodified
  through the move.
- `web.py` decomposed into a `web/` package, issue #125: `server.py`
  (HTTP handler/routing), `viewmodels.py` (`grade_path`/`grade_benchmark`/
  the ErrorCard mapper), `glossary_adapter.py`, `commands.py`, and
  `static/{index.html,app.css,app.js}` (JS/CSS extracted from the old
  inline `_INDEX_HTML` string, served via a small fixed route allowlist).
  Pure move — public API (`grade_benchmark`/`grade_path`/`run_server`)
  unchanged; `_Handler`/`_INDEX_HTML`/`_APP_JS`/`_case_view` re-exported
  from `web/__init__.py` for test back-compat.

### Docs
- Sandbox limits clarified in `executor.py`'s module/`main()` docstrings —
  explicitly no OS-sandbox, no FS/network isolation, trusted solutions
  only (issue #213); Windows limitations of the future
  `SandboxRunner`/`LocalRunner` documented in `docs/server-mode.md`,
  completing #140's acceptance criteria. Stale follow-up references
  cleaned up in `docs/README.md`/`docs/claude-handoff.md`; README
  `--watch` marked as requiring the `[watch]` extra (issue #215).
- README line-budget (220 lines) and local Markdown link/anchor
  guardrails: new `scripts/check_docs_guardrails.py`, wired into CI as a
  `docs-guardrails` job, documented in CONTRIBUTING.md (issue #173).
- Architecture/design docs formalized: `glossary/stdlib_inventory.py` +
  `coverage.py` registered in the DAG (#199); `docs/result-contract.md`
  for CLI/Web/API case-result fields and verdicts (#116); server-mode
  design — Runner layer, remote execution API, sandbox requirements
  (#140/#156/#157) plus ADR-0001 (#152); diagnostic/logging design with
  secret redaction (#150); Contributor Covenant `CODE_OF_CONDUCT.md`
  linked from CONTRIBUTING (#204).

### Tests
- Cross-adapter user-journey coverage for the web UI (issue #129, closing
  epic #123): most journeys from `docs/web-mvp.md § User journeys` were
  already covered incrementally across `tests/test_web*.py` as #125/#186/
  #187 landed, but the issue's own follow-up comment (after PR #185)
  explicitly said not to close it using only the original 3-item checklist.
  New `tests/test_web_journeys.py` proves three previously-untested seams
  between adapters that were each only unit-tested in isolation: a
  downloaded task's path is immediately gradable via `grade_path()` (J0→J1),
  an RE case's `glossary_ids` actually resolve to a real card through
  `glossary_adapter`/HTTP instead of a dead link (error-card→glossary
  navigation), and an entry queued mid-grading is visible through
  `glossary_adapter.glossary_missing()` — the same read path
  `GET /api/glossary/missing` uses, not just the lower-level
  `json_provider`. Command-palette keyboard flows (Ctrl+K/arrows/Enter/
  Escape) were verified manually against a running server, the same
  no-JS-test-runner tradeoff #125 already documented.

## [1.6.0] - 2026-07-08

### Added
- Glossary coverage relative to official Python/stdlib (issues #195–#198, part
  of epic #123). `GlossaryMissingEntry` gained `origin`
  (`solution`/`error`/`stdlib_scan`), `module` and `qualname` fields
  distinguishing practice-driven gaps (`MissingConceptDetector`) from
  source-driven ones, with `kind`/`status`/`origin` validation on load (issues
  #190/#195; old queues without the new fields still load with defaults). New
  leaf module `stdlib_inventory.py` builds a deterministic, offline inventory
  of Python builtins, exceptions (recursive `BaseException` walk) and a
  curated set of stdlib modules — no network, no user-code execution (issue
  #196). New `coverage.py` compares that inventory against the local card base
  and produces a `CoverageReport` (`builtins`/`exceptions`/`stdlib` categories
  with covered/missing/ratio) plus `GlossaryMissingEntry(origin="stdlib_scan")`
  backlog entries; repeated scans stay idempotent via the existing
  concept-keyed dedup (issue #197). CLI entrypoint `python -m
  stepik_grader.glossary.coverage [--cards PATH] [--missing-out PATH]
  [--modules a,b,c]` prints the coverage summary and optionally appends
  missing entries, via its own rich-optional printer so the module stays a
  leaf (issue #198). Format and API documented in `docs/glossary.md`.
- `--version` now distinguishes dev builds from releases (issue #163, closes
  epic #161): off-tag output gets an explicit `(dev build, not a release)`
  suffix appended to the existing `setuptools-scm` string; on-tag output is
  unchanged (clean `X.Y.0`).
- Live README badges, replacing a hand-maintained static `Coverage` badge that
  had silently drifted from the real number. `scripts/generate_coverage_badge.py`
  and `scripts/generate_version_badge.py` write shields.io "endpoint badge" JSON
  (`.github/badges/*.json`) from the real `pytest --cov` result and the
  project's logical `X.Y.Z` version (`scripts/version.py`) respectively; CI
  (`ubuntu-latest`/3.12 leg, push to `main` only) regenerates and commits both
  files together after each test run. A new `Version` badge sits next to
  `Release` in README so `main` drifting ahead of the last tagged release is
  visible without checking git.
- Security policy (PR #203, issue #201): `SECURITY.md` with a responsible
  disclosure process and supported-versions note; README/threat-model docs link
  to it. Full policy lives in `SECURITY.md` (not duplicated here).
- Project workflow templates (PR #203, issue #202): GitHub PR and issue
  templates under `.github/`.

- Local glossary knowledge-module foundation (issue #126, part of epic #123).
  New `stepik_grader.glossary` subpackage: typed `GlossaryCard` /
  `GlossaryMissingEntry` models, `JsonGlossaryProvider` for loading and
  searching a local JSON card base (single file or directory; search by
  id/title/aliases/keywords/tags; filter by status/tag) with clear
  `GlossaryError` on missing/broken JSON, a JSON missing-entry queue
  (`load`/`save`/`append` with dedup), and a conservative, deterministic
  `MissingConceptDetector` that finds uncovered stdlib calls, notable builtins,
  `match/case` and traceback exceptions via AST (never executes user code) and
  suppresses concepts already covered by known glossary terms. JSON format and
  Python API documented in `docs/glossary.md` with a sample fixture at
  `docs/examples/glossary.sample.json`. The external Glossary-Python project
  stays a one-way export target; the local base is the source of truth. WEB UI,
  endpoints and the exporter remain in #125/#129.
- Packaging hygiene (PR-1, epic #98): explicit MIT `LICENSE` at the repo root
  and PEP 639 SPDX license metadata in `pyproject.toml` (`license = "MIT"` +
  `license-files = ["LICENSE"]`, issue #100); PEP 561 `py.typed` marker so
  downstream consumers' type checkers see the package's type hints (issue #101).
  Build requirement bumped to `setuptools>=77` for SPDX support; `py.typed`
  declared in `[tool.setuptools.package-data]`. (Version sync, issue #99, was
  already done — see the pre-merge version rule in CLAUDE.md.)
- Glossary hints on runtime errors (issue #72, first brick of epic #96).
  New leaf module `core/glossary.py` holds a curated map of ~28 built-in
  Python exceptions → a one-line Russian hint + a link to the full card in the
  separate Glossary-Python project (not a copy of the 581-card glossary — the
  "vendor a thin layer" choice from epic #96: offline hints, link out for
  depth). Single source of truth for two surfaces: `reporter.print_case_verbose`
  prints a hint line + URL on an RE verdict (CLI verbose); `web._case_view`
  attaches a `glossary` block that the web UI renders as an error card with a
  link. `lookup_from_error` parses the exception name from the traceback's last
  line (dropping any `module.` prefix). The base URL and anchor scheme
  (`#<classname-lowercased>`) are single constants, trivially adjustable if the
  glossary's anchors change.

### Changed
- Glossary source-of-truth / coverage-truth clarification (PR #203, issues
  #194/#200): docs and the Claude handoff now state the invariant consistently —
  the internal Stepik-Python-Grader base is the content source of truth, official
  Python/stdlib is the completeness/coverage truth, and the external
  Glossary-Python is an export/vitrine target only (never the completeness
  benchmark). Canonical wording in `docs/glossary.md`; not duplicated here.
- Documentation split (PR-2, epic #102): README is now a lean showcase; heavy
  technical sections moved into a `docs/` knowledge base — `docs/architecture.md`
  (module DAG + layers, #105), `docs/project-structure.md` (file tree, #104),
  `docs/versions.md` (release-comparison table + fork-vs-original, #106). README
  becomes a lean showcase with one-line pointers to `docs/` and an
  updated table of contents. CONTRIBUTING gains a "README as showcase, `docs/`
  as knowledge base" rule so it doesn't bloat again (#107).

---

Более ранние релизы (**1.1.0 – 1.5.0**) и до-версионные записи вынесены в
[docs/changelog-archive.md](docs/changelog-archive.md) (issue #373).
