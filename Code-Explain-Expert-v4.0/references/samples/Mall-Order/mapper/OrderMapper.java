package com.mall.order.mapper;

import com.mall.order.entity.OrderDO;
import com.mall.order.enums.OrderStatus;
import org.apache.ibatis.annotations.*;

import java.util.List;

// ====================
// OrderMapper — 订单中心的"仓库管理员"：只管账本（t_order / t_stock 两张表）的存与取，从不问业务为什么。
// MyBatis 代办：接口方法 ↔ 注解里的 SQL 一一对应，方法名就是 SQL 的"人类语言别名"。
// 整个项目防超卖的秘密全部藏在 deductStock 一行 UPDATE 的 WHERE 条件里——并发裁决交给数据库行锁，
// 比 Java 层"先查后扣"（查时都有货、扣时全超卖）可靠得多
// ====================
@Mapper
public interface OrderMapper {

    /**
     * Insert one order row.
     */
    // insert（落订单）：把内存中的 OrderDO 写成 t_order 的一行，只在 createOrder 的事务内被调用
    @Insert("INSERT INTO t_order(order_no, out_request_no, user_id, sku_id, quantity, total_amount, status, pay_expire_at) "
          + "VALUES(#{orderNo}, #{outRequestNo}, #{userId}, #{skuId}, #{quantity}, #{totalAmount}, 'PENDING', #{payExpireAt})")
    int insert(OrderDO order);

    // selectByOrderNo（按单号查）：全项目最高频的读，幂等挡板、支付回调、取消、详情页都从这起步。
    // 刻意不加任何状态过滤：谁调用谁按自己的业务判状态，仓库管理员不做业务裁决
    @Select("SELECT * FROM t_order WHERE order_no = #{orderNo}")
    OrderDO selectByOrderNo(String orderNo);

    // selectByOutRequestNo（按幂等号查）：幂等挡板的专用探头，只服务 createOrder 的第一步。
    // t_order.out_request_no 建了唯一索引：挡板即使被并发绕过（两个请求同时查到"不存在"），
    // 也会在 insert 时被唯一索引拦下，最终只有一个订单能落库——挡板 + 唯一索引双保险
    @Select("SELECT * FROM t_order WHERE out_request_no = #{outRequestNo}")
    OrderDO selectByOutRequestNo(String outRequestNo);

    // casUpdateStatus（状态机 CAS 换挡）：整个状态机的"换挡机构"，PAID / CANCELED 全靠它。
    // WHERE 带 fromStatus 是精髓：支付回调与超时取消并发抢同一单时，数据库行锁保证
    // 只有一个 UPDATE 的 WHERE 仍成立，另一个影响 0 行——Java 里没写一行锁代码，锁在 SQL 里
    @Update("UPDATE t_order SET status = #{toStatus} WHERE order_no = #{orderNo} AND status = #{fromStatus}")
    int casUpdateStatus(@Param("orderNo") String orderNo,
                        @Param("fromStatus") OrderStatus fromStatus,
                        @Param("toStatus") OrderStatus toStatus);

    // deductStock（扣库存）：整个项目最关键的一行 SQL，防超卖的最后一道闸门。
    // "stock >= #{n}" 让数据库用行锁裁决并发：100 人同抢最后 1 件，99 人影响 0 行。
    // 影响 0 行 = 库存不足，由调用方（createOrder）抛业务异常并触发事务回滚
    @Update("UPDATE t_stock SET stock = stock - #{n} WHERE sku_id = #{skuId} AND stock >= #{n}")
    int deductStock(@Param("skuId") Long skuId, @Param("n") int n);

    // restoreStock（回补库存）：deductStock 的"反悔药"，取消订单的镜像动作。
    // 回补数量 n 一律取自订单行快照（当初扣了多少补多少），绝不接受外部传入——防止把库存改飞
    @Update("UPDATE t_stock SET stock = stock + #{n} WHERE sku_id = #{skuId}")
    int restoreStock(@Param("skuId") Long skuId, @Param("n") int n);

    // selectPageByUser（用户订单分页）：只服务「我的订单」页签，status 为 null 时查全部。
    // [Workaround] 动态 SQL 用 <script> 写在注解里而非 XML：本项目还没建 mapper XML 的目录约定，
    // 注解 SQL 与方法签名同屏可见更直观；若 SQL 继续膨胀应迁回 XML，届时本条移除
    @Select("<script>SELECT * FROM t_order WHERE user_id = #{userId} "
          + "<if test='status != null'>AND status = #{status}</if> "
          + "ORDER BY id DESC LIMIT #{offset}, #{size}</script>")
    List<OrderDO> selectPageByUser(@Param("userId") Long userId,
                                   @Param("status") OrderStatus status,
                                   @Param("offset") int offset,
                                   @Param("size") int size);
}
