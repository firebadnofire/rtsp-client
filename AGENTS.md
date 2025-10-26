# AGENTS.md — AI Development Guidelines for RTSP Viewer

## Overview

This document defines how AI coding agents (such as GitHub Copilot, GPT‑based MCP agents, and similar LLM assistants) should interpret, modify, and extend the **RTSP Viewer** project. The goal is to ensure consistency, maintainability, and alignment with the project’s technical and design intentions as defined in the README.md【7†README.md†L1-L10】.

The project is a **PyQt6 + PyAV‑based RTSP viewer** that supports up to four simultaneous camera feeds in a fixed‑resolution 2×2 grid. Agents should adhere strictly to its architectural style, maintain single‑file simplicity where feasible, and ensure user‑facing features remain stable and minimal.

---

## 1. Core Architecture Principles

### 1.1 Application Scope

* The project’s purpose is **real‑time RTSP viewing and recording**. Do not add unrelated features (e.g., cloud upload, AI object detection) unless explicitly requested.
* The architecture should remain **desktop‑focused**, with local I/O and no external dependencies beyond PyQt6, PyAV, and NumPy.

### 1.2 File Structure

The repository follows a single‑file design with optional configuration and resource files:

```
rtsp-client/
├── main.py              # Main PyQt6 application
├── config.json          # Saved configurations
├── requirements.txt     # Dependencies
├── README.md            # Project overview
└── LICENSE              # License file
```

Agents must preserve this layout. If modularization is required, create submodules **only** under a new `rtsp_client/` package directory, ensuring imports remain relative.

### 1.3 Runtime Behavior

* The application initializes a **2×2 fixed‑resolution grid** (960×540 panels each).
* Streams can be started/stopped individually or collectively.
* Configuration persistence (load/save JSON) must preserve all per‑panel settings.
* Recording is handled through PyAV and stored in **crash‑safe MKV** format.

---

## 2. Coding Conventions

### 2.1 Style

* Use **PEP 8** with **descriptive variable names** and minimal global state.
* Type‑annotate all new functions.
* Prefer **signals/slots** over polling for UI updates.

### 2.2 UI Logic

* UI modifications must use **Qt Designer‑compatible constructs** or dynamic `QWidget` instantiation.
* Avoid blocking calls in the UI thread — use **QThread** or **async worker pattern** for stream operations.
* Maintain responsiveness even during stream connect/disconnect.

### 2.3 Error Handling

* Wrap all FFmpeg/PyAV operations with safe try/except blocks.
* Log or print concise runtime errors without crashing the UI.
* Ensure reconnect logic follows the README definition (auto‑retry on failure)【7†README.md†L97-L101】.

---

## 3. Agent Behavioral Directives

### 3.1 When Generating Code

* Preserve all existing function names and signatures unless the change is explicitly structural.
* Avoid hard‑coding paths; use `os.path` and user prompts for files.
* When adding features, **update README.md and requirements.txt** automatically.
* Always test PyQt6 widget construction within a `QApplication` context.

### 3.2 When Refactoring

* Maintain backward compatibility with existing `config.json` files.
* Do not change the configuration schema without migration logic.
* Preserve recording, snapshot, and fullscreen behaviors exactly.

### 3.3 When Documenting

* Insert docstrings for all new methods and classes.
* Summarize major updates under a `## Changelog` section in the README.
* Keep comments concise and technical — avoid conversational tone.

---

## 4. Functional Extension Rules

| Category        | Guidelines                                                                                  |
| --------------- | ------------------------------------------------------------------------------------------- |
| **UI/UX**       | Keep minimalist; flat layout, blue highlight for active panel. No theme engine integration. |
| **Streaming**   | Maintain PyAV usage. Any ffmpeg CLI fallback must be optional and platform‑portable.        |
| **Recording**   | Ensure MKV output integrity and concurrent stream safety.                                   |
| **Persistence** | Use JSON only; do not introduce SQL or YAML.                                                |
| **Performance** | Frame decode/render must not exceed 16 ms (≈60 FPS target).                                 |

---

## 5. Testing and Validation

### 5.1 Local Testing

Agents should verify functionality by simulating streams using local FFmpeg test sources:

```bash
ffmpeg -re -f lavfi -i testsrc=size=1920x1080:rate=30 -f rtsp rtsp://localhost:8554/test
```

Then confirm UI responsiveness, snapshot accuracy, and recording stability.

### 5.2 Static Checks

Before committing code, agents must:

* Run `flake8` or `ruff` for linting.
* Validate `requirements.txt` matches imports.
* Ensure no Qt warnings occur during startup.

---

## 6. AI‑to‑AI Collaboration Guidelines

If multiple agents (e.g., Copilot + GPT) are used:

* **Copilot**: responsible for low‑level function completions and syntax.
* **GPT‑based agent**: oversees structure, documentation, and test coverage.
* Changes must be committed to a feature branch, with an auto‑generated PR summary describing affected modules.

---

## 7. Deployment & Packaging

* The application should remain **cross‑platform** for Linux, macOS, and Windows.
* Use `pyinstaller` or `cx_Freeze` for generating standalone builds.
* Include `README.md` and `LICENSE` in the final build package.

---

## 8. Prohibited Modifications

* Do not introduce online dependencies (e.g., camera discovery APIs, web services).
* Do not replace PyQt6 with Tkinter, Kivy, or other frameworks.
* Do not alter recording formats away from MKV.
* Do not introduce cloud analytics, telemetry, or ads.

---

## 9. Maintenance Workflow

### 9.1 Branch Strategy

* `main`: stable releases.
* `dev`: active development and testing.
* `copilot/*`: autonomous agent workspaces.

### 9.2 Commit Message Format

```
[scope]: short summary

Detailed description (optional)

Refs: issue #ID or PR link
```

### 9.3 Release Notes

Agents generating a new release must append an entry to `CHANGELOG.md` including:

* Version
* Date
* Key changes
* Compatibility notes

---

## 10. Final Principles

* The agent must **prioritize stability, readability, and determinism** over experimental code.
* Every code addition must be **traceable, documented, and testable**.
* The overall behavior should stay consistent with the project’s mission: *a simple, reliable RTSP viewer and recorder*【7†README.md†L1-L10】.
