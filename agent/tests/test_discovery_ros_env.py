"""discover_topics reports the agent's effective ROS environment, so the hub
UI can explain an all-topics-have-no-publishers scan (operator terminals on a
different ROS_DOMAIN_ID / RMW than the agent's systemd defaults).

These run without ROS: rclpy is absent, so discovery takes the no-node path,
and the rmw falls back to the env var.
"""

from missiondebug_agent.discovery import discover_topics, ros_env


def test_ros_env_defaults_without_ros(monkeypatch):
    monkeypatch.delenv("ROS_DOMAIN_ID", raising=False)
    monkeypatch.delenv("RMW_IMPLEMENTATION", raising=False)
    monkeypatch.delenv("ROS_DISTRO", raising=False)

    env = ros_env()
    assert env["domain_id"] == "0"  # unset means domain 0
    assert env["rmw"] is None
    assert env["distro"] is None


def test_ros_env_reflects_environment(monkeypatch):
    monkeypatch.setenv("ROS_DOMAIN_ID", "42")
    monkeypatch.setenv("RMW_IMPLEMENTATION", "rmw_cyclonedds_cpp")
    monkeypatch.setenv("ROS_DISTRO", "humble")

    env = ros_env()
    assert env["domain_id"] == "42"
    assert env["rmw"] == "rmw_cyclonedds_cpp"
    assert env["distro"] == "humble"


def test_empty_env_values_treated_as_unset(monkeypatch):
    monkeypatch.setenv("ROS_DOMAIN_ID", "")
    monkeypatch.setenv("RMW_IMPLEMENTATION", "")

    env = ros_env()
    assert env["domain_id"] == "0"
    assert env["rmw"] is None


def test_discover_topics_includes_ros_env(monkeypatch):
    monkeypatch.setenv("ROS_DOMAIN_ID", "7")
    result = discover_topics()
    assert result["ros_env"]["domain_id"] == "7"
    # No rclpy on this machine: the no-node path still reports env + settled.
    assert "settled" in result and "topics" in result
