package com.mall.order.service.impl;

import com.mall.order.client.SkuClient;
import com.mall.order.common.BusinessException;
import com.mall.order.common.PageVO;
import com.mall.order.dto.OrderCreateDTO;
import com.mall.order.dto.PayCallbackDTO;
import com.mall.order.entity.OrderDO;
import com.mall.order.enums.OrderStatus;
import com.mall.order.mapper.OrderMapper;
import com.mall.order.mq.DelayMessageProducer;
import com.mall.order.mq.OrderTimeoutTopic;
import com.mall.order.service.OrderService;
import com.mall.order.service.RefundService;
import com.mall.order.util.OrderNoGenerator;
import com.mall.order.util.SignVerifier;
import com.mall.order.vo.OrderVO;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.math.BigDecimal;
import java.time.Duration;
import java.time.LocalDateTime;

// ====================
// OrderServiceImpl — 订单中心的"业务经理"：前台（Controller）收完单，真正的排兵布阵都在这里。
// 解决的核心问题：把"一次下单"拆成环环相扣的五步，并保证要么全做要么全不做——
//  ① 幂等挡板（网络重试不重复下单） ② 价格防篡改（只信服务端） ③ 落订单
//  ④ 扣库存（CAS 防超卖） ⑤ 发延迟消息盯 15 分钟支付超时
// 一次下单的完整流程：
//  用户提交 → 幂等检查 → 反查 SKU 拿真价格 → 生成订单号 → [事务] 落单+扣库存 → 发 15 分钟延迟消息
//  → 到点仍未支付 → 自动取消 + 回补库存
// 亮点：防超卖三道防线（CAS 扣减 SQL、取消回补、每日对账）；防重复支付两道防线（幂等挡板 + 状态机 CAS）。
// 与接口的分工：契约见 OrderService（岗位说明书），本文件只讲"怎么做、为什么这么做"
// ====================
@Service
public class OrderServiceImpl implements OrderService {

    // 支付超时 15 分钟：订单上的支付截止时间、延迟消息时长、兜底扫描周期三处共用，要改只改这里
    private static final long PAY_TIMEOUT_MINUTES = 15;

    private final OrderMapper orderMapper;
    private final SkuClient skuClient;
    private final RefundService refundService;
    private final DelayMessageProducer delayMessageProducer;
    private final SignVerifier signVerifier;

    // 五个协作者全部构造器注入（团队规范，原因见 OrderController）
    public OrderServiceImpl(OrderMapper orderMapper,
                            SkuClient skuClient,
                            RefundService refundService,
                            DelayMessageProducer delayMessageProducer,
                            SignVerifier signVerifier) {
        this.orderMapper = orderMapper;
        this.skuClient = skuClient;
        this.refundService = refundService;
        this.delayMessageProducer = delayMessageProducer;
        this.signVerifier = signVerifier;
    }

    // ─────────────────────────── 写路径：下单与支付 ───────────────────────────

