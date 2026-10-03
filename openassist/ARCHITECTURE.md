# OpenAssist product layout

The product package is split by responsibility:

- `core/` owns conversation state, intent handoff, and task lifecycle. It does
  not import Tk or Windows APIs.
- `runtime/` owns product configuration, speech providers, and the adapter that
  starts a UFO native `Session`.
- `platform/` owns Windows-only capture isolation, raw input, focus, and taskbar
  integration.
- `ui/` owns Mirally's Tk workbench, edge companion, themes, and presentation.

The modules that remain directly under `openassist/` are compatibility imports
for callers that still use the original flat paths. New product code should
import from one of the four responsibility packages above.

`UFO/` is a Git submodule pointing at the OpenAssist UFO fork. Product-owned
configuration is kept under `config/openassist/ufo/`; `runtime.config` configures
the upstream loader with that directory before UFO modules are imported.
