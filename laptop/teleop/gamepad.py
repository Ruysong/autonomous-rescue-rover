"""Gamepad reader (pygame / SDL). Returns PadState; handles unplug and replug."""

from __future__ import annotations

import os

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
# Read the pad even when the console window is not focused.
os.environ.setdefault("SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS", "1")

import pygame  # noqa: E402

from .controller import PadState  # noqa: E402


class Gamepad:
    def __init__(self, index: int = 0) -> None:
        pygame.init()
        pygame.joystick.init()
        self._index = index
        self._joystick: pygame.joystick.JoystickType | None = None
        self.name = ""
        self._open()

    def _open(self) -> None:
        if pygame.joystick.get_count() > self._index:
            self._joystick = pygame.joystick.Joystick(self._index)
            self._joystick.init()
            self.name = self._joystick.get_name()
        else:
            self._joystick = None

    def read(self) -> PadState:
        for event in pygame.event.get():
            if event.type == pygame.JOYDEVICEREMOVED:
                self._joystick = None
            elif event.type == pygame.JOYDEVICEADDED and self._joystick is None:
                self._open()
        if self._joystick is None:
            pygame.event.pump()
            self._open()
            if self._joystick is None:
                return PadState(connected=False)

        js = self._joystick
        try:
            axes = tuple(js.get_axis(i) for i in range(js.get_numaxes()))
            buttons = frozenset(i for i in range(js.get_numbuttons()) if js.get_button(i))
        except pygame.error:
            self._joystick = None
            return PadState(connected=False)
        return PadState(connected=True, axes=axes, buttons=buttons)

    def close(self) -> None:
        pygame.quit()
