def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "live: needs ROS2CONSOLE_LIVE=1 and a reachable ros2demo SSH host",
    )
