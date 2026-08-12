package com.demo.payment;

import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/api/pay")
public class PaymentController {

    private final PaymentService paymentService;

    public PaymentController(PaymentService paymentService) {
        this.paymentService = paymentService;
    }

    @PostMapping("/create")
    public PaymentResult create(@RequestBody PaymentRequest request) {
        return paymentService.pay(request);
    }
}
变更
