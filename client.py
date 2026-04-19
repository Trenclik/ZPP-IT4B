import sys
import socket
import json
import sqlite3
import uuid
from datetime import datetime
from PyQt6.QtWidgets import *
from PyQt6.QtCore import *
from PyQt6.QtGui import QTextCursor, QAction

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
    disconnected = pyqtSignal()


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
        self.disconnected.emit()

    def stop(self):
        self.running = False
        if self.sock:
            self.sock.close()

# ---------- Chat Widget (Reusable for Private & Group) ----------
class ChatWidget(QWidget):
    def __init__(self, conv_id, conv_name, chat_type, network, local_db, current_user, raw_target=None):
        super().__init__()
        self.conv_id = conv_id          # e.g. "group_abc123" or "priv_alice_bob"
        self.conv_name = conv_name      # display name
        self.chat_type = chat_type      # "private" or "group"
        self.raw_target = raw_target if raw_target else (conv_name if chat_type == "private" else None)
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
        
        # Button bar for extra actions
        button_bar = QHBoxLayout()
        self.leave_btn = QPushButton("Leave Group")
        self.leave_btn.setVisible(self.chat_type == "group")
        self.leave_btn.clicked.connect(self.leave_group)
        button_bar.addStretch()
        button_bar.addWidget(self.leave_btn)
        
        layout.addWidget(self.text_display)
        layout.addLayout(button_bar)
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
                "target": self.raw_target,   # username
                "content": content,
                "timestamp": timestamp
            })
        else:  # group
            self.network.send_command("send_message", {
                "type": "group",
                "target": self.raw_target,   # raw group_id (without "group_" prefix)
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
            
    def leave_group(self):
        reply = QMessageBox.question(self, "Leave Group",
                                    f"Are you sure you want to leave '{self.conv_name}'?",
                                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            self.network.send_command("leave_group", {"group_id": self.raw_target})
    
    def on_new_message(self, data):
        if self.chat_type == "private":
            sender = data['from_username']   # was 'from'
            content = data['content']
            timestamp = data['timestamp']
        else:  # group
            sender = data['from_username']   # was 'from'
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
        self.load_private_conversations()
        self.network.message_received.connect(self.on_server_message)
        self.network.disconnected.connect(self.on_disconnected)

    def init_ui(self):
        self.setWindowTitle(f"Chat - {self.username}")
        self.setGeometry(100, 100, 800, 600)
        menubar = self.menuBar()
        account_menu = menubar.addMenu("Account")
        logout_action = QAction("Logout", self)
        logout_action.triggered.connect(self.logout)
        delete_action = QAction("Delete Account", self)
        delete_action.triggered.connect(self.delete_account)
        account_menu.addAction(logout_action)
        account_menu.addAction(delete_action)
        
        # Main tab widget (switches between Private Chats and Groups)
        self.main_tabs = QTabWidget()
        self.setCentralWidget(self.main_tabs)

        # ----- Private Chats Container -----
        self.private_container = QWidget()
        self.private_layout = QVBoxLayout(self.private_container)
        self.private_tabs = QTabWidget()          # holds individual private chats
        self.private_layout.addWidget(self.private_tabs)
        new_private_btn = QPushButton("New Private Chat")
        new_private_btn.clicked.connect(self.new_private_chat)
        self.private_layout.addWidget(new_private_btn)
        self.main_tabs.addTab(self.private_container, "Private Chats")

        # ----- Groups Container -----
        self.group_container = QWidget()
        self.group_layout = QVBoxLayout(self.group_container)
        self.group_tabs = QTabWidget()            # holds group chats
        self.group_layout.addWidget(self.group_tabs)
        create_group_btn = QPushButton("Create Group")
        create_group_btn.clicked.connect(self.create_group)
        self.group_layout.addWidget(create_group_btn)
        self.main_tabs.addTab(self.group_container, "Groups")

    def add_chat_tab(self, conv_id, title, chat_widget, is_group=False):
        """Add a chat widget as a new tab in the appropriate QTabWidget."""
        if is_group:
            tab_widget = self.group_tabs
        else:
            tab_widget = self.private_tabs
        tab_widget.addTab(chat_widget, title)
        self.chat_widgets[conv_id] = chat_widget
        # Switch to the newly added tab so the user sees it immediately
        tab_widget.setCurrentWidget(chat_widget)

    def load_groups(self):
        self.network.send_command("get_groups", {})
    def load_private_conversations(self):
        self.network.send_command("get_private_conversations", {})
        
    def new_private_chat(self):
        username, ok = QInputDialog.getText(self, "New Private Chat", "Enter username:")
        if ok and username and username != self.username:
            self.pending_private_chat = username
            self.network.send_command("check_user_exists", {"username": username})
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
        # 1. Handle unsolicited events (no 'status' key)
        if 'command' in msg:
            if msg['command'] == 'new_message':
                data = msg['data']
                conv_id = data['conversation_id']
                msg_id = data['message_id']
                sender = data['from_username']      # was 'from'
                content = data['content']
                timestamp = data['timestamp']
                is_outgoing = (sender == self.username)

                # Store locally using server-provided ID
                self.local_db.store_message(msg_id, conv_id, sender, content, timestamp, is_outgoing)

                if conv_id in self.chat_widgets:
                    self.chat_widgets[conv_id].on_new_message(data)
                else:
                    if data['type'] == 'private':
                        other = data['from_username']   # was 'from'
                        chat = ChatWidget(conv_id, other, "private",
                                        self.network, self.local_db, self.username,
                                        raw_target=other)
                        self.add_chat_tab(conv_id, other, chat, is_group=False)
                        self.main_tabs.setCurrentWidget(self.private_container)
                    else:  # group
                        group_id = data['group_id']
                        group_name = data.get('group_name', group_id)
                        chat = ChatWidget(conv_id, group_name, "group",
                                        self.network, self.local_db, self.username,
                                        raw_target=group_id)
                        self.add_chat_tab(conv_id, group_name, chat, is_group=True)
                    self.chat_widgets[conv_id].on_new_message(data)
            elif msg['command'] == 'new_group':
                data = msg['data']
                group_id = data['group_id']
                group_name = data['name']
                conv_id = f"group_{group_id}"
                if conv_id not in self.chat_widgets:
                    chat = ChatWidget(conv_id, group_name, "group",
                                    self.network, self.local_db, self.username,
                                    raw_target=group_id)
                    self.add_chat_tab(conv_id, group_name, chat, is_group=True)
            elif msg['command'] == 'member_left':
                data = msg['data']
                group_id = data['group_id']
                username = data['username']
                conv_id = f"group_{group_id}"
                if conv_id in self.chat_widgets:
                    chat = self.chat_widgets[conv_id]
                    chat.append_message("System", f"{username} left the group", datetime.now().isoformat(), is_outgoing=False)
            elif msg['command'] == 'message_ack':
                data = msg['data']
                msg_id = data['message_id']
                print(f"Message {msg_id} delivered to server")
            elif 'exists' in msg:
                if msg['exists']:
                    username = self.pending_private_chat
                    conv_id = f"priv_{'_'.join(sorted([self.username, username]))}"
                    if conv_id in self.chat_widgets:
                        QMessageBox.information(self, "Info", "Chat already open")
                    else:
                        chat = ChatWidget(conv_id, username, "private",
                                        self.network, self.local_db, self.username,
                                        raw_target=username)
                        self.add_chat_tab(conv_id, username, chat, is_group=False)
                else:
                    QMessageBox.warning(self, "Error", f"User '{self.pending_private_chat}' does not exist.")
                self.pending_private_chat = None
            return

        # 2. Handle responses (contain 'status')
        if 'status' not in msg:
            print("DEBUG: Unexpected message without command or status:", msg)
            return

        if msg['status'] == 'ok':
            # Response to get_groups
            if 'groups' in msg:
                groups = msg['groups']
                for grp in groups:
                    group_id = grp['group_id']
                    group_name = grp['name']
                    conv_id = f"group_{group_id}"
                    if conv_id not in self.chat_widgets:
                        chat = ChatWidget(conv_id, group_name, "group",
                                        self.network, self.local_db, self.username,
                                        raw_target=group_id)
                        self.add_chat_tab(conv_id, group_name, chat, is_group=True)

            # Response to get_private_conversations
            elif 'conversations' in msg:
                conv_ids = msg['conversations']
                for conv_id in conv_ids:
                    self.network.send_command("get_messages", {"conversation_id": conv_id, "limit": 50})

            # Response to get_messages
            elif 'conversation_id' in msg and 'messages' in msg:
                conv_id = msg['conversation_id']
                messages = msg['messages']
                for m in messages:
                    msg_id = m['message_id']
                    is_outgoing = (m['sender'] == self.username)
                    self.local_db.store_message(msg_id, conv_id, m['sender'], m['content'], m['timestamp'], is_outgoing)
                # Create chat tab if it doesn't exist yet (only for private, groups are already handled)
                if conv_id not in self.chat_widgets and conv_id.startswith("priv_"):
                    parts = conv_id.split('_')
                    other = parts[2] if parts[1] == self.username else parts[1]
                    chat = ChatWidget(conv_id, other, "private",
                                    self.network, self.local_db, self.username,
                                    raw_target=other)
                    self.add_chat_tab(conv_id, other, chat, is_group=False)

            # Response to leave_group (optional, you can add handling)
            elif 'deleted' in msg:
                group_id = msg['group_id']
                conv_id = f"group_{group_id}"
                if conv_id in self.chat_widgets:
                    widget = self.chat_widgets[conv_id]
                    for i in range(self.group_tabs.count()):
                        if self.group_tabs.widget(i) == widget:
                            self.group_tabs.removeTab(i)
                            break
                    del self.chat_widgets[conv_id]
                if msg['deleted']:
                    QMessageBox.information(self, "Group Deleted", "The group has been deleted because you were the last member.")
                else:
                    QMessageBox.information(self, "Left Group", "You have left the group.")

            # Ignore other OK responses (like create_group, send_message)
            else:
                pass   # no error popup

        else:
            # Error response
            QMessageBox.warning(self, "Error", msg.get('message', 'Unknown error'))
    
    def logout(self):
        self.network.send_command("logout", {})
        self.network.stop()
        self.close()
        # Show login dialog again
        self.login_dialog = LoginDialog()
        self.login_dialog.show()
        
    def on_disconnected(self):
        QMessageBox.information(self, "Disconnected", "You have been logged out.")
        self.close()
        # Reopen login
        self.login_dialog = LoginDialog()
        self.login_dialog.show()
        
    def delete_account(self):
        reply = QMessageBox.question(self, "Delete Account",
                                    "Are you sure? This will permanently delete your account and all data.",
                                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            self.network.send_command("delete_account", {})
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
                full_username = resp.get('full_username')
                self.username_edit.setText(full_username)
                QMessageBox.information(self, "Success", f"Registration successful. Your full username is:\n{full_username}\n\nUse this to log in.")
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