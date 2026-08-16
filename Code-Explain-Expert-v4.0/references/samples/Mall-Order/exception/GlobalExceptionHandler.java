package com.mall.order.exception;

import com.mall.order.common.BusinessException;
import com.mall.order.common.R;
import lombok.extern.slf4j.Slf4j;
import org.springframework.web.bind.MethodArgumentNotValidException;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;

// ====================
// GlobalExceptionHandler — 全局的"售后服务台"：所有没人接住的异常最后都流到这里，
// 换上一身体面的用户可读提示再出门。解决两个核心问题：
//  1.绝不让 Java 堆栈见用户——用户看不懂，还会把表名、类名、SQL 结构泄露给外部；
//  2.绝不让前端收到"玄学 500"——每种异常都有明确的 code 与一句话说明。
// Spring 的派单机制：@RestControllerAdvice + @ExceptionHandler 组成一张"异常 → 处理方法"路由表，
// 任何 Controller / Service 抛出的异常自动被路由到最匹配的处理方法——没人写 try-catch 也井井有条
// ====================
@Slf4j
@RestControllerAdvice
public class GlobalExceptionHandler {

    // handleBusiness（业务异常）：业务规则的正常否决（库存不足、状态不对、不是你的单），不是 bug。
    // 日志只打 warn 不打 error 是有讲究的：error 会触发夜间告警电话，业务否决也用 error，
    // 运维一晚上要接 400 个电话，真正的 bug 反而被淹没
    @ExceptionHandler(BusinessException.class)
    public R<Void> handleBusiness(BusinessException e) {
        log.warn("业务否决: {}", e.getMessage());
        return R.fail(e.getCode(), e.getMessage());
    }

    /**
     * Translate bean validation failures to HTTP 400.
     */
    // handleValidation（参数校验失败）：@Valid 拦下的问题（quantity=0、缺幂等号）在这里统一翻译。
    // 只取第一条错误而非全部拼接：前端一次只弹一个提示框，拼十条用户也只看最后一条
    @ExceptionHandler(MethodArgumentNotValidException.class)
    public R<Void> handleValidation(MethodArgumentNotValidException e) {
        String firstError = e.getBindingResult().getFieldErrors().get(0).getDefaultMessage();
        return R.fail(400, firstError);
    }

    // handleUnexpected（未预期异常）：最后的兜底，走到这里的都是潜在 bug。
    // 两个动作一个不能省：①完整堆栈进日志（用户看不懂，开发离不开）；②给用户一句"系统繁忙"
    // （开发要细节，用户要体面，各取所需）。返回文案刻意不含任何内部信息
    @ExceptionHandler(Exception.class)
    public R<Void> handleUnexpected(Exception e) {
        log.error("未预期异常", e);
        return R.fail(500, "系统繁忙，请稍后重试");
    }
}
