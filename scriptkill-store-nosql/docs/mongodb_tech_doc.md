# MongoDB 技术文档（剧本杀店务管理系统）

## 1. 适用范围与系统定位

MongoDB 在系统中承担**业务事实数据（durable facts）**的持久化存储职责，用于记录与追溯相对稳定或需要审计的业务对象，包括用户、玩家、员工（DM）、房间、剧本、场次、订单、交易流水与锁位历史记录等。与 Redis 的运行态状态数据相比，MongoDB 的数据具有更长生命周期、更强可追溯性以及更高的查询分析价值，并作为报表统计的主要数据来源。

在系统架构中，MongoDB 与 Redis 采用“持久化事实（MongoDB）+ 运行态状态（Redis）”的分工协同模式：库存扣减与锁位有效性在 Redis 内以 Lua 原子脚本维护，订单与流水等事实落库到 MongoDB 并通过索引与聚合管道提供查询与统计能力；两者通过过期补偿与状态同步机制实现一致性收敛。

## 2. 连接配置与数据库命名

系统的 MongoDB 连接与库名由 `nosql/config.py` 提供，支持环境变量覆盖：

- `MONGO_URI`：默认 `mongodb://localhost:27017`
- `MONGO_DB_NAME`：默认 `script_kill_store`

后端启动时在 `app.py` 的初始化逻辑中调用 MongoDB `ping` 并执行索引初始化（幂等）：

- 连接封装：`nosql/mongo.py`
- 连通性检测：`nosql/mongo.py:ping()`
- 索引初始化：`nosql/mongo.py:ensure_indexes()`

Docker 部署场景下，只要容器映射到宿主机端口（例如 `-p 27017:27017`），默认配置即可直接连接宿主机的 `localhost:27017`。

## 3. 数据建模原则与反范式策略

### 3.1 集合划分原则

集合划分以“实体边界清晰、访问模式稳定”为基本原则，核心集合包括：

- 身份与角色：`users`、`players`、`dms`
- 资源与排期：`scripts`、`rooms`、`schedules`
- 交易事实：`orders`、`transactions`
- 并发控制历史：`lock_records`
- 自增序列：`counters`

### 3.2 反范式（冗余字段）策略

系统在 `schedules`、`orders`、`lock_records` 中冗余展示型字段（例如 `Script_Title`、`Room_Name`、`DM_Name`、`Start_Time`、`Max_Players` 等），以减少运行期跨集合拼装成本并降低读取延迟。

该冗余在语义上具有合理性：订单与锁位记录属于历史事实，保留当时的快照字段有利于审计、对账与报表口径稳定；即使主数据（如剧本标题）后续变更，历史事实通常仍应展示当时的业务快照。

## 4. 集合设计与字段说明

### 4.1 `users`（统一认证与角色分域）

**用途**：统一存储 player/staff/boss 三类可登录账号，实现认证与分域授权。  
**主键**：`_id` 与 `User_ID` 对齐（整型）。  
**关键字段**：

- `Username`：用户名（唯一）
- `Phone`：手机号（唯一）
- `Password_Hash`：密码哈希（自定义盐 `$` 拼接 SHA256，兼容部分历史格式）
- `Role`：`player | staff | boss`
- `Ref_ID`：业务身份关联
  - player → `players.Player_ID`
  - staff → `dms.DM_ID`
  - boss → `NULL`
- `Create_Time`、`Last_Login`

**相关代码**：

- 注册/登录：`models/auth_model.py`
- Token 生成与校验：`models/auth_model.py:generate_token()/verify_token()`
- 后端鉴权中间件：`app.py:token_required`

### 4.2 `players`（玩家资料）

**用途**：玩家业务资料，与 `users` 的 `Ref_ID` 绑定。  
**主键**：`_id` 与 `Player_ID` 对齐（整型）。  
**关键字段**：`Nickname`、`Phone`、`Create_Time` 等。

### 4.3 `dms`（员工/主持人资料）

**用途**：员工（DM）业务资料，与 `users` 的 `Ref_ID` 绑定以实现员工分域。  
**主键**：`_id` 与 `DM_ID` 对齐（整型）。  
**关键字段**：`Name`、`Phone`、`Star_Level` 等。

### 4.4 `rooms`（房间资源）

**用途**：门店房间资源字典，供排期与统计使用。  
**主键**：`_id` 与 `Room_ID` 对齐（整型）。  
**关键字段**：`Room_Name`。

### 4.5 `scripts`（剧本资料）

**用途**：剧本信息与标签字段，支持列表、详情与热门统计。  
**主键**：`_id` 与 `Script_ID` 对齐（整型）。  
**关键字段**：

