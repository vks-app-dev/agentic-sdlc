import pytest

from agentic_sdlc import human


@pytest.fixture(autouse=True)
def person_at_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests drive the CLI as a person in a terminal who confirms. Tests of the agent path undo this."""
    monkeypatch.setattr(human, "interactive", lambda: True)
    monkeypatch.setattr(human, "confirm_in_terminal", lambda summary, req, assume_yes=False: True)
