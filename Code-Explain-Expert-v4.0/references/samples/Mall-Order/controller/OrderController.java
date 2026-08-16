package com.mall.order.controller;

import com.mall.order.common.PageVO;
import com.mall.order.common.R;
import com.mall.order.dto.OrderCreateDTO;
import com.mall.order.dto.PayCallbackDTO;
import com.mall.order.enums.OrderStatus;
import com.mall.order.service.OrderService;
import com.mall.order.vo.OrderVO;
import jakarta.validation.Valid;
import org.springframework.web.bind.annotation.*;

// ====================
// OrderController — 订单中心的"前台总服务台"：所有 HTTP 请求从这里进门。
// 只做三件事：收单（@Valid 校验参数）、转交（甩给业务经理 OrderService）、开回执（包成统一响应 R）。
// 前台自己绝不干活——不碰数据库、不写一行业务 if/else，这是本项目的分层铁律：
// 前台一旦开始"帮忙干活"，业务逻辑就会散落各处，改需求时谁也找不全。
// 顺着本类的 URL 从上往下读，就是用户视角的完整购物流：下单 → 查单 → 取消 → 支付回调（网关调的）
// ====================
@RestController
@RequestMapping("/api/orders")
public class OrderController {

    // 经理由 Spring 的"人事部门"（IoC 容器）在启动时雇佣并派工（依赖注入），这里只声明"我需要一位"
    private final OrderService orderService;

    // 构造器注入是团队规范：字段注入 @Autowired 写不了 final，也没法在单测里脱离容器 new 出来测
    public OrderController(OrderService orderService) {
        this.orderService = orderService;
    }

    // createOrder（创建订单）：用户在结算页点「提交订单」的入口，一个订单的一生从这里开始。
    // 幂等的钥匙是 dto.outRequestNo：网络超时后前端自动重试不会变成重复下单（挡板见 OrderServiceImpl 第①步）
    // 响应体长这样，前端靠 code==0 决定跳支付页还是弹错误提示：
    // { "code": 0, "msg": "ok", "data": { "orderNo": "20260816104230773312", "status": "PENDING", "payExpireAt": "2026-08-16 10:57:30" } }
    @PostMapping
    public R<OrderVO> createOrder(@Valid @RequestBody OrderCreateDTO dto,
                                  @RequestHeader("X-User-Id") Long userId) {
        // userId 只从网关签名背书的请求头取，绝不收前端 body 里的：body 是用户可改的，请求头不是
        return R.ok(orderService.createOrder(dto, userId));
    }

    // payCallback（支付回调）：不是前端调的，是支付网关的服务器机器调的——"钱到账了"的电话。
    // 网关承诺 at-least-once 送达（同一次支付可能打来 5 次），幂等责任在 Service 侧，这里只做透传
    @PostMapping("/{orderNo}/pay-callback")
    public R<Void> payCallback(@PathVariable String orderNo,
                               @RequestBody PayCallbackDTO callback) {
        orderService.handlePayCallback(orderNo, callback);
        // 网关其实只看 HTTP 200 与否决定要不要重试，body 内容它不关心；包成 R 是团队统一出口的习惯
        return R.ok();
    }

    /**
     * Cancel an order that has not been paid yet.
     */
    // cancel（取消订单）：用户在「待支付」订单卡片上点「取消」的入口。
    // 只放行 PENDING 态：已支付要走退款流程（另一个域的事），这里直接拒绝并返回业务提示
    @PostMapping("/{orderNo}/cancel")
    public R<Void> cancel(@PathVariable String orderNo,
                          @RequestHeader("X-User-Id") Long userId) {
        orderService.cancelOrder(orderNo, userId, "USER_CANCEL");
        return R.ok();
    }

    // detail（订单详情）：给前端「订单详情页」用，「我的订单」列表点进来也走这里
    @GetMapping("/{orderNo}")
    public R<OrderVO> detail(@PathVariable String orderNo,
                             @RequestHeader("X-User-Id") Long userId) {
        return R.ok(orderService.getOrderDetail(orderNo, userId));
    }

    // listUserOrders（我的订单分页）：给前端「我的订单」四个页签用（全部/待支付/待收货/已完成）。
    // 参数校验刻意放行宽松值（page=0、size=999），由 Service 统一夹紧——
    // 拦在门口（@Min/@Max）当然也行，但"列表参数规范化"的规则只写一处，避免门口和屋里各拦一半
    @GetMapping
    public R<PageVO<OrderVO>> listUserOrders(
            @RequestParam(required = false) OrderStatus status,  // 页签筛选，null = 全部
            @RequestParam(defaultValue = "1") int page,           // 页码从 1 起，0/负数由 Service 夹成 1
            @RequestParam(defaultValue = "10") int size,          // 上限 50 由 Service 夹紧，防前端一次拉全表
            @RequestHeader("X-User-Id") Long userId) {            // 只能看自己的单，越权在 Service 拦截
        return R.ok(orderService.listUserOrders(userId, status, page, size));
    }
}
