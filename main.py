#! bin/python

import sys

from PyQt6.QtCore import QRectF, Qt, QTimer
from PyQt6.QtGui import QColor, QFont, QPainter, QPen, QWindow
from PyQt6.QtWidgets import (
    QApplication,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)



#-------------------UI--------------------
class ShopView(QWidget):
    def __init__(self) -> None:
        super().__init__()

        
class DBView(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Databáze")
class CartView(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Košík")
class DepoView(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Depo")
class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.current_view:QWidget
        self.views = [ShopView,CartView,DepoView,DBView]
        try:
            with open("style.css") as stylesheet:
                self.setStyleSheet(stylesheet.read())
        except:
            pass
        self.switch_view()
        self.init_ui()
        
    def init_ui(self):
        # layout
        bars = QHBoxLayout
        sidebar = QVBoxLayout
        # sidebar navigation buttons
        for view in self.views:
            title = view.title
        
    def switch_view(self,view=ShopView) -> None:
        #self.setWindowTitle(view.windowTitle())  zjistit jak funguje tahle fuck ass metoda
        pass
if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
