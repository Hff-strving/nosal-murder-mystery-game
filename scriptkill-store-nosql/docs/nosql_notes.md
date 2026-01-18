以下是该表格的Markdown格式：

### 表2-1 关键功能需求（GWT）



| 编号  | Given（前置条件）| When（触发事件）| Then（期望结果）|
|-------|--------------------------|--------------------------|--------------------------------------------|
| 2-1   | 用户存在且凭证有效| 用户提交登录请求| 返回 token 与角色信息；后续请求携带鉴权头 |
| 2-2   | 剧本处于上架状态| 玩家请求剧本列表/详情| 返回剧本信息与封面字段，列表可排序展示 |
| 2-3   | 存在未来场次且容量>0| 玩家请求某剧本的场次列表| 返回时间/价格/占用状态（Booked/Locked/…） |
| 2-4   | 场次 seats>0 且玩家无有效锁 | 玩家发起锁位请求| 原子扣减库存并生成 lock_id；锁位具备 TTL |
| 2-5   | 场次 seats=0 或玩家已锁位 | 玩家发起锁位请求| 返回失败信息（已满/已锁定），库存不出现负 |
| 2-6   | 玩家有锁位或 seats>0| 玩家创建订单| 写入订单（待支付）；若由锁位转单则锁位状 |
| 2-7   | 订单存在且为待支付| 玩家支付订单| 订单状态→已支付，并生成交易流水用于报表 |
| 2-8   | 员工已登录且绑定 DM| 员工查询管理端数据| 仅返回该 DM 分域数据；越权访问被拒绝 |
| 2-9   | 老板已登录| 老板查询统计报表并按 DM 过滤 | 返回全局或指定 DM 聚合结果，口径与交易流 |
| 2-10  | 场次存在且无已支付订单| 员工取消场次| 场次状态变更为取消，避免破坏已完成交易 |

.

3-1

├── app.py
├── models/          # 业务模型（Auth/Script/Schedule/Lock/Order/Report）
├── nosql/           # MongoDB/Redis 访问与并发锁位服务
├── tools/           # 迁移/造数/检查/压测辅助脚本
├── results/         # JMeter jtl 结果文件
├── docs/            # 报告与测试说明
├── images/          # 剧本封面等资源
└── frontend-vue/    # Vue3 前端工程



4-1



### 数据集合设计表

| 集合          | 主键/标识         | 关键字段（示例）| 设计要点 |
|---------------|-------------------|----------------------------------------|------------------------------------------|
| users         | \_id=User\_ID     | Username, Phone, Password_Hash, Role, Ref_ID | 统一承载三类角色登录态，Ref_ID绑定玩家/员工业务身份 |
| scripts       | \_id=Script\_ID   | Title, Type, Min_Players, Max_Players, Status, Cover_Image, Group_Category, Difficulty | 剧本信息相对稳定，适合独立集合维护 |
| schedules     | \_id=Schedule\_ID | Script_ID, Room_ID, DM_ID, Start_Time, End_Time, Real_Price, Status, Script_Title, Room_Name, DM_Name, Max_Players | 场次列表高频读取，冗余展示字段降低查询拼装 |
| lock_records  | \_id=LockID       | Schedule_ID, Player_ID, LockTime, ExpireTime, Status, Script_Title, Room_Name, DM_Name | Redis锁位的持久化镜像与审计记录，支持转单与过期状态 |
| orders        | \_id=Order\_ID    | Player_ID, Schedule_ID, Amount, Pay_Status, Create_Time, Script_Title, Room_Name, DM_ID, Start_Time | 订单事实集合，冗余字段支撑列表与报表聚合 |
| transactions  | \_id=Trans\_ID    | Order_ID, Amount, Trans_Type, Channel, Trans_Time, Result, DM_ID, Schedule_ID | 报表口径以流水为准，Result用于过滤无效/纠正记录 |
| counters      | \_id=seq_name     | seq, updated_at | 自增序列发生器，替代关系型自增列 |



4-2

### Redis Key 设计表

