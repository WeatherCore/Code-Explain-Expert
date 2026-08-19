"""原版（无注释）—— verify_annotations.py 测试用例的基础版。"""


class OrderService:
    def __init__(self, mapper):
        self.mapper = mapper

    def create_order(self, user_id, sku_id, quantity):
        existing = self.mapper.select_by_user(user_id)
        if existing is not None:
            return existing
        price = self.mapper.get_price(sku_id)
        total = price * quantity
        order = {
            "user_id": user_id,
            "sku_id": sku_id,
            "quantity": quantity,
            "total": total,
            "status": "PENDING",
        }
        self.mapper.insert(order)
        return order

    def cancel_order(self, order_no):
        order = self.mapper.select_by_no(order_no)
        if order is None:
            return
        if order["status"] != "PENDING":
            return
        self.mapper.update_status(order_no, "CANCELED")
        self.mapper.restore_stock(order["sku_id"], order["quantity"])
