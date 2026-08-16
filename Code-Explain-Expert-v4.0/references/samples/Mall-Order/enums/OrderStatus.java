package com.mall.order.enums;

/**
 * 订单状态机：一张"订单人生阶段表"，每个枚举值是订单的一种活法（或死法）。
 * <p>
 * 状态只能沿下方箭头方向流转，任何跨阶段跳转（如 PENDING 直接变 COMPLETED）
 * 都视为脏数据，由 OrderServiceImpl 的 CAS 更新 SQL 在数据库层兜底拦截。
 * <pre>
 * PENDING ──支付成功──▶ PAID ──仓库发货──▶ SHIPPED ──确认收货──▶ COMPLETED
 *    │
 *    ├──用户取消 / 15分钟超时──▶ CANCELED（库存回补）
 *    └──支付失败 / 风控拦截────▶ CLOSED（终态，不回补优惠券资格）
 * </pre>
 */
public enum OrderStatus {

    /** 待支付：刚下单，库存已预扣，15 分钟内不付款就被延迟消息自动取消。 */
    PENDING("待支付"),

    /** 已支付：钱到账，只能从 PENDING 流转而来，等待仓库拣货发货。 */
    PAID("已支付"),

    /** 已发货：物流单号已产生，等用户确认收货或 7 天自动确认。 */
    SHIPPED("已发货"),

    /** 已完成：订单"寿终正寝"，终态，任何接口不得再变更。 */
    COMPLETED("已完成"),

    /** 已取消：用户主动取消或超时未支付，库存已回补。与 CLOSED 的业务分界见 CLOSED。 */
    CANCELED("已取消"),

    /** 已关闭：支付失败 / 风控强制关闭的终态。与 CANCELED 的业务分界——
     * CANCELED 视为"用户无悔"，优惠券资格回补；CLOSED 视为"交易有诈"，优惠券不退。 */
    CLOSED("已关闭");

    /** 给前端展示用的中文名，也用于客服后台导出报表，别删 */
    private final String label;

    OrderStatus(String label) {
        this.label = label;
    }

    public String getLabel() {
        return label;
    }

    // canTransitTo（能否流转）：状态机的"交警"，所有状态变更前必须先来问路。
    // 合法流转表写死在代码而非配置中心：流转规则就是业务法律，要改必须走代码评审，不允许热更新悄悄放行
    public boolean canTransitTo(OrderStatus target) {
        return switch (this) {
            case PENDING -> target == PAID || target == CANCELED || target == CLOSED;
            case PAID -> target == SHIPPED;
            case SHIPPED -> target == COMPLETED;
            // 三个终态哪也不许去：COMPLETED / CANCELED / CLOSED 是订单的坟墓，还能改就是脏数据
            case COMPLETED, CANCELED, CLOSED -> false;
        };
    }
}
