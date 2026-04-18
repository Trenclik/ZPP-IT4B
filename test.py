import redis
from pymongo import MongoClient

# Test Valkey
r = redis.Redis(host='localhost', port=6379)
print("Valkey ping:", r.ping())

# Test MongoDB
client = MongoClient('mongodb://localhost:27017/')
print("MongoDB server info:", client.server_info())