# 报告用表格（Markdown）

## 表 6-1 数据规模统计（关键集合）

| 对象 | 统计口径 | 数量/说明 |
| --- | --- | --- |
| MongoDB | 连接状态 | 客户端不可用（请先安装依赖或在可运行环境执行脚本） |
| orders | db.orders.countDocuments({}) | （待填） |
| transactions | db.transactions.countDocuments({}) | （待填） |
| lock_records | db.lock_records.countDocuments({}) | （待填） |
| 总文档数 | 各集合 count 求和 | （待填） |

## 表 2-1 关键功能需求（GWT）

| 编号 | Given（前置条件） | When（触发事件） | Then（期望结果） |
| --- | --- | --- | --- |
| 2-1 | 用户存在且凭证有效 | 用户提交登录请求 | 返回 token 与角色信息；后续请求携带鉴权头访问受限接口 |
| 2-2 | 剧本处于上架状态 | 玩家请求剧本列表/详情 | 返回剧本信息与封面字段，列表可排序展示 |
| 2-3 | 存在未来场次且容量>0 | 玩家请求某剧本的场次列表 | 返回时间/价格/占用状态（Booked/Locked/Max），并提示玩家是否已锁位/下单 |
| 2-4 | 场次 seats>0 且玩家无有效锁 | 玩家发起锁位请求 | 原子扣减库存并生成 lock_id；锁位具备 TTL，超时释放 |
| 2-5 | 场次 seats=0 或玩家已锁位 | 玩家发起锁位请求 | 返回失败信息（已满/已锁定），库存不出现负值 |
| 2-6 | 玩家有锁位或 seats>0 | 玩家创建订单 | 写入订单（待支付）；若由锁位转单则锁位状态变更为已转订单 |
| 2-7 | 订单存在且为待支付 | 玩家支付订单 | 订单状态→已支付，并生成交易流水用于报表口径 |
| 2-8 | 员工已登录且绑定 DM | 员工查询管理端数据 | 仅返回该 DM 分域数据；越权访问被拒绝 |
| 2-9 | 老板已登录 | 老板查询统计报表并按 DM 过滤 | 返回全局或指定 DM 聚合结果，口径与交易流水一致 |
| 2-10 | 场次存在且无已支付订单 | 员工取消场次 | 场次状态变更为取消，避免破坏已完成交易 |

## 表 4-1 MongoDB 主要集合结构摘要

| 集合 | 主键/标识 | 关键字段（示例） | 设计要点 |
| --- | --- | --- | --- |
| users | _id=User_ID | Username, Phone, Password_Hash, Role, Ref_ID | 统一承载三类角色登录态；Ref_ID 绑定玩家(Player_ID)/员工(DM_ID) |
| scripts | _id=Script_ID | Title, Type, Min_Players, Max_Players, Status, Cover_Image, Group_Category, Difficulty | 剧本信息相对稳定；便于列表与详情查询 |
| schedules | _id=Schedule_ID | Script_ID, Room_ID, DM_ID, Start_Time, End_Time, Real_Price, Status, Script_Title, Room_Name, DM_Name, Max_Players | 场次列表高频读取；冗余展示字段降低拼装开销 |
| lock_records | _id=LockID | Schedule_ID, Player_ID, LockTime, ExpireTime, Status, Script_Title, Room_Name, DM_Name | 锁位历史与审计；支持转订单与过期状态 |
| orders | _id=Order_ID | Player_ID, Schedule_ID, Amount, Pay_Status, Create_Time, Script_Title, Room_Name, DM_ID, Start_Time | 订单事实；冗余字段支撑列表与报表聚合 |
| transactions | _id=Trans_ID | Order_ID, Amount, Trans_Type, Channel, Trans_Time, Result, DM_ID, Schedule_ID | 报表口径以流水为准；Result 用于过滤无效/纠正记录 |
| counters | _id=seq_name | seq, updated_at | 自增序列发生器（findOneAndUpdate 原子自增） |

## 表 4-2 Redis Key 设计摘要

| Key 模式 | 类型 | 值/成员含义 | 生命周期与用途 |
| --- | --- | --- | --- |
| seats:{schedule_id} | String | 剩余座位数（整数） | 运行态库存；可由 MongoDB 容量-订单-有效锁位推导重建 |
| lock:{schedule_id}:{player_id} | String | LockID（整数） | 玩家对场次的有效锁位；设置 TTL 自动过期删除 |
| locks:exp | Sorted Set | member=lockKey；score=到期毫秒时间戳 | 过期索引；后台扫描回补 seats 并同步 MongoDB 过期状态 |
| lock:id | String | 自增 LockID 计数器 | INCR 生成唯一 LockID；与 lock_records 主键对齐 |

## 表 6-2 Postman 功能测试用例与建议截图

| 序号 | 接口/用例 | 方法 | URL | 建议截图 |
| --- | --- | --- | --- | --- |
| 1 | Auth - Player Login (player_3001) | POST | {{baseUrl}}/api/auth/login | 是 |
| 2 | Auth - Boss Login (boss_demo) | POST | {{baseUrl}}/api/auth/login | 是 |
| 3 | Scripts - List | GET | {{baseUrl}}/api/scripts | 否 |
| 4 | Scripts - Detail | GET | {{baseUrl}}/api/scripts/{{scriptId}} | 否 |
| 5 | Schedules - By Script (with player_id) | GET | {{baseUrl}}/api/scripts/{{scriptId}}/schedules?player_id={{playerId}} | 是 |
| 6 | Lock - Create | POST | {{baseUrl}}/api/locks | 是 |
| 7 | Lock - Cancel | POST | {{baseUrl}}/api/locks/{{lockId}}/cancel | 否 |
| 8 | Order - Create | POST | {{baseUrl}}/api/orders | 是 |
| 9 | Order - Pay | POST | {{baseUrl}}/api/orders/{{orderId}}/pay | 是 |
| 10 | Order - Cancel | POST | {{baseUrl}}/api/orders/{{orderId}}/cancel | 否 |
| 11 | My - Orders | GET | {{baseUrl}}/api/my/orders | 是 |
| 12 | My - Locks | GET | {{baseUrl}}/api/my/locks | 否 |
| 13 | Admin - Rooms | GET | {{baseUrl}}/api/admin/rooms | 否 |
| 14 | Admin - Dashboard | GET | {{baseUrl}}/api/admin/dashboard | 否 |
| 15 | Admin - Reports Top Scripts | GET | {{baseUrl}}/api/admin/reports/top-scripts?limit=5 | 是 |
| 16 | Admin - DB Objects (Capability Self-check) | GET | {{baseUrl}}/api/admin/db-objects | 是 |

## 表 6-3 并发压测统计（JMeter .jtl 计算）

| 请求 | 样本数 | 成功数 | 失败数 | 错误率 | 平均响应(ms) | P95(ms) | 吞吐量(请求/秒) |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Login | 100 | 100 | 0 | 0.00% | 11.58 | 21.10 | 21.12 |
| Create Lock | 100 | 5 | 95 | 95.00% | 14.41 | 25.00 | 21.38 |
| TOTAL | 200 | 105 | 95 | 47.50% | 12.99 | 25.00 | 42.16 |
