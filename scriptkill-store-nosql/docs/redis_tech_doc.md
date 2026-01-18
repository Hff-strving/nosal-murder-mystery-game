# Redis 技术文档（剧本杀店务管理系统）

## 1. 适用范围与系统定位

Redis 在系统中承担**高并发运行态状态（hot state）**的存储与协调职责，主要用于库存、锁位与过期回收等对低延迟与原子性要求极高的路径。相较于 MongoDB 的业务事实持久化，Redis 的数据通常具备短生命周期与强时效性，允许在故障或重启后由 MongoDB 事实数据推导重建。

系统以场次（Schedule）为库存粒度，将“座位剩余量”与“有效锁位”维护在 Redis 中，并通过 Lua 脚本实现原子扣减与锁位创建，从而在百级并发请求下保证不超卖。

## 2. 连接配置与部署要点

连接参数由 `nosql/config.py` 提供，支持环境变量覆盖：

- `REDIS_HOST`：默认 `localhost`
- `REDIS_PORT`：默认 `6379`
- `REDIS_DB`：默认 `0`
- `REDIS_PASSWORD`：默认空

客户端封装：`nosql/redis_client.py:get_redis()`，启用 `decode_responses=True` 以直接返回字符串。

Docker 部署场景下，只要容器映射到宿主机端口（例如 `-p 6379:6379`），默认配置即可通过 `localhost:6379` 访问。

## 3. Key 空间设计与命名规范

Redis Key 采用“前缀 + 主维度 ID”的命名规范，避免冲突并增强可读性。核心 Key 如下：

### 3.1 `seats:{schedule_id}`（String）

**语义**：某场次的剩余座位数（整数）。  
**用途**：库存控制的唯一权威运行态数值；锁位或直接下单会原子扣减；取消/过期会回补。  
**初始化策略**：首次访问或重置时，系统以 MongoDB `schedules.Max_Players` 减去订单占用与有效锁位数，计算并写入 Redis。实现位于：

- `nosql/seat_lock_service.py:ensure_seats_initialized()`

### 3.2 `lock:{schedule_id}:{player_id}`（String + TTL）

**语义**：某玩家对某场次的有效锁位，value 为 LockID。  
**用途**：避免同一玩家重复锁位；为“锁位→下单”提供运行态凭证。  
**生命周期**：设置 TTL（毫秒级 PX），默认 15 分钟（可配置），到期后键自动删除。TTL 默认配置：

- `nosql/config.py:LOCK_MINUTES_DEFAULT`

### 3.3 `locks:exp`（Sorted Set）

**语义**：锁位过期索引队列，member 为 `lock:{schedule_id}:{player_id}`，score 为到期毫秒时间戳。  
**用途**：解决“TTL 自动删除 lockKey 但 seats 不会自动 +1”的一致性缺口；后台线程按 score 扫描已到期成员并回补库存。实现位于：

- `nosql/seat_lock_service.py:cleanup_expired_locks()`
- `app.py` 启动线程定期调用清理函数

### 3.4 `lock:id`（String，自增计数器）

**语义**：LockID 自增序列。  
**用途**：通过 INCR 生成并发安全的 LockID，与 MongoDB `lock_records._id` 对齐；避免锁位主键冲突。  
**初始化**：迁移脚本会用 MongoDB 中最大 LockID 初始化该键：

- `tools/migrate_mysql_to_mongo.py:_init_seats_and_lock_id()`

## 4. Lua 脚本与原子性实现

Redis 并发控制的核心在于将“检查—更新—写入”合并为单次原子执行，避免竞态条件。系统在 `nosql/seat_lock_service.py` 内定义并通过 `EVAL` 执行 Lua 脚本。

### 4.1 `_LUA_LOCK`（创建锁位：检查 + 扣减 + 写入）

**输入 Keys**：

1. `lockKey`：`lock:{schedule_id}:{player_id}`
2. `seatsKey`：`seats:{schedule_id}`
3. `expZset`：`locks:exp`
4. `lockIdKey`：`lock:id`

**输入 Args**：

- `ttlMs`：锁位 TTL（毫秒）
- `expAtMs`：到期时间戳（毫秒）

**原子步骤**：

- 若 `lockKey` 已存在，返回 `-1`（重复锁位）
- 读取 `seatsKey`，若 `<=0` 返回 `-2`（无库存）
- `INCR lock:id` 得到新 LockID
- `DECR seatsKey` 扣减库存
- `SET lockKey newId PX ttlMs` 写入锁位键并设置 TTL
- `ZADD locks:exp expAtMs lockKey` 登记到期索引
- 返回 LockID

**返回码语义**：

- `-1`：该玩家已锁定该场次
- `-2`：该场次已满
- `>0`：锁位成功（LockID）

后端会将返回码映射为可解释错误信息（`models/lock_model.py`）。

### 4.2 `_LUA_CANCEL_LOCK`（取消锁位：删除 + 回补 + 索引移除）

**输入 Keys**：`lockKey`、`seatsKey`、`expZset`。  
**原子步骤**：若 `DEL lockKey` 成功，则 `INCR seatsKey` 并 `ZREM expZset lockKey`，返回 1；否则返回 0。  
**业务含义**：取消锁位可立即释放座位并消除到期索引残留。

### 4.3 `_LUA_CONVERT_LOCK`（锁位转订单：删除 lockKey + 移除索引）

