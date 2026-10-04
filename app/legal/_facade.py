"""
Фасад модуля поверх нескольких файлов: прежние имена (app.legal.ask, app.market_expert.FACTS_FILE …)
читаются из того файла, где они определены, а присваивание (тесты подменяют ROOT, LIVE_LIB, FAQ_FILE,
competitors_dir, FACTS_FILE) доходит до всех файлов, где это имя есть. Так подмена работает так же,
как раньше в одном файле: функции внутри пакета видят новое значение.
"""
import sys
import types


class Facade(types.ModuleType):
    def __getattr__(self, name):
        # вызывается, только если имени нет в самом фасаде
        for part in self.__dict__.get("_parts", ()):
            d = vars(part)
            if name in d:
                return d[name]
        raise AttributeError("module %r has no attribute %r" % (self.__name__, name))

    def __setattr__(self, name, value):
        parts = self.__dict__.get("_parts") or ()
        owners = [p for p in parts if name in vars(p)] if not name.startswith("__") else []
        if owners and not isinstance(value, types.ModuleType):
            for p in owners:
                setattr(p, name, value)
            return
        super().__setattr__(name, value)

    def __dir__(self):
        names = set(super().__dir__())
        for part in self.__dict__.get("_parts", ()):
            names.update(vars(part))
        return sorted(names)


def install(module_name: str, parts) -> None:
    """Превращает модуль в фасад над parts (порядок — от нижнего слоя к верхнему: первый, где имя есть, — его хозяин)."""
    mod = sys.modules[module_name]
    mod.__dict__["_parts"] = tuple(parts)
    mod.__class__ = Facade
