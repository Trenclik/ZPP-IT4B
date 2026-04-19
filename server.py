import socket
import threading
import time
import json
import uuid
import bcrypt
import random
import redis
import re
from pymongo import MongoClient

# ---------- Constants ----------
HOST = '127.0.0.1'
PORT = 5555

# ---------- Database Setup ----------
redis_client = redis.Redis(host='localhost', port=6379, decode_responses=True)
mongo_client = MongoClient('mongodb://localhost:27017/')
db = mongo_client.chatdb
messages_col = db.messages
# Create index for fast history retrieval and sharding (shard key = conversation_id)
messages_col.create_index([('conversation_id', 1), ('timestamp', 1)])

# ---------- Helper Functions ----------
def hash_password(pw: str) -> str:
    return bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode()

def check_password(pw: str, hashed: str) -> bool:
    return bcrypt.checkpw(pw.encode(), hashed.encode())

def generate_unique_tag(base_username):
    # Generate random 4-digit tag, ensure not already used
    for _ in range(10):  # try up to 10 times
        tag = f"#{random.randint(0, 9999):04d}"
        full = f"{base_username}{tag}"
        if not redis_client.exists(f"user:{full}"):
            return full
    # fallback to a timestamp-based tag
    return f"{base_username}#{int(time.time()) % 10000:04d}"
# ---------- Server Core ----------
class ChatServer:
    def __init__(self):
        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.active_users = {}          # user_id -> client_handler
        self.lock = threading.Lock()

    def start(self):
        self.server_socket.bind((HOST, PORT))
        self.server_socket.listen()
        print(f"Server listening on {HOST}:{PORT}")
        while True:
            client_sock, addr = self.server_socket.accept()
            print(f"New connection from {addr}")
            handler = ClientHandler(client_sock, self)
            threading.Thread(target=handler.run, daemon=True).start()

