package com.mall.order.dto;

import jakarta.validation.constraints.Max;
import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import lombok.Data;

import java.math.BigDecimal;

// ====================
// OrderCreateDTO — 前端递进来的"点菜单"：只装用户说了什么，绝不装系统算出什么。
// 与数据库实体 OrderDO 严格分家，DTO 进门用完即弃。一旦直接拿 OrderDO 接前端，
// 以后给它加的任何内部字段（风控标记、成本价）都会自动泄露给浏览器——这是行业经典泄露事故
// ====================
@Data  // Lombok 编译期生成 getter/setter/equals/toString——本类零手写方法，字段的读写能力全是注解送的
public class OrderCreateDTO {

    // 幂等的钥匙：前端每次「点击提交」现生成一个 UUID，网络超时重试时复用同一个。
    // 没有它，一次手滑 = 两个订单两份扣款（挡板逻辑见 OrderServiceImpl 第①步）
    @NotBlank(message = "缺少幂等号，请刷新页面重试")
    private String outRequestNo;

    // 商品 SKU ID。@NotNull 只拦"没传"；传一个不存在的 ID 由 Service 反查 SKU 时兜住
    @NotNull(message = "请选择商品")
    private Long skuId;

    // 数量上下限直接交给校验注解：与其在 Service 里写 if，不如进门时统一拦截，
    // 错误信息自动被 GlobalExceptionHandler 翻译成 400 返回前端
    @NotNull(message = "请填写购买数量")
    @Min(value = 1, message = "至少购买 1 件")
    @Max(value = 99, message = "单笔最多 99 件")
    private Integer quantity;

    // 页面展示用的"参考价"：仅回显给用户看，一分钱不能参与计算。
    // 服务端真价格以 SKU 表为准（防抓包改包），见 OrderServiceImpl 第②步
    private BigDecimal displayPrice;
}