- `Title`、`Type`、`Min_Players`、`Max_Players`、`Duration`、`Base_Price`
- `Status`：上架/下架（0/1）
- `Cover_Image`、`Group_Category`、`Difficulty`、`Gender_Config` 等

**相关代码**：

- 列表/详情：`models/script_model.py`
- 热门统计（按支付订单聚合）：`models/script_model.py:get_hot_scripts()`

### 4.6 `schedules`（场次/排期）

**用途**：剧本的具体排期实例，是库存与并发控制的核心粒度。  
**主键**：`_id` 与 `Schedule_ID` 对齐（整型）。  
**关键字段**：

- 关联：`Script_ID`、`Room_ID`、`DM_ID`
- 时间：`Start_Time`、`End_Time`
- 价格：`Real_Price`
- 状态：`Status`
- 冗余快照：`Script_Title`、`Room_Name`、`DM_Name`、`Max_Players`、`Script_Cover`

**读取路径要点**：场次列表接口会同时统计订单占用与有效锁位数，并返回 `Booked_Count` 与 `Locked_Count`：

- `models/schedule_model.py:get_schedules_by_script()`
  - `orders` 聚合：`Pay_Status in (0,1)` 的订单数
  - `lock_records` 聚合：`Status=0 且 ExpireTime>now` 的锁位数

### 4.7 `lock_records`（锁位历史记录）

**用途**：对 Redis 运行态锁位的持久化镜像与审计记录，用于“我的锁位”、管理端锁位列表、转化率统计与过期状态分析。  
**主键**：`_id` 与 `LockID` 对齐（整型，来源于 Redis `lock:id` 自增）。  
**关键字段**：

- `Schedule_ID`、`Player_ID`
- `LockTime`、`ExpireTime`
- `Status`：0=锁定中，1=已转订单，2=已取消，3=已过期（与 `nosql/seat_lock_service.py` 的同步逻辑一致）
- 冗余快照：`Script_ID`、`Script_Title`、`Start_Time`、`Room_ID`、`Room_Name`、`DM_ID`、`DM_Name`

**相关代码**：

- 锁位创建与落库：`models/lock_model.py:create_lock()`
- 取消锁位：`models/lock_model.py:cancel_lock()`
- 过期同步：`nosql/seat_lock_service.py:cleanup_expired_locks()`

### 4.8 `orders`（订单事实）

**用途**：预约与支付的核心事实集合，为用户订单列表与经营统计提供主口径。  
**主键**：`_id` 与 `Order_ID` 对齐（整型，时间戳+随机数生成）。  
**关键字段**：

- `Player_ID`、`Schedule_ID`
- `Amount`、`Pay_Status`：0=待支付，1=已支付，2=已退款，3=已取消
- `Create_Time`
- 冗余快照：`Script_ID`、`Script_Title`、`Room_ID`、`Room_Name`、`DM_ID`、`DM_Name`、`Start_Time`

**一致性约束**：创建订单前会检查同一玩家同一场次是否已有有效订单（未付/已付），用于防重复预约：

- `models/order_model.py:create_order()`

### 4.9 `transactions`（交易流水）

**用途**：报表统计的核心口径集合；支付完成时插入流水记录。  
**主键**：`_id` 与 `Trans_ID` 对齐（整型，时间戳+随机数生成/或序列）。  
**关键字段**：

- `Order_ID`、`Amount`
- `Trans_Type`：1=支付（收入）
- `Channel`：1/2/3（模拟支付渠道）
- `Trans_Time`
- `Result`：1=有效流水；0=无效/纠正（迁移脚本纠正超额订单时会置 0）
- `DM_ID`、`Schedule_ID`（便于按 DM/场次过滤聚合）

**相关代码**：

- 支付落单与插入流水：`models/order_model.py:pay_order()`
- 仪表盘/报表聚合：`models/report_model.py`

### 4.10 `counters`（自增序列）

**用途**：在 MongoDB 中实现可并发安全的整型自增主键序列，用于 `User_ID/Player_ID/Schedule_ID/...` 等业务编号生成。  
**主键**：`_id` 为序列名，例如 `Player_ID`、`Schedule_ID`。  
**字段**：`seq`（当前序列值）、`updated_at`。  
**相关实现**：`nosql/mongo.py:get_next_sequence()`

其核心使用 `find_one_and_update` 的更新管道实现“缺省初始化 + 自增 + 返回自增后值”的原子过程，保证并发下不重复且无需额外锁。

## 5. 索引体系（ensure_indexes）