    /**
     * Place an order with server-side pricing and idempotency.
     */
    // createOrder（创建订单）：经理接单。两条绝不退让的底线：
    //  ① 同一 outRequestNo 永远只产生一个订单；② 金额永远以服务端 SKU 表为准，前端传的价格一分不信
    @Override
    @Transactional(rollbackFor = Exception.class)  // [事务] 落单+扣库存原子完成：扣不动库存就整体回滚，绝不留"单在货没了"的半成品
    public OrderVO createOrder(OrderCreateDTO dto, Long userId) {

        // ── ① 幂等挡板 ──
        // outRequestNo 是前端每次「点击提交」生成的唯一号。网络超时时前端会自动重试，
        // 没有这块挡板，用户手滑点一次"提交"可能收到两个订单、被扣两份钱
        OrderDO existing = orderMapper.selectByOutRequestNo(dto.getOutRequestNo());
        if (existing != null) {
            // 命中挡板不算错误：把"上一次的结果"原样还回去，对前端来说就像第一次就成功了
            return OrderVO.from(existing);
        }

        // ── ② 价格防篡改 ──
        // dto.displayPrice 只用于页面回显，一分钱都不能参与计算——抓包改包 10 秒就能学会。
        // 服务端拿着 skuId 反查 SKU 表重算，"真价格"只此一处来源
        BigDecimal realPrice = skuClient.getSku(dto.getSkuId()).getPrice();
        // 金额一律 BigDecimal：double 的 0.1 + 0.2 = 0.30000000000000004，月底对账能把财务逼疯
        BigDecimal totalAmount = realPrice.multiply(BigDecimal.valueOf(dto.getQuantity()));

        // ── ③ 生成订单号 + 落订单 ──
        // 订单号 = 时间戳 + userId 后 4 位 + 随机位：同毫秒并发不撞号，且客服肉眼能读出下单时间
        String orderNo = OrderNoGenerator.next(userId);
        OrderDO order = OrderDO.builder()
                .orderNo(orderNo)
                .outRequestNo(dto.getOutRequestNo())
                .userId(userId)
                .skuId(dto.getSkuId())
                .quantity(dto.getQuantity())
                .totalAmount(totalAmount)
                .status(OrderStatus.PENDING)
                // 支付截止 = 当前 + 15 分钟。与延迟消息的时长必须同源（共用上面同一个常量），两边各写 15 迟早不一致
                .payExpireAt(LocalDateTime.now().plusMinutes(PAY_TIMEOUT_MINUTES))
                .build();
        orderMapper.insert(order);

        // ── ④ 扣库存（防超卖主战场，SQL 的秘密见 OrderMapper#deductStock）──
        int affected = orderMapper.deductStock(dto.getSkuId(), dto.getQuantity());
        if (affected == 0) {
            // 扣不动 = 库存不足或恰被并发抢光。抛业务异常触发整个事务回滚，上面的落单随之作废
            throw new BusinessException("库存不足，手慢了");
        }

        // ── ⑤ 超时盯梢 ──
        // [注意] 潜在缺陷：延迟消息在事务提交前投递。若消息发出后事务回滚，消息收不回来，
        // 15 分钟后消费者会对一个不存在的订单执行取消（有告警日志但污染监控）。
        // 正确姿势是 afterCommit 回调或事务消息；按红线只标注不修复，待订单组排期
        delayMessageProducer.send(OrderTimeoutTopic.CANCEL, orderNo, Duration.ofMinutes(PAY_TIMEOUT_MINUTES));

        return OrderVO.from(order);
    }

    // handlePayCallback（支付回调）：全系统最不能出错的方法——网关机器打来的"钱到账了"电话。
    // 两个天性必须在这里被驯服：at-least-once 送达（同次支付可能重复来 5 次）、
    // 与超时取消消息赛跑（用户卡在第 14 分 59 秒才付款）。驯服三件套：验签 → 幂等挡板 → 状态机 CAS
    @Override
    @Transactional(rollbackFor = Exception.class)
    public void handlePayCallback(String orderNo, PayCallbackDTO callback) {
        // 验签放第一行：后面每一步都要花 DB 查询，不能为一个可能是伪造的回调浪费一次主库 IO
        if (!signVerifier.verify(orderNo, callback)) {
            // 验签失败不是普通业务否决：按盗刷处理，记 error 级日志给风控组留追查线索，再拒绝
            throw new BusinessException("回调验签失败");
        }

        OrderDO order = orderMapper.selectByOrderNo(orderNo);
        // 订单不存在
        // [补充] 原注释只复述了代码（抛异常），补上意图：单号不存在多半是消息串台或恶意扫描，
        // 抛异常让网关收到非 200，按退避策略重试后最终进死信队列，避免对幽灵单号无限轰炸
        if (order == null) {
            throw new BusinessException("订单不存在: " + orderNo);
        }

        // 幂等闸门：已 PAID 的单再次收到回调，直接当成功。
        // 网关第 5 次重试到达时从这里静默返回——效果与第 1 次完全一致，这是对 at-least-once 的标准回应
        if (order.getStatus() == OrderStatus.PAID) {
            return;
        }

        // 状态机 CAS：UPDATE ... SET status=PAID WHERE order_no=? AND status=PENDING
        // 影响 0 行 = 超时取消抢先一步把单改成了 CANCELED——用户在最后一秒付的钱进了已取消的单。
        // 此时绝不就地"复活"订单（库存可能已回补被别人买走），走全额退款，钱不能吞
        int affected = orderMapper.casUpdateStatus(orderNo, OrderStatus.PENDING, OrderStatus.PAID);
        if (affected == 0) {
            refundService.refund(orderNo, order.getTotalAmount(), "PAID_AFTER_CANCELED");
            return;
        }
        // 状态: PENDING -> PAID；触发条件: 网关回调 SUCCESS；下一可能状态: SHIPPED（仓库发货）
    }