| Key 模式                  | 类型       | 值/成员含义                          | 生命周期与用途                                  |
|---------------------------|------------|---------------------------------------|-------------------------------------------------|
| `seats:{schedule_id}`     | String     | 剩余座位数（整数）                    | 运行态库存；初始化时由MongoDB场次容量减去已下单与有效锁位计算得到 |
| `lock:{schedule_id}:{player_id}` | String | LockID（整数）| 玩家对场次的有效锁位；设置TTL，过期自动删除        |
| `locks:exp`               | Sorted Set | member=lockKey, score=到期毫秒时间戳   | 过期索引；用于后台扫描回补seats并同步MongoDB状态   |
| `lock:id`                 | String     | LockID 自增计数器                     | 通过INCR生成唯一LockID                           |



6-3

### 接口性能测试结果表

| 请求        | 样本数 | 成功数 | 失败数 | 错误率 | 平均响应(ms) | P95(ms) | 吞吐量(请求/秒) |
|-------------|--------|--------|--------|--------|--------------|---------|-----------------|
| Login       | 100    | 100    | 0      | 0%     | 11.58        | 21.1    | 21.12           |
| Create Lock | 100    | 5      | 95     | 95%    | 14.41        | 25.0    | 21.38           |
| TOTAL       | 200    | 105    | 95     | 47.5%  | 12.99        | 25.0    | 42.16           |



6-1

### 数据集合文档数量统计表

| 对象                | 统计口径                          | 数量  |
|---------------------|-----------------------------------|-------|
| users               | `db.users.countDocuments({})`     | 128   |
| players             | `db.players.countDocuments({})`   | 120   |
| dms                 | `db.dms.countDocuments({})`       | 6     |
| rooms               | `db.rooms.countDocuments({})`     | 5     |
| scripts             | `db.scripts.countDocuments({})`   | 12    |
| schedules           | `db.schedules.countDocuments({})` | 192   |
| orders              | `db.orders.countDocuments({})`    | 1201  |
| transactions        | `db.transactions.countDocuments({})` | 846 |
| lock_records        | `db.lock_records.countDocuments({})` | 24 |
| 总文档数（关键集合求和） | 上述行求和                        | 2534  |


6-2
### 表6-2 Postman 功能测试用例

| 序号 | 接口/用例                          | 方法 | URL                                  |
|------|------------------------------------|------|--------------------------------------|
| 1    | Auth - Player Login (player_3001)  | POST | {{baseUrl}}/api/auth/login           |
| 2    | Auth - Boss Login (boss_demo)      | POST | {{baseUrl}}/api/auth/login           |
| 3    | Scripts - List                     | GET  | {{baseUrl}}/api/scripts              |
| 4    | Scripts - Detail                   | GET  | {{baseUrl}}/api/scripts/{{scriptId}} |
| 5    | Schedules - By Script (with player_id) | GET | {{baseUrl}}/api/scripts/{{scriptId}}/sch |
| 6    | Lock - Create                      | POST | {{baseUrl}}/api/locks                |
| 7    | Lock - Cancel                      | POST | {{baseUrl}}/api/locks/{{lockId}}/cance |
| 8    | Order - Create                     | POST | {{baseUrl}}/api/orders               |
| 9    | Order - Pay                        | POST | {{baseUrl}}/api/orders/{{orderId}}/pay |
| 10   | Order - Cancel                     | POST | {{baseUrl}}/api/orders/{{orderId}}/can |
| 11   | My - Orders                        | GET  | {{baseUrl}}/api/my/orders            |
| 12   | My - Locks                         | GET  | {{baseUrl}}/api/my/locks             |
| 13   | Admin - Rooms                      | GET  | {{baseUrl}}/api/admin/rooms          |
| 14   | Admin - Dashboard                  | GET  | {{baseUrl}}/api/admin/dashboard      |
| 15   | Admin - Reports Top Scripts        | GET  | {{baseUrl}}/api/admin/reports/top-sc |
| 16   | Admin - DB Objects (Capability Self-) | GET | {{baseUrl}}/api/admin/db-objects     |