系统在 `nosql/mongo.py:ensure_indexes()` 中集中建立核心索引（幂等可重复调用），覆盖认证唯一性、列表查询与统计聚合的关键路径。索引命名被用于管理端“DB Objects 自检”接口展示（`app.py:/api/admin/db-objects`）。

索引示例（不完全列举）：

- `users`：
  - `uk_users_username`（Username 唯一）
  - `uk_users_phone`（Phone 唯一）
  - `idx_users_role`（Role）
- `scripts`：
  - `idx_scripts_status`（Status）
  - `idx_scripts_title`（Title）
- `schedules`：
  - `idx_sch_script_start`（Script_ID + Start_Time）
  - `idx_sch_start`（Start_Time）
  - `idx_sch_dm`（DM_ID）
  - `idx_sch_room`（Room_ID）
- `orders`：
  - `idx_orders_player_time`（Player_ID + Create_Time）
  - `idx_orders_player_schedule_status`（Player_ID + Schedule_ID + Pay_Status）
- `lock_records`：
  - `idx_locks_expire`（ExpireTime）
  - `idx_locks_schedule`（Schedule_ID）
  - `idx_locks_player_time`（Player_ID + LockTime）

## 6. 聚合统计与典型管道

报表统计集中于 `models/report_model.py`，主要依赖 `$match/$group/$sort/$limit` 等算子，避免将大规模数据回传应用层计算。

### 6.1 仪表盘统计（收入/订单数/上座率）

`ReportModel.get_dashboard_stats(dm_id)` 的核心口径：

- 收入与订单数：对 `transactions` 在 `Result=1 且 Trans_Type=1` 口径下按时间区间聚合求和与计数；
- 活跃锁位数：`lock_records` 中 `Status=0 且 ExpireTime>now` 的计数；
- 上座率：未来 7 天场次容量总和与占用（订单+有效锁位）计数计算百分比。

### 6.2 热门剧本 TopN

`ReportModel.get_top_scripts()` 或 `ScriptModel.get_hot_scripts()` 的典型管道：

- 在 `orders` 上以 `Pay_Status=1` 过滤；
- 按 `Script_ID` 分组统计订单数与营收（`$sum`）；
- 按订单数/金额排序并限制 TopN。

### 6.3 房间利用率与转化率

- 房间利用率：按房间维度聚合场次与支付订单指标；
- 锁位转化率：对 `lock_records` 统计 `Status=1` 占比，并与订单支付转化口径组合输出。

## 7. 与 Redis 的一致性协同

MongoDB 记录业务事实，Redis 维护库存与锁位有效性。系统在以下关键点实现一致性收敛：

- 锁位创建：Redis 原子成功后写入 `lock_records` 历史记录；
- 锁位转订单：订单写入后删除 Redis 锁位键，同时将 MongoDB `lock_records.Status` 更新为已转订单（1）；
- 锁位过期：Redis TTL 删除锁位键后，由后台清理线程（`app.py`）调用 `cleanup_expired_locks()` 回补 `seats`，并将 MongoDB 中对应 `lock_records` 标记为过期（3）。

该策略在工程上对应“强原子点（Redis）+ 最终一致收敛（MongoDB）”的实现模式。

## 8. 数据准备与可复现脚本

系统提供脚本用于迁移与造数，便于构建可复现的数据集：

- MySQL → MongoDB 迁移：`tools/migrate_mysql_to_mongo.py`
  - 写入集合并重建 `counters`
  - 初始化 Redis `seats:*` 与 `lock:id`
  - 纠正可能存在的超额订单并将流水 `Result` 置为 0（避免报表口径污染）
- NoSQL 造数补齐：`tools/seed_nosql_data.py`
  - 补齐房间/剧本/场次/玩家与订单流水
  - 造数完成后重建 `seats:*` 使库存与事实一致
- 一致性检查：`tools/check_nosql_data.py`

## 9. 验证与证据截图建议（对应报告图表）

用于“数据库设计”与“测试验证”的常见截图内容包括：

- 集合与字段结构（第 4 章）：展示数据库 `script_kill_store` 的集合列表及任意集合的字段列（建议保存为 `figures/fig4-1_mongodb_collections.png`）。
- 集合计数（第 6 章）：展示 `orders/transactions/lock_records` 的 `countDocuments` 输出（建议保存为 `figures/fig6-1_mongo_counts.png`）。
- 锁位过期验证（第 6 章）：展示 `lock_records` 中有效锁位计数与状态分布（建议保存为 `figures/fig6-5_mongo_verify.png`）。

