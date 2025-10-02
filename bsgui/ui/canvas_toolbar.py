import matplotlib.pyplot as plt
from PySide6.QtGui import QAction
from PySide6.QtCore import Qt, Signal
from matplotlib.backends.backend_qtagg import NavigationToolbar2QT as NavigationToolbar


class CustomToolbar(NavigationToolbar):
    roiDrawn = Signal(dict)
    def __init__(self, canvas, parent):
        super().__init__(canvas, parent)
        self.parent = parent

        # Configure the ROI actions
        self.drawRectangleAction = QAction("Add ROI", self)
        self.drawRectangleAction.setCheckable(True)
        self.addAction(self.drawRectangleAction)

        # Create QAction for remove rectangles
        self.removeRectangleAction = QAction("Remove ROI", self)
        self.removeRectangleAction.setCheckable(True)
        self.addAction(self.removeRectangleAction)

        # Connect the action trigger to enable/disable rectangle drawing
        self.drawRectangleAction.triggered.connect(self.toggle_rectangle_drawing)
        self.removeRectangleAction.triggered.connect(self.toggle_rectangle_remove)

        self.canvas.mpl_connect("button_press_event", self.on_mouse_press)
        self.canvas.mpl_connect("motion_notify_event", self.on_mouse_drag)
        self.canvas.mpl_connect("button_release_event", self.on_mouse_release)
        self.canvas.mpl_connect("motion_notify_event", self.on_mouse_hover)
        self.canvas.mpl_connect("pick_event", self.on_pick_rectangle)

        self.start_x = None
        self.start_y = None
        self.end_x = None
        self.end_y = None
        self.is_drawing = False
        self.is_removing = False

        self.rect = None
        self.line = None
        self.rectangles = []  # Store drawn rectangles
        self.rectangle_labels = []
        self.lines = []
        self.idx = []
        self.active_rectangle = None
        self.active_line = None

    def on_mouse_press(self, event):
        if self.is_drawing and event.button == 1:
            self.start_x = event.xdata
            self.start_y = event.ydata

    def on_mouse_release(self, event):
        if event.button == 1:
            if self.is_drawing:
                self.end_x = event.xdata
                self.end_y = event.ydata
                self.draw_rectangle()
                self.rectangles.append(self.rect)
                self.draw_sub_line(self.start_x, self.start_y, self.end_x, self.end_y)
                self.lines.append(self.line)
                self._annotate_rectangle(self.rect)
                self._emit_roi(self.rect)

                self.start_x = None
                self.start_y = None
                self.end_x = None
                self.end_y = None
                self.rect = None

            elif self.active_rectangle:
                x_st = self.active_rectangle.get_x()
                y_st = self.active_rectangle.get_y()
                x_ed = event.xdata
                y_ed = event.ydata
                if self.active_line:
                    self.active_line.remove()
                self.draw_sub_line(x_st, y_st, x_ed, y_ed)
                self.lines.append(self.line)
                self.active_rectangle = None
                self.active_line = None

    def on_mouse_hover(self, event):
        if event.inaxes and self.drawRectangleAction.isChecked():
            self.canvas.setCursor(
                Qt.CursorShape.CrossCursor
            )  # Set cursor to crosshair while hovering over the figure
        elif event.inaxes:
            self.hover_change(event)
        else:
            self.canvas.unsetCursor()  # Set cursor to default outside the figure

    def on_mouse_drag(self, event):
        if self.active_rectangle:
            self.active_rectangle.set_visible(True)
            if event.inaxes and event.button == 1:
                if event.xdata is not None and event.ydata is not None:
                    dx = event.xdata - self.active_rectangle.get_x()
                    dy = event.ydata - self.active_rectangle.get_y()
                    self.active_rectangle.set_x(event.xdata)
                    self.active_rectangle.set_y(event.ydata)
                    self._update_label_position(self.active_rectangle)
                    self.canvas.draw()

        if self.is_drawing and event.inaxes and event.button == 1:
            if self.rect:
                self.rect.remove()  # Remove the previous temporary rectangle patch
            self.end_x = event.xdata
            self.end_y = event.ydata
            self.draw_rectangle()

    def draw_sub_line(self, x_st, y_st, x_ed, y_ed):
        if self.parent.line:
            xdata = self.parent.line.get_xdata()
            ydata = self.parent.line.get_ydata()

            x_coor = [x_st, x_ed]
            y_coor = [y_st, y_ed]
            x_coor.sort()
            y_coor.sort()

            idx = (
                (xdata >= x_coor[0])
                & (xdata <= x_coor[1])
                & (ydata >= y_coor[0])
                & (ydata <= y_coor[1])
            )

            selected_x = xdata[idx]
            selected_y = ydata[idx]
            if len(idx):
                (self.line,) = self.parent.ax.plot(
                    selected_x, selected_y, color="blue", alpha=1
                )
            else:
                self.line = None
        else:
            self.line = None

    def draw_rectangle(self):
        if all(
            val is not None
            for val in [self.start_x, self.start_y, self.end_x, self.end_y]
        ):
            width = abs(self.end_x - self.start_x)
            height = abs(self.end_y - self.start_y)
            min_x = min(self.start_x, self.end_x)
            min_y = min(self.start_y, self.end_y)
            self.rect = plt.Rectangle(
                (min_x, min_y), width, height, edgecolor="white", fill=False, picker=True
            )
            ax = self.canvas.figure.gca()
            ax.add_patch(self.rect)
            self.canvas.draw()

    def _annotate_rectangle(self, rect):
        x = rect.get_x()
        y = rect.get_y()
        width = rect.get_width()
        height = rect.get_height()
        ax = self.canvas.figure.gca()
        label = ax.annotate(
            str(len(self.rectangle_labels) + 1),
            (x + width / 2.0, y + height / 2.0),
            color="white",
            fontweight="bold",
            ha="center",
            va="center",
        )
        self.rectangle_labels.append(label)
        self.canvas.draw()

    def _update_label_position(self, rect):
        try:
            idx = self.rectangles.index(rect)
        except ValueError:
            return
        label = self.rectangle_labels[idx]
        label.set_position(
            (rect.get_x() + rect.get_width() / 2.0, rect.get_y() + rect.get_height() / 2.0)
        )

    def _emit_roi(self, rect):
        data = {
            "x": rect.get_x(),
            "y": rect.get_y(),
            "width": rect.get_width(),
            "height": rect.get_height(),
        }
        self.roiDrawn.emit(data)

    def toggle_rectangle_drawing(self):
        if self.drawRectangleAction.isChecked():
            self.is_drawing = True
            self.drawRectangleAction.setChecked(True)
            self.active_rectangle = None
            self.is_removing = False
            self.removeRectangleAction.setChecked(False)
        else:
            self.is_drawing = False

    def toggle_rectangle_remove(self):
        if self.removeRectangleAction.isChecked():
            self.is_drawing = False
            self.drawRectangleAction.setChecked(False)
            self.is_removing = True
            self.active_rectangle = None
        else:
            self.is_removing = False

    def hover_change(self, event):
        if len(self.rectangles):
            for rect in self.rectangles:
                if isinstance(rect, plt.Rectangle):
                    if rect.contains(event)[0]:
                        rect.set_edgecolor("blue")
                        self.active_rectangle = rect
                    else:
                        rect.set_edgecolor("red")
                    self.canvas.draw()

    def on_pick_rectangle(self, event):
        if isinstance(event.artist, plt.Rectangle) and event.mouseevent.button == 1:
            self.active_rectangle = event.artist
            self.active_rectangle.set_visible(False)
            foundlines = self.is_line_in_rectangle()
            if foundlines:
                if foundlines[-1] is not self.parent.line:
                    self.active_line = foundlines[-1]
            if self.active_rectangle not in self.rectangles:
                self.active_rectangle.remove()
                self.active_rectangle = None
                return

        if self.is_removing:
            if self.active_rectangle in self.rectangles:
                idx = self.rectangles.index(self.active_rectangle)
                label = self.rectangle_labels.pop(idx)
                label.remove()
                self.rectangles.remove(self.active_rectangle)
            if self.active_line in self.lines:
                try:
                    self.active_line.remove()
                except Exception:
                    pass
            if self.active_rectangle is not None:
                self.active_rectangle.remove()
                self.active_rectangle = None
            self.canvas.draw()

    def is_line_in_rectangle(self):
        rect_x = self.active_rectangle.get_x()
        rect_y = self.active_rectangle.get_y()
        rect_width = self.active_rectangle.get_width()
        rect_height = self.active_rectangle.get_height()
        rect_right = rect_x + rect_width
        rect_top = rect_y + rect_height

        found_lines = []
        for line in self.parent.ax.lines:
            xdata = line.get_xdata()
            ydata = line.get_ydata()

            within_rect = (
                (xdata >= rect_x)
                & (xdata <= rect_right)
                & (ydata >= rect_y)
                & (ydata <= rect_top)
            )

            if within_rect.any():
                found_lines.append(line)

        return found_lines
