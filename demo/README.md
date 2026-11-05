# Demo cell

The cell in `demo/nodes.py` is a small inspection line plus two topics that are disconnected on purpose.

- `/depth/points/compressed` has one subscriber and no publisher. Health is `dead_input`.
- `/board/temperature` has one publisher and no subscriber. Health is `dead_output`.

`/inspection/verdict` is also published with no subscriber. The cell leaves it that way.

## Start the container

The image is `ros:jazzy-ros-base`. The repo is mounted at `/work`. Host port 2222 forwards to sshd in the container.

```bash
docker run -d --name ros2demo \
  -p 2222:22 \
  -v "$PWD":/work \
  ros:jazzy-ros-base \
  bash -lc 'apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq openssh-server >/dev/null && mkdir -p /run/sshd && /usr/sbin/sshd -D'
```

## Log in

sshd starts with no password and none of your keys. Before you connect, add your own SSH public key to the container's `authorized_keys` for the root account. Inside the container that file is `/root/.ssh/authorized_keys`. The directory is mode 700 and the file is mode 600. The standard tool for copying a key is ssh-copy-id. It cannot place the first key here, because it needs a password or a key that is already trusted, and this container has neither. You own the container, so `docker exec` into `ros2demo` as root can create the directory and the file. This runbook does not contain a key, and it does not write one.

Add this block to `~/.ssh/config`. The plugin uses the name `ros2demo`.

```
Host ros2demo
  HostName 127.0.0.1
  Port 2222
  User root
```

`ssh -o BatchMode=yes ros2demo` has to succeed with no prompt.

## Start the cell

Start the cell in a second process.

```bash
docker exec -d ros2demo bash -lc '. /opt/ros/jazzy/setup.bash && python3 /work/demo/nodes.py all >/tmp/nodes.log 2>&1'
```

The live container on this machine uses that image, that port, and that mount. Its cell process is `python3 /work/demo/nodes.py all`.
