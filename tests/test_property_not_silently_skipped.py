"""Property-набор не имеет права исчезнуть молча (issue #1004, находка `QA-2-07`).

``tests/test_property.py`` начинается с ``pytest.importorskip("hypothesis")``.
Строка стоит там не зря: жёсткий импорт отсутствующего пакета обрушивал ВСЮ
коллекцию pytest, а не один файл (issue #646). Но у неё есть вторая половина, о
которой никто не договаривался: пропадёт ``hypothesis`` — и восемь
property-тестов превратятся в «8 skipped» с кодом возврата 0. Прогон зелёный,
PR мержится, а инварианты нормализатора и разборщика больше никто не проверяет.

**Почему здесь нет переменной окружения, в отличие от соседних guard'ов.**
У e2e (``STEPIK_REQUIRE_E2E_TESTS``) и песочницы (``STEPIK_REQUIRE_SANDBOX_TESTS``)
она нужна, потому что их зависимости **необязательны по замыслу**: ``playwright``
живёт в отдельной экстре ``[e2e]``, песочница привязана к ОС, и у разработчика
без них прогон обязан быть зелёным. У ``hypothesis`` этого выбора нет — он лежит
в ``[dev]``, в том же списке, что и сам ``pytest``. Раз pytest запустился,
``[dev]`` установлена; отсутствие ``hypothesis`` рядом означает не «облегчённое
окружение», а сломанное. Флаг здесь был бы вымогательством согласия на то, что и
так истинно, а выключенный по умолчанию guard не guard.

**И сам guard не смеет скипнуться.** ``pytest.importorskip`` бросает
``Skipped``, тот наследуется от ``BaseException``, и наивная попытка загрузить
модуль-набор из другого теста уносила бы в skip заодно и проверяющего — ровно
тот класс, что уже ловили в e2e (находка `QA-2-03`: фикстура разворачивалась
раньше проверки флага, и сломанное окружение пропускало сам guard). Поэтому
``Skipped`` здесь перехватывается и переводится в отказ.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys
from types import ModuleType

import pytest

_SUITE = pathlib.Path(__file__).parent / "test_property.py"


def _load_suite() -> ModuleType:
    """Загрузить property-набор отдельным модулем, не мешая его же сбору.

    Импорт под собственным именем: pytest собирает ``tests/test_property.py``
    сам, и второй вход под тем же именем в ``sys.modules`` был бы подменой.

    **Перевод пропуска в отказ живёт здесь, а не в одном из тестов.** Первая
    редакция держала его в единственном guard'е, и проба на по-настоящему
    отсутствующем ``hypothesis`` показала цену: два остальных теста ушли в
    skip сами — ``Skipped`` из ``importorskip`` пролетал сквозь них наружу.
    Guard, пропускающий себя, неотличим от отсутствующего (находка `QA-2-03`),
    поэтому загрузчик один и отказывает он всем сразу.
    """
    spec = importlib.util.spec_from_file_location("_property_suite_probe", _SUITE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except pytest.skip.Exception as skipped:
        pytest.fail(f"property-набор ушёл в skip целиком: {skipped}")
    except ImportError as error:
        pytest.fail(f"property-набор не загрузился: {error}")
    return module


def test_hypothesis_is_installed() -> None:
    """``hypothesis`` объявлен в ``[dev]`` — значит обязан быть рядом с pytest.

    Проверка отвечает на вопрос «зависимость действительно доехала», а не «её
    кто-то объявил»: объявление живёт в ``pyproject.toml`` и остаётся верным,
    даже когда установка развалилась.
    """
    assert importlib.util.find_spec("hypothesis") is not None, (
        "hypothesis не установлен, хотя объявлен в [dev] рядом с pytest — "
        "property-набор ушёл бы в skip, а прогон остался бы зелёным"
    )


def test_the_property_suite_loads_instead_of_skipping() -> None:
    """Набор загружается, а не превращается в пропуск.

    Прогнано на настоящем отсутствии ``hypothesis`` (пакет убран из окружения,
    не подменён заглушкой): без этого guard'а прогон отвечал «8 skipped» и код
    возврата 0, с ним — красный отказ, называющий причину.
    """
    module = _load_suite()

    assert module.__name__ == "_property_suite_probe"


def test_the_suite_really_carries_property_tests() -> None:
    """В наборе есть тесты, которыми управляет hypothesis, а не только импорты.

    Загрузившийся модуль без единого ``@given`` — то же исчезновение, только
    другим путём: файл на месте, инвариантов в нём нет.
    """
    module = _load_suite()
    driven = [
        name
        for name, value in vars(module).items()
        if name.startswith("test_") and hasattr(value, "hypothesis")
    ]

    assert driven, "в property-наборе не осталось ни одного теста под @given"


def test_every_given_test_is_named_so_pytest_collects_it() -> None:
    """Тест под ``@given``, названный не ``test_*``, не собирается вовсе.

    Молчаливое исчезновение поштучно: декоратор на месте, инвариант описан, а в
    прогон функция не попадает — и отличить это от «такого теста нет» нечем.
    """
    module = _load_suite()
    misnamed = [
        name
        for name, value in vars(module).items()
        if hasattr(value, "hypothesis") and not name.startswith("test_")
    ]

    assert misnamed == [], f"под @given, но не собирается pytest: {misnamed}"
