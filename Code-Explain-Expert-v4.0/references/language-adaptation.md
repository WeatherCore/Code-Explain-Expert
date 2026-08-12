# 语言适配：注释语法与规范

按骨架 JSON 中 `language` 字段选择对应章节。核心要求：**注释形式必须符合语言惯例**，内容规范见 `Explain-style-guide.md`。

## Java（含 Kotlin）

**类注释**：Javadoc 块注释，位于类声明上方：

```java
/**
 * 支付核心服务：编排订单锁定、网关扣款与流水落库。
 * 不变量：pay() 要么全部成功要么全部回滚；同一订单号只允许一次支付。
 */
@Service
public class PaymentService { ... }
```

**方法注释**：Javadoc，含 @param / @return / @throws（仅在有意义时）：

```java
/**
 * 发起支付：锁定订单 -> 调网关扣款 -> 落库流水。
 * 幂等：已存在成功流水时直接返回，不重复扣款。
 *
 * @param request 支付请求，orderNo 不可为空
 * @return 支付结果；ALREADY_PAID 表示重复请求
 * @throws GatewayTimeoutException 网关超时，订单保持锁定等待重试
 */
```

**行内注释**：`// 中文意图说明`（Spring 风格项目常用）。

**Kotlin 特例**：类/方法注释用 `/** */` 同样适用；单行用 `//`。

## Python

**类/方法注释**：docstring（三引号，`"""`），首行一句话，可多行：

```python
class OrderService:
    """订单核心服务：创建、锁定、发货。

    线程安全：无共享可变状态，方法可安全并发调用。
    """

    def create_order(self, amount: float) -> Order:
        """创建订单并持久化。

        注意：不校验余额，余额校验由支付环节负责。
        """
        ...
```

**行内注释**：`# 中文意图说明`，与代码空 2 格（PEP8 建议同一缩进级别内 `#` 前至少 2 空格）。

**约束**：不要用 `# type:` 或 `# noqa` 风格混入业务注释；参数/返回类型优先用类型注解表达，注释只讲意图。

## TypeScript / JavaScript

**类/方法注释**：JSDoc 风格（`/** */`），含 @param / @returns：

```typescript
/**
 * 将购物车条目转为结算单。
 * 已下架商品会被剔除并计入 skipped 数组。
 * @param items 购物车条目
 * @returns 结算单与剔除项
 */
function buildSettlement(items: CartItem[]): Settlement { ... }
```

**行内注释**：`// 中文意图说明`。

**特例**：
- interface/type 声明：可在关键字段上加 `/** 字段含义 */`，但不要每个字段都加。
- React 组件：函数组件注释写在组件声明上方，说明 props 契约与副作用（如"仅客户端渲染"）。

## Go

**注释**：与代码相邻的普通 `//` 行注释；导出（大写开头）的标识符按 Go 惯例必须有注释，以标识符名开头：

```go
// OrderService 处理订单的创建、锁定与发货流程。
// 并发安全：内部通过 mutex 保护状态流转。
type OrderService struct { ... }

// CreateOrder 创建订单并落库，返回订单号。
// 失败时返回 error，调用方需处理库存预占回滚。
func (s *OrderService) CreateOrder(amount float64) (string, error) { ... }
```

**注意**：Go 导出标识符注释遵循"以标识符名开头"惯例；未导出（小写）标识符注释可自由使用中文意图。

## 多语言通用规则

1. 注释语言默认中文，标识符/类型/异常名不翻译。
2. 块注释 `/** */` 适用于类与方法；单行 `//` 或 `#` 适用于行内。
3. 语言注释语法清单：
   - Java/Kotlin/JS/TS/Go/C/C++/C#/PHP/Ruby/Swift：行注释 `//`，块注释 `/** */` 或 `/* */`
   - Python：行注释 `#`，块注释 `"""docstring"""` 或 `'''docstring'''`
   - Rust：行注释 `//`，块注释 `///`（文档注释）
4. 语言特有的"文档注释"（Javadoc/JSDoc/docstring/`///`）在生成类与方法注释时优先使用，纯 `//` 只用于行内补充。
