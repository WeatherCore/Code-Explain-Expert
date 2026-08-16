package com.mall.order.service;

import com.mall.order.common.PageVO;
import com.mall.order.dto.OrderCreateDTO;
import com.mall.order.dto.PayCallbackDTO;
import com.mall.order.enums.OrderStatus;
import com.mall.order.vo.OrderVO;

// ====================
// OrderService — 订单经理的"岗位说明书"（接口）。
// 注释分工的团队约定：接口只写对外契约——做什么、谁来调、失败时对调用方的承诺；
// 业务怎么做、为什么这么做（事务边界、锁、状态机细节）全部收在实现类 OrderServiceImpl。
// 为什么不许两边都写：同一件事注两遍必然打架，改需求时改一处漏一处，注释比代码先烂
// ====================
public interface OrderService {

    /**
     * Place an order. Idempotent by outRequestNo.
     */
    // createOrder（创建订单）：对外承诺三件事——同一 outRequestNo 永远返回同一个订单（幂等）；
    // 价格永远以服务端 SKU 表为准；库存不足抛 BusinessException，不落任何半成品数据
    OrderVO createOrder(OrderCreateDTO dto, Long userId);

    /**
     * Handle async payment notification from the gateway.
     * The gateway delivers at-least-once.
     */
    // handlePayCallback（支付回调）：网关机器调用而非前端。承诺幂等——同一笔支付的第 1 次和
    // 第 5 次回调效果完全一致；验签失败按盗刷处理直接抛异常
    void handlePayCallback(String orderNo, PayCallbackDTO callback);

    /**
     * Cancel an unpaid order and restore stock.
     */
    // cancelOrder（取消订单）：仅 PENDING 态可取消，取消必回补库存（两步原子完成）。
    // 三个来客共用此方法：用户点取消（USER_CANCEL）、超时自动取消（TIMEOUT）、客服代取消（CS_CANCEL）
    void cancelOrder(String orderNo, Long userId, String source);

    // getOrderDetail（订单详情）：只读查询，前端详情页与客服后台共用；非本人订单抛 BusinessException
    OrderVO getOrderDetail(String orderNo, Long userId);

    // listUserOrders（我的订单分页）：只读；status=null 查全部，分页参数越界由实现统一夹紧
    PageVO<OrderVO> listUserOrders(Long userId, OrderStatus status, int page, int size);
}
