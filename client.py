import sys
import threading
import socket
import json
import sqlite3
import uuid
from datetime import datetime
from PyQt6.QtWidgets import *
from PyQt6.QtCore import *
from PyQt6.QtGui import QTextCursor

# ---------- Constants ----------
SERVER_HOST = '127.0.0.1'
SERVER_PORT = 5555

# ---------- Database (SQLite) ----------
class LocalDB:
    def __init__(self, username):
        self.conn = sqlite3.connect(f"{username}_chat.db")
        self.cursor = self.conn.cursor()
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS messages (
                id TEXT PRIMARY KEY,
                conversation_id TEXT,
                sender TEXT,
                content TEXT,
                timestamp TEXT,
                is_outgoing INTEGER
            )
        ''')
        self.conn.commit()

    def store_message(self, msg_id, conv_id, sender, content, timestamp, is_outgoing):
        self.cursor.execute(
            "INSERT OR REPLACE INTO messages VALUES (?,?,?,?,?,?)",
            (msg_id, conv_id, sender, content, timestamp, is_outgoing)
        )
        self.conn.commit()

    def get_messages(self, conv_id, limit=50):
        self.cursor.execute(
            "SELECT sender, content, timestamp, is_outgoing FROM messages WHERE conversation_id = ? ORDER BY timestamp LIMIT ?",
            (conv_id, limit)
        )
        return self.cursor.fetchall()

    def close(self):
        self.conn.close()

# ---------- Network Thread ----------
class NetworkThread(QThread):
    message_received = pyqtSignal(dict)
    connected = pyqtSignal()
    error = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.sock = None
        self.running = False

    def connect_to_server(self):
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.connect((SERVER_HOST, SERVER_PORT))
            self.running = True
            self.start()
            self.connected.emit()
        except Exception as e:
            self.error.emit(str(e))

    def send_command(self, command, data):
        if self.sock is None:          # <-- add this check
            self.error.emit("Not connected to server")
            return
        try:
            msg = json.dumps({"command": command, "data": data}) + "\n"
            self.sock.send(msg.encode())
        except (BrokenPipeError, OSError) as e:
            self.error.emit(f"Send failed: {e}")

    def run(self):
        buffer = ""
        while self.running:
            try:
                if self.sock is None:
                    break
                data = self.sock.recv(4096).decode()
                if not data:
                    break
                buffer += data
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    if line:
                        self.message_received.emit(json.loads(line))
            except:
                break
        self.running = False

    def stop(self):
        self.running = False
        if self.sock:
            self.sock.close()

# ---------- Chat Widget (Reusable for Private & Group) ----------
class ChatWidget(QWidget):
    def __init__(self, conv_id, conv_name, chat_type, network, local_db, current_user):
        super().__init__()
        self.conv_id = conv_id
        self.conv_name = conv_name
        self.chat_type = chat_type      # "private" or "group"
        self.network = network
        self.local_db = local_db
        self.current_user = current_user
        self.init_ui()
        self.load_history()

    def init_ui(self):
        layout = QVBoxLayout()
        self.text_display = QTextBrowser()
        self.text_input = QLineEdit()
        self.send_btn = QPushButton("Send")
        layout.addWidget(self.text_display)
        layout.addWidget(self.text_input)
        layout.addWidget(self.send_btn)
        self.setLayout(layout)

        self.send_btn.clicked.connect(self.send_message)
        self.text_input.returnPressed.connect(self.send_message)

    def send_message(self):
        content = self.text_input.text().strip()
        if not content:
            return
        timestamp = datetime.now().isoformat()
        msg_id = str(uuid.uuid4())
        # Store locally first
        self.local_db.store_message(msg_id, self.conv_id, self.current_user,
                                    content, timestamp, 1)
        self.append_message(self.current_user, content, timestamp, True)
        # Send to server
        if self.chat_type == "private":
            self.network.send_command("send_message", {
                "type": "private",
                "target": self.conv_name,   # for private, conv_name is other username
                "content": content,
                "timestamp": timestamp
            })
        else:
            self.network.send_command("send_message", {
                "type": "group",
                "target": self.conv_id,     # group_id
                "content": content,
                "timestamp": timestamp
            })
        self.text_input.clear()

    def append_message(self, sender, content, timestamp, is_outgoing=False):
        prefix = "You: " if is_outgoing else f"{sender}: "
        self.text_display.append(f"[{timestamp[:19]}] {prefix}{content}")
        self.text_display.moveCursor(QTextCursor.MoveOperation.End)

    def load_history(self):
        rows = self.local_db.get_messages(self.conv_id)
        for sender, content, timestamp, is_outgoing in rows:
            self.append_message(sender, content, timestamp, bool(is_outgoing))

    def on_new_message(self, data):
        if self.chat_type == "private":
            sender = data['from']
            content = data['content']
            timestamp = data['timestamp']
        else:
            sender = data['from']
            content = data['content']
            timestamp = data['timestamp']
        # Store locally
        msg_id = str(uuid.uuid4())
        self.local_db.store_message(msg_id, self.conv_id, sender, content, timestamp, 0)
        self.append_message(sender, content, timestamp, False)

# ---------- Main Window ----------
class MainWindow(QMainWindow):
    def __init__(self, username, user_id, network, local_db):
        super().__init__()
        self.username = username
        self.user_id = user_id
        self.network = network
        self.local_db = local_db
        self.chat_widgets = {}   # conv_id -> ChatWidget
        self.init_ui()
        self.load_groups()
        self.network.message_received.connect(self.on_server_message)

    def init_ui(self):
        self.setWindowTitle(f"Chat - {self.username}")
        self.setGeometry(100, 100, 800, 600)
        self.tab_widget = QTabWidget()
        self.setCentralWidget(self.tab_widget)

        # Private chats tab
        self.private_tab = QWidget()
        self.private_layout = QVBoxLayout()
        self.private_tab.setLayout(self.private_layout)
        self.tab_widget.addTab(self.private_tab, "Private Chats")

        # Groups tab
        self.group_tab = QWidget()
        self.group_layout = QVBoxLayout()
        self.group_tab.setLayout(self.group_layout)
        self.tab_widget.addTab(self.group_tab, "Groups")

        # Buttons for new actions
        new_private_btn = QPushButton("New Private Chat")
        new_private_btn.clicked.connect(self.new_private_chat)
        self.private_layout.addWidget(new_private_btn)

        create_group_btn = QPushButton("Create Group")
        create_group_btn.clicked.connect(self.create_group)
        self.group_layout.addWidget(create_group_btn)

    def load_groups(self):
        self.network.send_command("get_groups", {})

    def add_chat_tab(self, conv_id, title, chat_widget, is_group=False):
        parent = self.group_tab if is_group else self.private_tab
        # Find a container (scroll area or just add directly)
        for i in range(parent.layout().count()):
            widget = parent.layout().itemAt(i).widget()
            if isinstance(widget, QTabWidget):
                tab_widget = widget
                break
        else:
            tab_widget = QTabWidget()
            parent.layout().addWidget(tab_widget)
        tab_widget.addTab(chat_widget, title)
        self.chat_widgets[conv_id] = chat_widget

    def new_private_chat(self):
        username, ok = QInputDialog.getText(self, "New Private Chat", "Enter username:")
        if ok and username and username != self.username:
            conv_id = f"priv_{'_'.join(sorted([self.username, username]))}"
            if conv_id in self.chat_widgets:
                QMessageBox.information(self, "Info", "Chat already open")
                return
            chat = ChatWidget(conv_id, username, "private", self.network, self.local_db, self.username)
            self.add_chat_tab(conv_id, username, chat, is_group=False)

    def create_group(self):
        name, ok = QInputDialog.getText(self, "Create Group", "Group name:")
        if ok and name:
            members_str, ok = QInputDialog.getText(self, "Create Group",
                                                   "Member usernames (comma separated):")
            members = [m.strip() for m in members_str.split(",") if m.strip()] if ok else []
            self.network.send_command("create_group", {"name": name, "members": members})

    def on_server_message(self, msg):
        # Handle unsolicited events (no 'status' key)
        if 'command' in msg:
            if msg['command'] == 'new_message':
                data = msg['data']
                conv_id = data['conversation_id']
                if conv_id in self.chat_widgets:
                    self.chat_widgets[conv_id].on_new_message(data)
                else:
                    # Automatically open chat for new conversation
                    if data['type'] == 'private':
                        other = data['from']
                        chat = ChatWidget(conv_id, other, "private",
                                        self.network, self.local_db, self.username)
                        self.add_chat_tab(conv_id, other, chat, is_group=False)
                        chat.on_new_message(data)
                    else:  # group
                        # Group message from unknown group – request group list
                        self.network.send_command("get_groups", {})
            return

        # Handle responses (always contain 'status')
        if 'status' not in msg:
            return

        if msg['status'] == 'ok':
            # Response to get_groups
            if 'groups' in msg:
                groups = msg['groups']
                for grp in groups:
                    conv_id = f"group_{grp['group_id']}"
                    if conv_id not in self.chat_widgets:
                        chat = ChatWidget(conv_id, grp['name'], "group",
                                        self.network, self.local_db, self.username)
                        self.add_chat_tab(conv_id, grp['name'], chat, is_group=True)
            # Response to get_messages (if you implement history loading)
            elif 'messages' in msg:
                # Handle history if needed
                pass
            # Other generic OK responses can be ignored or logged
        else:
            # Error response
            QMessageBox.warning(self, "Error", msg.get('message', 'Unknown error'))

# ---------- Login / Register Dialog ----------
class LoginDialog(QDialog):
    def __init__(self):
        super().__init__()
        self.network = NetworkThread()
        self.network.connected.connect(self.on_connected)
        self.network.error.connect(self.on_network_error)
        self.network.message_received.connect(self.on_server_response)
        self.setWindowTitle("Chat Login")
        self.setModal(True)
        self.init_ui()
        self.network.connect_to_server()
        self.waiting_for_response = False

    def init_ui(self):
        layout = QVBoxLayout()
        self.username_edit = QLineEdit()
        self.password_edit = QLineEdit()
        self.password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.login_btn = QPushButton("Login")
        self.register_btn = QPushButton("Register")
        layout.addWidget(QLabel("Username:"))
        layout.addWidget(self.username_edit)
        layout.addWidget(QLabel("Password:"))
        layout.addWidget(self.password_edit)
        layout.addWidget(self.login_btn)
        layout.addWidget(self.register_btn)
        self.setLayout(layout)

        self.login_btn.clicked.connect(self.login)
        self.register_btn.clicked.connect(self.register)
        self.login_btn.setEnabled(False)
        self.register_btn.setEnabled(False)

    def on_connected(self):
        self.login_btn.setEnabled(True)
        self.register_btn.setEnabled(True)

    def on_network_error(self, err):
        QMessageBox.critical(self, "Connection Error", f"Cannot connect to server: {err}")
        self.login_btn.setEnabled(False)
        self.register_btn.setEnabled(False)

    def on_server_response(self, resp):
        if not self.waiting_for_response:
            return
        self.waiting_for_response = False
        if resp.get('status') == 'ok':
            if self.pending_action == 'login':
                self.accept()
                self.main_window = MainWindow(
                    self.username_edit.text(),
                    resp['user_id'],
                    self.network,
                    LocalDB(self.username_edit.text())
                )
                self.main_window.show()
            else:  # register
                QMessageBox.information(self, "Success", "Registration successful. You can now login.")
        else:
            QMessageBox.warning(self, "Error", resp.get('message', 'Unknown error'))

    def login(self):
        if self.waiting_for_response:
            return
        self.pending_action = 'login'
        self.waiting_for_response = True
        self.network.send_command("login", {
            "username": self.username_edit.text(),
            "password": self.password_edit.text()
        })

    def register(self):
        if self.waiting_for_response:
            return
        self.pending_action = 'register'
        self.waiting_for_response = True
        self.network.send_command("register", {
            "username": self.username_edit.text(),
            "password": self.password_edit.text()
        })

# ---------- Application Entry Point ----------
if __name__ == "__main__":
    app = QApplication(sys.argv)
    login = LoginDialog()
    if login.exec() == QDialog.DialogCode.Accepted:
        sys.exit(app.exec())
    else:
        sys.exit(0)