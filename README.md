<p align="right">
  <a href="./README_EN.md"><img alt="language English" src="https://img.shields.io/badge/language-English-9aa0a6?style=flat-square"></a>
  <a href="./README.md"><img alt="语言 简体中文" src="https://img.shields.io/badge/语言-简体中文-e05d44?style=flat-square"></a>
</p>

# nosal-murder-mystery-game（NoSQL 课程项目汇总）

【课程中相关虚拟机的部分没有展示】

本仓库用于汇总“非关系型数据库（NoSQL）”课程实验与结课项目，包含两个小案例（Redis、MongoDB）与一个结课大作业系统（MongoDB + Redis 的高并发业务系统）。

English version: `README_EN.md`

## 目录结构

- `redis-case/`：Redis 小案例（代购抢票/代金券抢购程序，秒杀场景）
- `mongodb-case/`：MongoDB 小案例（基础 CRUD / 文档操作示例）
- `scriptkill-store-nosql/`：结课作业《剧本杀店务管理系统：基于 MongoDB 与 Redis 的高并发业务系统设计与实现》

## 项目简介

### 1）Redis 案例（代购抢票/代金券抢购）

目录：`redis-case/seckill/`

要点（面向课程知识点）：
- Redis 缓存与高并发读写
- Lua 脚本原子扣减库存（防超卖）
- 分布式锁/限购（防重复下单）
- JMeter 并发压测（`.jmx/.jtl`）

> 该案例原始实验环境中涉及虚拟机/局域网 Redis 等配置，本仓库仅保留代码与文档说明，虚拟机细节不在此展示。

### 2）MongoDB 案例（基础文档操作）

目录：`mongodb-case/`

要点：
- PyMongo 基础连接与 CRUD
- 文档/集合操作与简单的业务封装示例

### 3）结课作业：剧本杀店务管理系统（NoSQL 架构）

目录：`scriptkill-store-nosql/`

要点：
- MongoDB 存储业务事实（users/scripts/schedules/orders/transactions/lock_records…）
- Redis 管理运行态状态（库存 seats、锁位 lock、过期队列 locks:exp）
- Lua 脚本保证锁位/扣减原子性，TTL + 后台清理线程做过期回补与一致性收敛
- 前端 Vue3 + 后端 Flask，支持玩家/员工/老板多角色分域管理

快速入口：
- 部署与运行：`scriptkill-store-nosql/README.md`
- 技术文档：`scriptkill-store-nosql/docs/mongodb_tech_doc.md`、`scriptkill-store-nosql/docs/redis_tech_doc.md`
- 报告材料（表格/图表）：`scriptkill-store-nosql/docs/`

## 版权与使用说明

本仓库用于课程学习与实验归档，包含的测试结果、截图与文档用于课程汇报与复现验证。
