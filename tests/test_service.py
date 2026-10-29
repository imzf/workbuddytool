"""服务状态文案：区分「进程在跑」「面板能连上」「有没有账号」。"""

from __future__ import annotations

import unittest

from wb_launcher import service


def make(state: str, reachable: bool = False, accounts: int = -1, healthy_accounts: int = -1) -> service.ServiceStatus:
    return service.ServiceStatus(
        state=state,
        reachable=reachable,
        accounts=accounts,
        healthy_accounts=healthy_accounts,
    )


class StatusLabelTest(unittest.TestCase):
    def test_running_with_accounts(self) -> None:
        self.assertEqual(make("active", True, 3, 3).label, "运行中")

    def test_running_without_accounts(self) -> None:
        self.assertEqual(make("active", True, 0, 0).label, "运行中（还没有账号）")

    def test_active_but_no_answer(self) -> None:
        self.assertEqual(make("active", False).label, "运行中（端口未响应）")

    def test_starting(self) -> None:
        self.assertEqual(make("activating").label, "启动中")
        self.assertEqual(make("reloading").label, "启动中")

    def test_failed(self) -> None:
        self.assertEqual(make("failed").label, "启动失败")

    def test_stopped(self) -> None:
        self.assertEqual(make("inactive").label, "已停止")
        self.assertEqual(make("unknown").label, "已停止")

    def test_running_property(self) -> None:
        self.assertTrue(make("active").running)
        self.assertFalse(make("activating").running)

    def test_healthy_property(self) -> None:
        self.assertTrue(make("active", True, 1, 1).healthy)
        self.assertFalse(make("active", True, 1, 0).healthy)
        self.assertFalse(make("active", False).healthy)


class HealthUrlTest(unittest.TestCase):
    def test_ipv4(self) -> None:
        self.assertEqual(service.health_url("127.0.0.1", 7863), "http://127.0.0.1:7863/healthz")

    def test_wildcard_maps_to_loopback(self) -> None:
        self.assertEqual(service.health_url("0.0.0.0", 80), "http://127.0.0.1:80/healthz")
        self.assertEqual(service.health_url("::", 80), "http://127.0.0.1:80/healthz")

    def test_ipv6_brackets(self) -> None:
        self.assertEqual(service.health_url("::1", 7863), "http://[::1]:7863/healthz")


if __name__ == "__main__":
    unittest.main()
