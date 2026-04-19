#! /bin/sh

sudo rm -f /tmp/mongodb-27017.sock
systemctl start valkey.service
sudo mongod --config /etc/mongodb.conf & disown
./bin/python server.py 
