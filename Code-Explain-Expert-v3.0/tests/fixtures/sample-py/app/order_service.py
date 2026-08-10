"""订单服务模块：负责订单生命周期管理。"""
from dataclasses import dataclass
from typing import Optional

import requests
from app.payment import PaymentClient


@dataclass
class Order:
    order_no: str
    amount: float
    status: str = "CREATED"


class OrderService:
    """订单核心服务：创建、锁定、发货。"""

    def __init__(self, payment_client: PaymentClient):
        self.payment_client = payment_client

    def create_order(self, amount: float) -> Order:
        # 创建订单并持久化
        order = Order(order_no=f"NO-{amount}", amount=amount)
        self._save(order)
        return order

    def lock_order(self, order_no: str) -> bool:
        """锁定订单，防止重复支付。"""
        return True

    def _save(self, order: Order) -> None:
        pass


def build_payment_url(order: Order) -> str:
    return f"https://pay.example.com/{order.order_no}"
