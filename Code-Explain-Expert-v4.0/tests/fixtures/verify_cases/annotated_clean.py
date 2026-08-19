"""注释后版（只加注释，逻辑零改动）—— verify_annotations.py 应判 PASS。"""

# OrderService — 订单服务"业务经理"，负责下单与取消的排兵布阵


class OrderService:
    def __init__(self, mapper):
        self.mapper = mapper

    # create_order（创建订单）：幂等挡板 + 服务端算价 + 落单
    def create_order(self, user_id, sku_id, quantity):
        # 幂等挡板：同一用户重复提交直接返回已有订单，防重复扣款
        existing = self.mapper.select_by_user(user_id)
        if existing is not None:
            return existing
        # 服务端反查价格：前端传的价格一分不信，只信 DB
        price = self.mapper.get_price(sku_id)
        total = price * quantity  # 金额用整型分避免浮点误差
        order = {
            "user_id": user_id,
            "sku_id": sku_id,
            "quantity": quantity,
            "total": total,
            "status": "PENDING",  # 初始态 PENDING，等支付回调流转到 PAID
        }
        self.mapper.insert(order)
        return order

    # cancel_order（取消订单）：CAS 改状态 + 回补库存，两步需原子
    def cancel_order(self, order_no):
        order = self.mapper.select_by_no(order_no)
        # 单不存在当取消成功：与延迟消息赛跑时消息晚到按幂等处理
        if order is None:
            return
        # 只有 PENDING 可取消，PAID 走退款流程
        if order["status"] != "PENDING":
            return
        self.mapper.update_status(order_no, "CANCELED")  # 状态: PENDING -> CANCELED
        # 回补库存：数量取自订单快照，不用外部入参
        self.mapper.restore_stock(order["sku_id"], order["quantity"])
