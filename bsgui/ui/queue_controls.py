"""Helpers for enhancing queue table interactions."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QObject, QEvent, Qt
from PySide6.QtGui import QKeyEvent, QMouseEvent
from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QTableWidget
from shiboken6 import Shiboken


class QueueTableCursorController(QObject):
    """Event filter that fine-tunes cursor behaviour for queue tables.

    The controller wraps the default Qt navigation so we can provide a smoother
    experience when users navigate with the keyboard or mouse. It is intentionally
    lightweight so it can be attached to any `QTableWidget` instance used in the
    queue views.
    """

    def __init__(self, table: QTableWidget) -> None:
        super().__init__(table)
        self._table: Optional[QTableWidget] = table
        self._header: Optional[QHeaderView] = table.horizontalHeader()
        self._right_button_active = False

        table.installEventFilter(self)
        table.viewport().installEventFilter(self)
        if self._header is not None:
            self._header.installEventFilter(self)
        table.setMouseTracking(True)
        table.destroyed.connect(self._handle_table_destroyed)
        if self._header is not None:
            self._header.destroyed.connect(self._handle_header_destroyed)
        self.configure_header_behavior()

    def detach(self) -> None:
        """Remove the event filter from the table."""

        table = self._table
        header = self._header
        if table is not None and Shiboken.isValid(table):
            table.removeEventFilter(self)
            table.viewport().removeEventFilter(self)
        if header is not None and Shiboken.isValid(header):
            header.removeEventFilter(self)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        table = self._table
        header = self._header
        if table is None or not Shiboken.isValid(table):
            return False

        if event.type() == QEvent.KeyPress and isinstance(event, QKeyEvent):
            if self._handle_key_press(event):
                return True
        viewport = table.viewport()
        if watched is viewport:
            if event.type() == QEvent.MouseMove:
                self._handle_mouse_move(event)
            elif event.type() == QEvent.Leave:
                table.unsetCursor()
        elif header is not None and Shiboken.isValid(header):
            if watched is header:
                if event.type() == QEvent.MouseButtonPress and isinstance(event, QMouseEvent):
                    self._handle_header_press(event)
                elif event.type() == QEvent.MouseMove and isinstance(event, QMouseEvent):
                    self._handle_header_move(event)
                elif event.type() == QEvent.MouseButtonRelease and isinstance(event, QMouseEvent):
                    self._handle_header_release(event)

        return super().eventFilter(watched, event)

    def configure_header_behavior(self, headersize: float | None = 90) -> None:
        """Ensure header settings allow manual column resizing."""

        header = self._header
        print(f"Configuring header behavior with size {headersize}")
        if header is None or not Shiboken.isValid(header):
            return

        if headersize is None:
            widths = [header.sectionSize(i) for i in range(header.count())]
            headersize = max([header.minimumSectionSize(), *(w for w in widths if w > 0)], default=90)
        header.setSectionsClickable(True)
        header.setStretchLastSection(True)
        print(f"Setting minimum section size to {headersize}")
        header.setMinimumSectionSize(int(headersize))
        header.setSectionResizeMode(QHeaderView.Interactive)

    def _handle_key_press(self, event: QKeyEvent) -> bool:
        table = self._table
        if table is None or not Shiboken.isValid(table):
            return False

        key = event.key()
        modifiers = event.modifiers()
        current = table.currentIndex()
        row_count = table.rowCount()
        column_count = table.columnCount()

        if not current.isValid() or row_count == 0 or column_count == 0:
            return False

        if key == Qt.Key_Tab:
            self._advance_focus(forward=True)
            return True
        if key == Qt.Key_Backtab:
            self._advance_focus(forward=False)
            return True
        if key in {Qt.Key_Return, Qt.Key_Enter} and modifiers == Qt.NoModifier:
            self._advance_focus(forward=True, wrap_rows=True)
            return True

        return False

    def _handle_mouse_move(self, event: QMouseEvent) -> None:
        table = self._table
        if table is None or not Shiboken.isValid(table):
            return

        index = table.indexAt(event.pos())
        if index.isValid():
            table.setCursor(Qt.PointingHandCursor)
            if table.selectionBehavior() == QAbstractItemView.SelectRows:
                table.setCurrentIndex(index)
        else:
            table.unsetCursor()

    def _advance_focus(self, *, forward: bool, wrap_rows: bool = False) -> None:
        table = self._table
        if table is None or not Shiboken.isValid(table):
            return

        current = table.currentIndex()
        row = current.row()
        column = current.column()
        row_count = table.rowCount()
        column_count = table.columnCount()

        next_column = column + (1 if forward else -1)
        next_row = row

        if next_column >= column_count:
            next_column = 0
            next_row = row + 1
        elif next_column < 0:
            next_column = column_count - 1
            next_row = row - 1

        if wrap_rows:
            if next_row >= row_count:
                next_row = 0
            elif next_row < 0:
                next_row = row_count - 1
        else:
            next_row = max(0, min(next_row, row_count - 1))

        next_index = table.model().index(next_row, next_column)
        if next_index.isValid():
            behavior = table.selectionBehavior()
            if behavior == QAbstractItemView.SelectRows:
                table.selectRow(next_row)
            table.setCurrentIndex(next_index)

    def _handle_header_press(self, event: QMouseEvent) -> None:
        header = self._header
        if header is None or not Shiboken.isValid(header):
            return

        self._right_button_active = event.button() == Qt.RightButton
        if self._right_button_active:
            header.setSectionResizeMode(QHeaderView.Interactive)

    def _handle_header_move(self, event: QMouseEvent) -> None:
        header = self._header
        if header is None or not Shiboken.isValid(header):
            return
        if not self._right_button_active:
            return
        logical_index = header.logicalIndexAt(event.pos())
        if logical_index < 0:
            return
        current_size = header.sectionSize(logical_index)
        if current_size > header.minimumSectionSize():
            header.setMinimumSectionSize(current_size)

    def _handle_header_release(self, event: QMouseEvent) -> None:
        header = self._header
        if header is None or not Shiboken.isValid(header):
            return
        if event.button() == Qt.RightButton and self._right_button_active:
            self._right_button_active = False
            self.configure_header_behavior(header.minimumSectionSize())

    def _handle_table_destroyed(self) -> None:
        self._table = None
        self._header = None
        self._right_button_active = False

    def _handle_header_destroyed(self) -> None:
        self._header = None
