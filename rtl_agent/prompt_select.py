"""Inline single-select widget.

Presents a question with a highlighted, arrow-navigable option list -- similar to
the selection UI in Claude Code. Enter chooses; digits jump-select; Esc/Ctrl-C
cancels. An "Other..." entry drops to a free-text prompt so the user is never
boxed in. Falls back to a numbered text menu when prompt_toolkit is unavailable.
"""

from __future__ import annotations

OTHER_LABEL = "Other (type a custom answer)"

try:
    from prompt_toolkit.application import Application
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.layout import Layout, HSplit, Window
    from prompt_toolkit.layout.controls import FormattedTextControl
    from prompt_toolkit import PromptSession

    _HAS_PTK = True
except Exception:  # noqa: BLE001
    _HAS_PTK = False


def select_option(question: str, options: list[str], allow_other: bool = True) -> str | None:
    """Return the chosen value (or free text for "Other"), or None if cancelled."""
    choices = list(options)
    if allow_other:
        choices.append(OTHER_LABEL)

    if not _HAS_PTK:
        return _select_plain(question, choices)

    idx = [0]
    kb = KeyBindings()

    @kb.add("up")
    @kb.add("c-p")
    def _up(event):  # noqa: ANN001
        idx[0] = (idx[0] - 1) % len(choices)

    @kb.add("down")
    @kb.add("c-n")
    def _down(event):  # noqa: ANN001
        idx[0] = (idx[0] + 1) % len(choices)

    @kb.add("enter")
    def _enter(event):  # noqa: ANN001
        event.app.exit(result=choices[idx[0]])

    @kb.add("c-c")
    @kb.add("escape")
    def _cancel(event):  # noqa: ANN001
        event.app.exit(result=None)

    for n in range(1, min(len(choices), 9) + 1):
        def _make(n_):
            def handler(event):  # noqa: ANN001
                idx[0] = n_ - 1
                event.app.exit(result=choices[n_ - 1])
            return handler
        kb.add(str(n))(_make(n))

    def render():
        lines = [("bold", f"? {question}\n")]
        for i, opt in enumerate(choices):
            marker = "❯" if i == idx[0] else " "
            style = "reverse" if i == idx[0] else ""
            lines.append((style, f" {marker} {i + 1}. {opt}\n"))
        lines.append(("italic", "   (↑/↓ to move, number to jump, Enter to select, Esc to skip)"))
        return lines

    control = FormattedTextControl(render, focusable=True, show_cursor=False)
    window = Window(control, height=len(choices) + 2, wrap_lines=True)
    app = Application(layout=Layout(HSplit([window])), key_bindings=kb, full_screen=False)

    result = app.run()
    if result == OTHER_LABEL:
        return _free_text(f"{question} (type your answer)")
    return result


def _free_text(prompt_text: str) -> str | None:
    if _HAS_PTK:
        try:
            answer = PromptSession().prompt(f"{prompt_text}\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            return None
    else:
        try:
            answer = input(f"{prompt_text}\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            return None
    return answer or None


def _select_plain(question: str, choices: list[str]) -> str | None:
    print(f"? {question}")
    for i, opt in enumerate(choices):
        print(f"  {i + 1}. {opt}")
    try:
        raw = input("Select a number > ").strip()
    except (EOFError, KeyboardInterrupt):
        return None
    if not raw.isdigit() or not (1 <= int(raw) <= len(choices)):
        return None
    chosen = choices[int(raw) - 1]
    if chosen == OTHER_LABEL:
        return _free_text(question)
    return chosen


def ask_free_text(question: str) -> str | None:
    """Open-ended clarification with no fixed options."""
    return _free_text(f"? {question}")
