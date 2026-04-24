#!/bin/bash

echo "🚀 Installing project..."

# update system
sudo apt update -y
sudo apt install -y python3 python3-venv python3-pip

# create venv
python3 -m venv venv
source venv/bin/activate

# install dependencies
pip install --upgrade pip
pip install -r requirements.txt

# copy services
sudo cp services/dht.service /etc/systemd/system/
sudo cp services/watchdog.service /etc/systemd/system/

# reload systemd
sudo systemctl daemon-reload

# enable autostart
sudo systemctl enable dht.service
sudo systemctl enable watchdog.service

# start services
sudo systemctl start dht.service
sudo systemctl start watchdog.service

echo "✅ Installation complete!"