class ClientHandler:
    def __init__(self, sock, server):
        self.sock = sock
        self.server = server
        self.user_id = None
        self.username = None

    def send(self, data):
        self.sock.send((json.dumps(data) + "\n").encode())

    def run(self):
        try:
            while True:
                data = self.sock.recv(4096).decode()
                if not data:
                    break
                for line in data.strip().split('\n'):
                    if line:
                        self.handle_command(json.loads(line))
        except (ConnectionResetError, BrokenPipeError):
            pass
        finally:
            self.logout()
            self.sock.close()

    def handle_command(self, cmd):
        method = getattr(self, f"do_{cmd['command']}", None)
        if method:
            method(cmd['data'])
        else:
            self.send({"status": "error", "message": "Unknown command"})

    # ---------- Account Commands ----------
    def do_register(self, data):
        base_username = data['username']
        # Check if base name is taken? Not necessary; we allow duplicates with different tags.
        full_username = generate_unique_tag(base_username)
        user_id = str(uuid.uuid4())
        redis_client.hset(f"user:{full_username}", mapping={
            "user_id": user_id,
            "password": hash_password(data['password']),
            "base_username": base_username   # store base for display
        })
        self.send({"status": "ok", "user_id": user_id, "full_username": full_username})

    def do_login(self, data):
        username = data['username']
        password = data['password']
        user_data = redis_client.hgetall(f"user:{username}")
        if not user_data or not check_password(password, user_data['password']):
            self.send({"status": "error", "message": "Invalid credentials"})
            return
        user_id = user_data['user_id']
        with self.server.lock:
            if user_id in self.server.active_users:
                self.send({"status": "error", "message": "Already logged in elsewhere"})
                return
            self.user_id = user_id
            self.username = username
            self.server.active_users[user_id] = self
        self.send({"status": "ok", "user_id": user_id, "username": username})

    def logout(self):
        if self.user_id:
            with self.server.lock:
                self.server.active_users.pop(self.user_id, None)
    def do_logout(self, data):
        self.logout()
        self.send({"status": "ok"})

    def do_delete_account(self, data):
        username = self.username
        user_id = self.user_id

        # --- NEW: Update all messages sent by this user to include sender_id ---
        messages_col.update_many(
            {"sender": username, "sender_id": {"$exists": False}},
            {"$set": {"sender_id": user_id}}
        )
        # Also optionally rename sender → sender_username for consistency
        messages_col.update_many(
            {"sender": username},
            {"$rename": {"sender": "sender_username"}}
        )
        # Get all groups the user is in
        group_ids = redis_client.smembers(f"user_groups:{username}")
        for group_id in group_ids:
            # Remove user from group members
            redis_client.srem(f"group_members:{group_id}", username)
            # Notify remaining online members (excluding the deleted user)
            remaining_members = redis_client.smembers(f"group_members:{group_id}")
            group_info = redis_client.hgetall(f"group:{group_id}")
            group_name = group_info.get('name', '')
            for member in remaining_members:
                user_data = redis_client.hgetall(f"user:{member}")
                if user_data:
                    member_id = user_data['user_id']
                    with self.server.lock:
                        member_handler = self.server.active_users.get(member_id)
                    if member_handler:
                        member_handler.send({
                            "command": "member_left",
                            "data": {
                                "group_id": group_id,
                                "group_name": group_name,
                                "username": username
                            }
                        })
            # Remove group from user's own list
            redis_client.srem(f"user_groups:{username}", group_id)
            # If group becomes empty, delete it
            if redis_client.scard(f"group_members:{group_id}") == 0:
                redis_client.delete(f"group:{group_id}")
                redis_client.delete(f"group_members:{group_id}")
        # Delete the user account
        redis_client.delete(f"user:{username}")
        # Send response, then disconnect
        self.send({"status": "ok"})
        self.logout()
        self.sock.close()

    # ---------- Group Commands ----------
    def do_create_group(self, data):
        group_name = data['name']
        members = set(data.get('members', [])) | {self.username}
        # Validate all members exist
        for member in members:
            if not redis_client.exists(f"user:{member}"):
                self.send({"status": "error", "message": f"User {member} does not exist"})
                return
        members = set(data.get('members', [])) | {self.username}
        group_id = str(uuid.uuid4())
        # Store group metadata
        redis_client.hset(f"group:{group_id}", mapping={
            "name": group_name,
            "creator": self.username
        })
        # Add members
        for member in members:
            redis_client.sadd(f"group_members:{group_id}", member)
            redis_client.sadd(f"user_groups:{member}", group_id)
        # Notify all members (including creator) about the new group
        for member in members:
            user_data = redis_client.hgetall(f"user:{member}")
            if user_data:
                member_id = user_data['user_id']
                with self.server.lock:
                    member_handler = self.server.active_users.get(member_id)
                if member_handler:
                    member_handler.send({
                        "command": "new_group",
                        "data": {
                            "group_id": group_id,
                            "name": group_name
                        }
                    })
        self.send({"status": "ok", "group_id": group_id, "group_name": group_name})

    def do_add_member(self, data):
        group_id = data['group_id']
        new_member = data['username']
        # Check if group exists
        if not redis_client.exists(f"group:{group_id}"):
            self.send({"status": "error", "message": "Group not found"})
            return
        redis_client.sadd(f"group_members:{group_id}", new_member)
        redis_client.sadd(f"user_groups:{new_member}", group_id)
        self.send({"status": "ok"})
        
    def do_check_user_exists(self, data):
        username = data['username']
        exists = redis_client.exists(f"user:{username}")
        self.send({"status": "ok", "exists": exists})

    # ---------- Messaging Commands ----------
    def do_send_message(self, data):
        msg_type = data['type']
        target = data['target']
        content = data['content']
        timestamp = data.get('timestamp', None)
        sender_id = self.user_id
        sender_username = self.username
        if msg_type == "private":
            conv_id = f"priv_{'_'.join(sorted([sender, target]))}"
            msg_doc = {
                "conversation_id": conv_id,
                "sender_id": sender_id,
                "sender_username": sender_username,
                "content": content,
                "timestamp": timestamp,
                "type": "private"
            }
            result = messages_col.insert_one(msg_doc)
            msg_id = str(result.inserted_id)          # <-- define msg_id first
            
            # Send acknowledgment to sender
            self.send({
                "command": "message_ack",
                "data": {
                    "message_id": msg_id,
                    "conversation_id": conv_id,
                    "status": "delivered"
                }
            })
            
            # Forward to recipient if online
            recipient_user_data = redis_client.hgetall(f"user:{target}")
            if recipient_user_data:
                recipient_id = recipient_user_data['user_id']
                with self.server.lock:
                    recipient_handler = self.server.active_users.get(recipient_id)
                if recipient_handler:
                    recipient_handler.send({
                        "command": "new_message",
                        "data": {
                            "message_id": msg_id,
                            "type": "private",
                            "from_id": sender_id,
                            "from_username": sender_username,
                            "content": content,
                            "timestamp": timestamp,
                            "conversation_id": conv_id
                        }
                    })
                    
        else:  # group
            conv_id = f"group_{target}"
            msg_doc = {
                "conversation_id": conv_id,
                "sender": sender,
                "content": content,
                "timestamp": timestamp,
                "type": "group"
            }
            result = messages_col.insert_one(msg_doc)
            msg_id = str(result.inserted_id)          # <-- define msg_id first
            
            # Send acknowledgment to sender
            self.send({
                "command": "message_ack",
                "data": {
                    "message_id": msg_id,
                    "conversation_id": conv_id,
                    "status": "delivered"
                }
            })
            
            # Forward to all online group members
            members = redis_client.smembers(f"group_members:{target}")
            group_info = redis_client.hgetall(f"group:{target}")
            group_name = group_info.get('name', target)
            for member in members:
                if member == sender:
                    continue
                user_data = redis_client.hgetall(f"user:{member}")
                if user_data:
                    member_id = user_data['user_id']
                    with self.server.lock:
                        member_handler = self.server.active_users.get(member_id)
                    if member_handler:
                        member_handler.send({
                            "command": "new_message",
                            "data": {
                                "message_id": msg_id,
                                "type": "group",
                                "group_id": target,
                                "group_name": group_name,
                                "from": sender,
                                "content": content,
                                "timestamp": timestamp,
                                "conversation_id": conv_id
                            }
                        })
        
        # Optional final OK (client ignores it, but harmless)
        self.send({"status": "ok"})
    def do_get_messages(self, data):
        conv_id = data['conversation_id']
        limit = data.get('limit', 50)
        before = data.get('before', None)
        query = {"conversation_id": conv_id}
        if before:
            query["timestamp"] = {"$lt": before}
        cursor = messages_col.find(query).sort("timestamp", -1).limit(limit)
        messages = []
        for doc in cursor:
            messages.append({
                "message_id": str(doc["_id"]),
                "sender_id": doc.get("sender_id"),
                "sender_username": doc.get("sender_username", doc.get("sender")),
                "content": doc["content"],
                "timestamp": doc["timestamp"],
                "type": doc["type"]
            })
        self.send({"status": "ok", "conversation_id": conv_id, "messages": list(reversed(messages))})

    def do_get_groups(self, data):
        groups = []
        group_ids = redis_client.smembers(f"user_groups:{self.username}")
        for gid in group_ids:
            group_info = redis_client.hgetall(f"group:{gid}")
            if group_info:
                groups.append({
                    "group_id": gid,
                    "name": group_info["name"]
                })
        self.send({"status": "ok", "groups": groups})
    
    def do_get_private_conversations(self, data):
        username = self.username
        regex = f"^priv_.*{re.escape(username)}.*"
        cursor = messages_col.aggregate([
            {"$match": {"type": "private", "conversation_id": {"$regex": regex}}},
            {"$group": {"_id": "$conversation_id"}}
        ])
        conv_ids = [doc["_id"] for doc in cursor]
        self.send({"status": "ok", "conversations": conv_ids})
        
    def do_leave_group(self, data):
        group_id = data['group_id']
        username = self.username

        # Check if user is a member
        if not redis_client.sismember(f"group_members:{group_id}", username):
            self.send({"status": "error", "message": "You are not a member of this group"})
            return

        # Remove user from group members
        redis_client.srem(f"group_members:{group_id}", username)
        redis_client.srem(f"user_groups:{username}", group_id)

        # Check if group is empty
        remaining_members = redis_client.scard(f"group_members:{group_id}")
        if remaining_members == 0:
            # Delete group metadata
            redis_client.delete(f"group:{group_id}")
            redis_client.delete(f"group_members:{group_id}")
            self.send({"status": "ok", "deleted": True, "group_id": group_id})
        else:
            # Notify other members (optional, for real-time update)
            group_info = redis_client.hgetall(f"group:{group_id}")
            group_name = group_info.get('name', '')
            members = redis_client.smembers(f"group_members:{group_id}")
            for member in members:
                user_data = redis_client.hgetall(f"user:{member}")
                if user_data:
                    member_id = user_data['user_id']
                    with self.server.lock:
                        member_handler = self.server.active_users.get(member_id)
                    if member_handler:
                        member_handler.send({
                            "command": "member_left",
                            "data": {
                                "group_id": group_id,
                                "group_name": group_name,
                                "username": username
                            }
                        })
            self.send({"status": "ok", "deleted": False, "group_id": group_id})

if __name__ == "__main__":
    server = ChatServer()
    server.start()