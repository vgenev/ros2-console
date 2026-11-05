# Demo cell

The cell in `demo/nodes.py` is a small inspection line plus two topics that are disconnected on purpose.

- `/depth/points/compressed` has one subscriber and no publisher. Health is `dead_input`.
- `/board/temperature` has one publisher and no subscriber. Health is `dead_output`.

`/inspection/verdict` is also published with no subscriber. The cell leaves it that way.

## Start the container

The image is `ros:jazzy-ros-base`. The repo is mounted at `/work`. Host port 2222 forwards to sshd in the container. Bring your own SSH login. This file does not contain a key, and it does not write one.

```bash
docker run -d --name ros2demo \
  -p 2222:22 \
  -v "$PWD":/work \
  ros:jazzy-ros-base \
  bash -lc 'apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq openssh-server >/dev/null && mkdir -p /run/sshd && /usr/sbin/sshd -D'
```

Start the cell in a second process.

```bash
docker exec -d ros2demo bash -lc '. /opt/ros/jazzy/setup.bash && python3 /work/demo/nodes.py all >/tmp/nodes.log 2>&1'
```

The live container on this machine uses that image, that port, and that mount. Its cell process is `python3 /work/demo/nodes.py all`. The login setup for sshd stays outside the repo.
