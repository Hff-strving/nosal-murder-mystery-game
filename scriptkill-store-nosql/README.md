# 剧本杀店务管理系统（MongoDB + Redis / NoSQL 版本）部署与运行

本项目后端使用 Flask，前端使用 Vue3（Vite）。MongoDB 与 Redis 以 Docker 容器方式部署并通过端口映射提供服务（本项目默认连接 `localhost`）。

## 1. 环境依赖

- Windows
- Docker（用于 MongoDB、Redis）
- Python 3.10+（后端与脚本）
- Node.js 18+（前端）

## 2. 启动 MongoDB / Redis（Docker）

> 若已存在同名容器并在运行，可跳过本节。

```bash
docker run -d --name my-redis -p 6379:6379 redis:latest
docker run -d --name my-mongo -p 27017:27017 mongo:latest
```

可选：带持久化卷（建议在需要保留数据时使用）

```bash
docker volume create mongo_data
docker run -d --name my-mongo -p 27017:27017 -v mongo_data:/data/db mongo:latest
```

连通性自检（可选）：

```bash
docker exec -it my-mongo mongosh --eval "db.adminCommand('ping')"
docker exec -it my-redis redis-cli ping
```

## 3. 后端启动（Flask）

在项目根目录执行：

```bash
pip install -r requirements.txt
python app.py
```

默认监听：`http://127.0.0.1:5000`

### 3.1 环境变量（可选）

默认配置已可直接连接 Docker 映射端口；如需自定义可设置：

- `MONGO_URI`（默认 `mongodb://localhost:27017`）
- `MONGO_DB_NAME`（默认 `script_kill_store`）
- `REDIS_HOST`（默认 `localhost`）
- `REDIS_PORT`（默认 `6379`）
- `REDIS_DB`（默认 `0`）
- `REDIS_PASSWORD`（默认空）
- `LOCK_MINUTES_DEFAULT`（默认 `15`）

## 4. 数据准备（迁移 / 造数 / 检查）

### 4.1 造数（推荐：快速得到可测数据）

```bash
python tools/seed_nosql_data.py --min-orders 1200
python tools/check_nosql_data.py
```

### 4.2 从 MySQL 迁移（可选）

```bash
pip install -r tools/requirements-mysql-migrate.txt
python tools/migrate_mysql_to_mongo.py --drop --mysql-host localhost --mysql-port 3306 --mysql-user root --mysql-password 123456 --mysql-db 剧本杀店务管理系统
```

## 5. 前端启动（Vue3 + Vite）

进入 `frontend-vue/`：

```bash
npm install
npm run dev
```

默认地址：`http://127.0.0.1:5173`  
说明：Vite 已配置把 `/api` 代理到 `http://127.0.0.1:5000`（见 `frontend-vue/vite.config.js`）。

## 6. 测试（Postman / JMeter）

### 6.1 Postman 功能测试

- 导入：`tools/postman_collection.json`
- 导入：`tools/postman_environment.json`（选择环境：`ScriptKill NoSQL Local`）

建议执行顺序与截图清单见：`测试指南-精简版.md`、`docs/测试.md`

### 6.2 JMeter 并发锁位测试

1）导出压测账号（从 MongoDB `users(Role=player)` 生成 CSV）：

```bash
python tools/export_jmeter_players_csv.py --out tools/jmeter_players.csv --limit 100
```

2）必要时重置测试场次（用于重复压测）：

```bash
python tools/reset_test_schedule.py 9001 5
```

3）命令行压测（示例）：

```bash
jmeter -n -t tools/jmeter_lock_test.jmx -JplayersCsv=tools/jmeter_players.csv -JscheduleId=9001 -Jthreads=100 -JrampUp=5 -l results/lock_test.jtl
```

## 7. 默认测试账号

- 玩家：`player_3001 / 123456`
- 员工：`staff_demo / 123456`
- 老板：`boss_demo / 123456`

## 8. Windows 终端中文编码（可选）

如遇到中文用户名在 PowerShell/CMD 显示为 `???`，建议使用上面的纯英文账号；或运行：

```powershell
./tools/set-terminal-utf8.ps1
```


