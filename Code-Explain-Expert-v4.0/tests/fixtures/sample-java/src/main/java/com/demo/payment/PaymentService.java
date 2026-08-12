package com.demo.payment;

import com.demo.order.OrderService;
import com.demo.payment.dto.PaymentRequest;
import com.demo.payment.dto.PaymentResult;
import com.demo.gateway.PaypalGateway;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

/**
 * 支付核心服务：负责订单支付全流程编排。
 */
@Service
public class PaymentService {

    private final PaymentMapper paymentMapper;
    private final PaypalGateway paypalGateway;
    private final OrderService orderService;

    public PaymentService(PaymentMapper paymentMapper, PaypalGateway paypalGateway, OrderService orderService) {
        this.paymentMapper = paymentMapper;
        this.paypalGateway = paypalGateway;
        this.orderService = orderService;
    }

    /**
     * 发起支付：先锁定订单，再调网关，最后落库。
     */
    @Transactional
    public PaymentResult pay(PaymentRequest request) {
        // 幂等校验
        if (paymentMapper.findByOrderNo(request.getOrderNo()) != null) {
            return PaymentResult.alreadyPaid();
        }
        orderService.lockOrder(request.getOrderNo());
        PaymentResult result = paypalGateway.charge(request);
        paymentMapper.insert(request, result);
        return result;
    }

    public PaymentResult refund(String orderNo, double amount) {
        return paypalGateway.refund(orderNo, amount);
    }
}
// 工作区新改