**输入 Keys**：`lockKey`、`expZset`。  
**原子步骤**：若 `DEL lockKey` 成功，则 `ZREM expZset lockKey` 返回 1；否则返回 0。  
**业务含义**：锁位转订单后，锁位运行态状态应消失，避免过期线程重复回补库存。

### 4.4 `_LUA_TAKE_SEAT`（直接占座：库存扣减）

**输入 Keys**：`seatsKey`。  
**原子步骤**：读取 seats，若 `<=0` 返回 0；否则 `DECR seatsKey` 返回 1。  
**业务含义**：当玩家未提前锁位而直接下单时，仍需在 Redis 内原子扣减库存，避免超卖。

## 5. 运行态服务函数与作用说明

Redis 相关业务逻辑集中于 `nosql/seat_lock_service.py`，其对外提供的主要函数如下：

### 5.1 `ensure_seats_initialized(schedule_id)`

**作用**：在 Redis 未存在 `seats:{schedule_id}` 时，从 MongoDB 事实推导并初始化 seats。  
**推导口径**：

- `Max_Players`：来自 `schedules.Max_Players`
- `booked`：`orders` 中 `Pay_Status in (0,1)` 的计数
- `locked`：`lock_records` 中 `Status=0 且 ExpireTime>now` 的计数
- `seats = Max_Players - booked - locked`（下限为 0）

该设计保证 Redis 状态可重建，降低对 Redis 持久化的依赖。

### 5.2 `create_lock(player_id, schedule_id, lock_minutes=None)`

**作用**：创建锁位（Redis 原子）并返回 `LockID` 与到期时间。  
**内部行为**：

- 先确保 seats 初始化；
- 计算 TTL 与到期时间戳；
- 执行 `_LUA_LOCK` 完成原子扣减与写入。

### 5.3 `cancel_lock(player_id, schedule_id)`

**作用**：取消锁位并回补库存（Redis 原子）。  
**内部行为**：执行 `_LUA_CANCEL_LOCK`。

### 5.4 `convert_lock_to_order(player_id, schedule_id)`

**作用**：锁位转订单后删除运行态锁位（Redis 原子）。  
**内部行为**：执行 `_LUA_CONVERT_LOCK`。

### 5.5 `take_seat(schedule_id)` / `release_seat(schedule_id)`

**作用**：为“无锁位直接下单/取消订单”提供座位扣减与归还能力。  
**内部行为**：

- `take_seat` 执行 `_LUA_TAKE_SEAT` 原子扣减；
- `release_seat` 对 `seats:{schedule_id}` 做 `INCR` 回补。

### 5.6 `cleanup_expired_locks(limit=200)`

**作用**：处理 Redis TTL 过期后座位回补与 MongoDB 状态同步。  
**背景**：TTL 删除只会移除 `lockKey`，并不会自动将 `seats` 回补，若缺失补偿将导致库存永久减少。  
**处理流程**：

- 以当前时间 `now_ms` 扫描 `locks:exp` 中 `score<=now_ms` 的成员（`ZRANGEBYSCORE`）；
- 对每个成员解析 `schedule_id` 与 `player_id`；
- 若 `lockKey` 仍存在（可能被重置），移除 `locks:exp` 中该成员并跳过；
- 若 `lockKey` 不存在（已过期），对 `seats:{schedule_id}` 做回补并移除 `locks:exp` 成员；
- 同步 MongoDB：将对应 `lock_records` 中仍为锁定且已过期的记录更新为过期状态（`Status=3`）。

**调度方式**：后端启动后由 `app.py` 创建后台线程以固定周期（约 5 秒）执行清理，避免过期队列积压。

## 6. 典型业务链路中的 Redis 参与点

### 6.1 锁位链路（锁位 → 生成 LockID → TTL）

系统在锁位接口中调用 `LockModel.create_lock()`：

- Redis：`create_lock()` 执行 Lua 原子扣减与 `lockKey` 写入；
- MongoDB：写入 `lock_records` 历史记录，保留快照字段；
- 前端：获得 `lock_id` 作为成功凭证。

### 6.2 下单链路（锁位转单 / 无锁位占座）

系统在 `OrderModel.create_order()` 中实现两类路径：

- 若存在有效锁位键：执行 `convert_lock_to_order()` 删除 `lockKey` 并同步 MongoDB 锁位记录 `Status=1`；
- 若不存在锁位键：执行 `take_seat()` 原子扣减 seats 后再写入订单；若订单写入失败，通过 `release_seat()` 回补库存（补偿逻辑）。

### 6.3 取消链路（取消订单/取消锁位）

- 取消锁位：`cancel_lock()` 原子回补 seats；
- 取消未支付订单：MongoDB 更新订单状态后执行 `release_seat()` 回补 seats。

## 7. 运行态验证与证据截图建议（对应报告图表）

用于第 4 章（Redis 设计）与第 6 章（测试验证）的常见截图内容包括：

- Key 空间与示例值（第 4 章）：展示 `seats:*`、`lock:*`、`locks:exp`、`lock:id` 的存在性与含义（建议保存为 `figures/fig4-2_redis_keys.png`）。
- 并发测试后验证（第 6 章）：展示 `GET seats:<scheduleId>`、`SCAN lock:<scheduleId>:*`、`ZRANGE locks:exp ...`、`TTL lock:<scheduleId>:<playerId>` 等（建议保存为 `figures/fig6-4_redis_verify.png`）。

系统已提供“自动采集版”验证图（`figures/fig6-4_redis_verify.png`），若报告更偏向“真实证据截图”，则可使用 `docker exec -it my-redis redis-cli` 输出上述命令结果并截图替换。

