"""A tiny recording stand-in for Streamlit, used to execute the page scripts in environments where Streamlit
is not installed. It checks OUR page logic (data flow, guards, state handling). It does not prove that real
Streamlit renders the pages; the AppTest-based tests in test_apptest.py do that when Streamlit is installed."""
import contextlib
import runpy
import sys
import types
from pathlib import Path

import pandas as pd


class Stop(Exception):
    pass


class Rerun(Exception):
    pass


class SessionState(dict):
    def __getattr__(self, k):
        try:
            return self[k]
        except KeyError:
            raise AttributeError(k)

    def __setattr__(self, k, v):
        self[k] = v


class Ctx:
    """Context manager / iterable placeholder for layout elements."""
    def __init__(self, rt): self.rt = rt
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        handler = getattr(type(self.rt), name, None)        # input widgets on a column behave like st.<widget>
        return getattr(self.rt, name) if handler is not None and name != "widget" else self.rt.widget(name)
    def __iter__(self): return iter(())


class Runtime:
    def __init__(self, inputs=None):
        self.session_state = SessionState()
        self.inputs = dict(inputs or {})   # {key_or_label: value} consumed per run unless persistent
        self.calls: list[tuple] = []
        self.tables: list[pd.DataFrame] = []
        self.errors: list[str] = []
        self.texts: list[str] = []
        self.pages = []

    # ---- recording -------------------------------------------------------------------------
    def rec(self, kind, *a, **k): self.calls.append((kind, a, k))

    def _value(self, label, key, default):
        for name in (key, label):
            if name is not None and name in self.inputs:
                return self.inputs[name]
        if key and key in self.session_state:
            return self.session_state[key]
        return default

    def widget(self, name):
        def fn(*a, **k):
            self.rec(name, *a, **k)
            if name in ("error", "warning"): self.errors.append(f"{name}: {a[0] if a else ''}")
            if name in ("text", "markdown", "caption", "write", "success", "info", "subheader", "title"):
                self.texts.append(str(a[0]) if a else "")
            return Ctx(self)
        return fn

    # ---- inputs ----------------------------------------------------------------------------
    def text_input(self, label="", value="", key=None, **k):
        v = self._value(label, key, value)
        if key: self.session_state[key] = v
        return v
    text_area = text_input

    def selectbox(self, label, options=(), index=0, key=None, **k):
        options = list(options)
        v = self._value(label, key, options[index] if options else None)
        if key: self.session_state[key] = v
        return v

    def multiselect(self, label, options=(), default=None, key=None, **k):
        v = self._value(label, key, list(default or []))
        if key: self.session_state[key] = v
        return v

    def checkbox(self, label, value=False, key=None, **k):
        v = self._value(label, key, value)
        if key: self.session_state[key] = v
        return v

    def radio(self, label, options=(), key=None, **k):
        options = list(options)
        v = self._value(label, key, options[0])
        if key: self.session_state[key] = v
        return v

    def slider(self, label, min_value=0, max_value=100, value=None, key=None, **k):
        v = self._value(label, key, value if value is not None else min_value)
        if key: self.session_state[key] = v
        return v

    date_input = lambda self, label, value=None, key=None, **k: self._value(label, key, value)
    file_uploader = lambda self, label, key=None, **k: self._value(label, key, None)

    def button(self, label, key=None, disabled=False, **k):
        self.rec("button", label, key=key, disabled=disabled)
        clicked = bool(self._value(label, key, False)) and not disabled
        return clicked
    form_submit_button = button

    def columns(self, spec, **k):
        n = spec if isinstance(spec, int) else len(spec)
        return [Ctx(self) for _ in range(n)]

    def tabs(self, labels): return [Ctx(self) for _ in labels]

    def dataframe(self, data, **k):
        if isinstance(data, pd.DataFrame): self.tables.append(data)
        return Ctx(self)

    def stop(self): raise Stop()
    def rerun(self): raise Rerun()


def make_module(rt: Runtime) -> types.ModuleType:
    m = types.ModuleType("streamlit")
    m.session_state = rt.session_state
    for name in ("text_input", "text_area", "selectbox", "multiselect", "checkbox", "radio", "slider", "date_input",
                 "file_uploader", "button", "form_submit_button", "columns", "tabs", "dataframe", "stop", "rerun"):
        setattr(m, name, getattr(rt, name))
    for name in ("expander", "form", "spinner", "container", "sidebar", "empty"):
        setattr(m, name, lambda *a, _n=name, **k: (rt.rec(_n, *a), Ctx(rt))[1])
    m.sidebar = Ctx(rt)
    m.cache_resource = lambda *a, **k: (a[0] if a and callable(a[0]) else (lambda f: f))
    m.cache_data = m.cache_resource
    m.Page = lambda target, **k: types.SimpleNamespace(target=target, **k)
    m.navigation = lambda pages, **k: types.SimpleNamespace(run=lambda: (rt.pages.append(pages), pages[0].target())[1])
    m.__getattr__ = lambda name: rt.widget(name)       # module-level __getattr__ (PEP 562) for everything else
    return m


@contextlib.contextmanager
def streamlit_stub(rt: Runtime):
    saved = {k: sys.modules.get(k) for k in ("streamlit", "ui")}
    sys.modules["streamlit"] = make_module(rt)
    sys.modules.pop("ui", None)           # force ui.py to bind to the stub
    try:
        yield
    finally:
        for k, v in saved.items():
            sys.modules.pop(k, None)
            if v is not None: sys.modules[k] = v
        sys.modules.pop("plotly.express", None)


def run_page(rt: Runtime, relative: str, max_reruns: int = 3) -> None:
    """Execute a page script like Streamlit would, re-running after st.rerun() while keeping session state."""
    root = Path(__file__).resolve().parents[1]
    for _ in range(max_reruns + 1):
        rt.calls.clear(); rt.tables.clear(); rt.errors.clear(); rt.texts.clear()
        try:
            runpy.run_path(str(root / relative), run_name="__main__")
            return
        except Stop:
            return
        except Rerun:
            rt.inputs = {k: v for k, v in rt.inputs.items() if k.startswith("persist:")}   # clicks are one-shot
            continue
