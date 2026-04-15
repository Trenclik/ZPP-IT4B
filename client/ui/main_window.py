import sys
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout,
    QHBoxLayout, QTextEdit, QLineEdit, QPushButton
)
from PyQt6.QtCore import Qt


class ChatWidget(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Simple Chat Widget")
        self.setGeometry(100, 100, 500, 600)

        # Central widget and main layout
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)

        # Message display area (read‑only)
        self.message_display = QTextEdit()
        self.message_display.setReadOnly(True)
        self.message_display.setPlaceholderText("Chat history will appear here...")
        main_layout.addWidget(self.message_display)

        # Input area (line edit + send button)
        input_layout = QHBoxLayout()
        self.input_field = QLineEdit()
        self.input_field.setPlaceholderText("Type your message here...")
        self.send_button = QPushButton("Send")
        input_layout.addWidget(self.input_field)
        input_layout.addWidget(self.send_button)
        main_layout.addLayout(input_layout)

        # Connect signals
        self.send_button.clicked.connect(self.send_message)
        self.input_field.returnPressed.connect(self.send_message)

    def send_message(self):
        """Get the text from input, add it to the chat display, and clear the input."""
        message = self.input_field.text().strip()
        if not message:
            return

        # Append the user's message to the display
        self.message_display.append(f"You: {message}")
        self.input_field.clear()

        # Optional: Add a dummy reply to demonstrate two‑way chat.
        # Remove or comment out this block if you only want user messages.
        self.message_display.append("Bot: Thanks for your message!")


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = ChatWidget()
    window.show()
    sys.exit(app.exec())