    /**
     * Cancel an unpaid order and restore stock.
     */
    // cancelOrder（取消订单）：订单两种"死法"中较体面的一种（另一种 CLOSED 见 OrderStatus 状态图）。
    // 三个来客按同一套动作接待：用户点取消（USER_CANCEL）/ 15 分钟延迟消息到期（TIMEOUT）/ 客服代取消（CS_CANCEL）。
    // 动作口诀：CAS 改状态 + 回补库存，两步同一事务，缺一就整体回滚
    @Override
    @Transactional(rollbackFor = Exception.class)
    public void cancelOrder(String orderNo, Long userId, String source) {
        OrderDO order = orderMapper.selectByOrderNo(orderNo);
        // 单不存在直接当取消成功：延迟消息和用户点击赛跑时，用户可能已抢先取消，
        // 消息晚到按"已完成"处理（幂等），绝不能抛异常让消息队列无限重试
        if (order == null) {
            return;
        }

        // [注意] 潜在越权：归属校验只对 USER_CANCEL 生效（TIMEOUT/CS_CANCEL 传的是系统账号）。
        // 新增来源若漏配这里的判断，就能借新入口取消别人的订单；新增来源必须同步评审此行
        if ("USER_CANCEL".equals(source) && !order.getUserId().equals(userId)) {
            throw new BusinessException("只能取消自己的订单");
        }

        // 只有 PENDING 可取消：PAID 的走退款流程（钱已收），SHIPPED 的走拒收流程（货已出），各回各家
        int affected = orderMapper.casUpdateStatus(orderNo, OrderStatus.PENDING, OrderStatus.CANCELED);
        if (affected == 0) {
            // CAS 失败 = 状态已被并发修改（多半支付回调抢先一步），取消作罢，不算错误不打扰用户
            return;
        }

        // 回补库存：扣减的镜像动作，数量取自订单行快照而非任何外部入参——当初扣了多少补多少
        orderMapper.restoreStock(order.getSkuId(), order.getQuantity());
        // 状态: PENDING -> CANCELED；触发条件: 用户取消/超时/客服代取消。终态，不可再流转

        // 待确认：CANCELED 回补优惠券资格、CLOSED 不回补的分界来自旧版需求文档 v2.3，
        // 代码里没找到优惠券回补的实际实现，需与营销组核实后补全此处意图
    }

    // ─────────────────────────── 读路径：查询 ───────────────────────────

    // getOrderDetail（订单详情）：只读，前端详情页与客服后台共用。
    // 归属校验放在 Service 而非 Controller：不止一个入口调它，校验写在离数据最近的一层才不会漏
    @Override
    @Transactional(readOnly = true)
    public OrderVO getOrderDetail(String orderNo, Long userId) {
        OrderDO order = orderMapper.selectByOrderNo(orderNo);
        if (order == null || !order.getUserId().equals(userId)) {
            // "不存在"与"不是你的单"返回同一句话：分开提示会让越权者借报错差异探测别人的单号是否存在
            throw new BusinessException("订单不存在");
        }
        return OrderVO.from(order);
    }

    // listUserOrders（我的订单分页）：只读，给「我的订单」页签。Controller 放进来的宽松分页参数
    // 在这里统一夹紧（page>=1、size 1~50）：列表类接口的防御规则收口在一处，别散在门口
    @Override
    @Transactional(readOnly = true)
    public PageVO<OrderVO> listUserOrders(Long userId, OrderStatus status, int page, int size) {
        int safePage = Math.max(page, 1);
        int safeSize = Math.min(Math.max(size, 1), 50);
        // readOnly = true 的两个便宜：连接池可把读流量路由到从库；驱动走一致性快照读，读不到别的事务中间态
        return PageVO.of(orderMapper.selectPageByUser(userId, status, (safePage - 1) * safeSize, safeSize));
    }

    // ─────────────────────────────────────────────────────────────────────────
    // 完整全局执行链路（一个订单的三种典型命运）：
    //
    // [正常流] POST /api/orders → createOrder（幂等/真价格/落单/扣库存）→ PENDING
    //            → 15 分钟内网关回调 payCallback → 验签+幂等+CAS → PAID
    //            → 仓库发货 → SHIPPED → 确认收货 → COMPLETED（寿终正寝）
    //
    // [超时流] createOrder → PENDING → 延迟消息 15 分钟到期仍未支付
    //            → cancelOrder(TIMEOUT) → CANCELED + 库存回补
    //
    // [极限流] 第 14:59 秒的支付回调与超时取消消息赛跑，CAS 只有一个赢：
    //            回调赢 → PAID 正常走；取消赢 → 回调方发现后发起 PAID_AFTER_CANCELED 全额退款，钱货两清
    // ─────────────────────────────────────────────────────────────────────────
}
