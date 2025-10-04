"""Widget for displaying Bluesky Queue Server console output."""

from __future__ import annotations

from collections import deque
from datetime import datetime
from typing import Any, Deque, Mapping, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget, QPlainTextEdit


class QServerConsoleWidget(QWidget):
    """Scrollable view of console messages received from the Queue Server."""

    def __init__(
        self,
        *,
        parent: Optional[QWidget] = None,
        title: str = "QServer Console",
        max_entries: int = 500,
        auto_scroll: bool = True,
    ) -> None:
        super().__init__(parent)

        self._max_entries = max(1, max_entries)
        self._auto_scroll = auto_scroll
        self._buffer: Deque[str] = deque(maxlen=self._max_entries)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(8)

        self._title_label = QLabel(title)
        self._title_label.setObjectName("qserver_console_title")
        header.addWidget(self._title_label)
        header.addStretch(1)

        self._auto_scroll_button = QPushButton("Auto Scroll")
        self._auto_scroll_button.setCheckable(True)
        self._auto_scroll_button.setChecked(self._auto_scroll)
        self._auto_scroll_button.clicked.connect(self._toggle_auto_scroll)
        header.addWidget(self._auto_scroll_button)

        clear_button = QPushButton("Clear")
        clear_button.clicked.connect(self.clear)
        header.addWidget(clear_button)

        layout.addLayout(header)

        self._text = QPlainTextEdit()
        self._text.setReadOnly(True)
        self._text.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self._text.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        layout.addWidget(self._text, 1)

    # ------------------------------------------------------------------
    # Configuration helpers

    def set_title(self, title: str) -> None:
        self._title_label.setText(title)

    def set_max_entries(self, count: int) -> None:
        count = max(1, int(count))
        if count == self._max_entries:
            return
        self._max_entries = count
        self._buffer = deque(list(self._buffer)[-self._max_entries :], maxlen=self._max_entries)
        self._refresh_text()

    def set_auto_scroll(self, enabled: bool) -> None:
        self._auto_scroll = bool(enabled)
        self._auto_scroll_button.setChecked(self._auto_scroll)

    # ------------------------------------------------------------------
    # Message handling

    def append_message(self, msg: str) -> None:
        text = self._format_message(msg)
        if not text:
            return
        self._buffer.append(text)
        self._refresh_text()

    def clear(self) -> None:
        self._buffer.clear()
        self._text.clear()

    # ------------------------------------------------------------------
    # Internal utilities

    def _toggle_auto_scroll(self, checked: bool) -> None:
        self._auto_scroll = checked

    def _refresh_text(self) -> None:
        if len(self._buffer) >= self._max_entries:
            latest = self._buffer.pop()
            self._buffer.clear()
            self._buffer.append(latest)
        self._text.setPlainText("".join(self._buffer))
        if self._auto_scroll:
            cursor = self._text.textCursor()
            cursor.movePosition(cursor.MoveOperation.End)
            self._text.setTextCursor(cursor)
            self._text.ensureCursorVisible()

    @staticmethod
    def _format_message(message: Mapping[str, Any]) -> str:
        if isinstance(message, Mapping):
            text = QServerConsoleWidget._extract_text(message)
            # prefix = QServerConsoleWidget._extract_prefix(message)
            if text:
                return f"{text}" # if prefix else text
        return str(message)

    @staticmethod
    def _extract_text(message: Mapping[str, Any]) -> str:
        for key in ("text", "msg", "message"):
            value = message.get(key)
            if value:
                return str(value)
        return ""

    @staticmethod
    def _extract_prefix(message: Mapping[str, Any]) -> str:
        stream = message.get("stream") or message.get("stream_name")
        timestamp = message.get("time") or message.get("created")

        parts: list[str] = []
        if timestamp:
            try:
                ts = float(timestamp)
                parts.append(datetime.fromtimestamp(ts).strftime("%H:%M:%S"))
            except Exception:
                parts.append(str(timestamp))
        if stream:
            parts.append(str(stream))

        if not parts:
            return ""
        joined = " ".join(parts)
        return f"[{joined}] "
