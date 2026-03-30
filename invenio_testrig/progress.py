# -*- coding: utf-8 -*-
#
# Copyright (C) 2026 CESNET z.s.p.o.
#
# invenio-testrig is free software; you can redistribute it and/or
# modify it under the terms of the MIT License; see LICENSE file for more
# details.
"""Progress."""

from typing import Protocol


class Progress(Protocol):
    """Protocol for reporting progress of the testing process."""

    def start(self, message: str, icon: str | None = None) -> None:
        """Report the start of a new step in the testing process.

        :param message: Status message to display
        :param icon: Optional icon/emoji to display with the message
        """
        ...

    def info(self, message: str, icon: str | None = None) -> None:
        """Report informational messages about the testing process.

        :param message: Informational message to display
        :param icon: Optional icon/emoji to display with the message
        """
        ...

    def success(self, message: str, icon: str | None = None) -> None:
        """Report successful completion of a step in the testing process.

        :param message: Success message to display
        :param icon: Optional icon/emoji to display with the message
        """
        ...

    def warning(self, message: str, icon: str | None = None) -> None:
        """Report a warning during the testing process.

        :param message: Warning message to display
        :param icon: Optional icon/emoji to display with the message
        """
        ...

    def error(self, message: str, icon: str | None = None) -> None:
        """Report an error during the testing process.

        :param message: Error message to display
        :param icon: Optional icon/emoji to display with the message
        """
        ...

    def text(
        self,
        message: str,
        fg: str | None = None,
        bold: bool = False,
        dim: bool = False,
    ) -> None:
        """Output styled text without semantic meaning.

        Use for structured summaries, tables, and other formatted output
        that doesn't fit the start/info/success/warning/error categories.

        :param message: Text to display
        :param fg: Foreground color name (e.g. "green", "cyan", "yellow")
        :param bold: Whether to render in bold
        :param dim: Whether to render dimmed
        """
        ...


class SilentProgress(Progress):
    def start(self, message: str, icon: str | None = None) -> None:
        pass

    def info(self, message: str, icon: str | None = None) -> None:
        pass

    def success(self, message: str, icon: str | None = None) -> None:
        pass

    def warning(self, message: str, icon: str | None = None) -> None:
        pass

    def error(self, message: str, icon: str | None = None) -> None:
        pass

    def text(
        self,
        message: str,
        fg: str | None = None,
        bold: bool = False,
        dim: bool = False,
    ) -> None:
        pass
