# Description

## 中文版

12306 购票系统是一个面向高并发场景的分布式铁路购票学习项目，完整重构了注册登录、余票查询、抢票下单、订单管理、支付与退票业务闭环。项目提供单体聚合与微服务双架构形态，核心亮点包括 Lua 令牌桶限流、Caffeine 多级缓存、责任链校验、布隆过滤器、分库分表与数据加密、RocketMQ 延迟消息自动关单。基于 Java 17、Spring Boot 3、Spring Cloud Alibaba、RocketMQ、ShardingSphere、Redis 与 MySQL 构建，并配套十余个自研中间件 Starter。适合后端开发者与求职者系统学习高并发架构设计，也可作为微服务技术栈的实战参考。

## English

12306 Ticketing System is a high-concurrency rebuild of China Railway's booking flow — registration, seat queries, flash purchase, orders, payment and refunds — shipped as both a monolith and Spring Cloud microservices. Every hotspot has a named countermeasure: Lua token-bucket rate limiting, Caffeine multi-level caching, chain-of-responsibility validation, a Bloom filter against cache penetration, sharded databases with encrypted data, and RocketMQ delayed messages that auto-close unpaid orders. Built on Java 17, Spring Boot 3, Spring Cloud Alibaba, ShardingSphere, Redis and MySQL, with a dozen self-built middleware starters. A study reference for backend engineers and job seekers on high-concurrency architecture.
