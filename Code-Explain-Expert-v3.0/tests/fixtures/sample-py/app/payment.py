class PaymentClient:
    def charge(self, order_no: str, amount: float) -> dict:
        return {"ok": True}
