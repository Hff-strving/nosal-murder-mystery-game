<p align="right">
  <a href="./README_EN.md"><img alt="language English" src="https://img.shields.io/badge/language-English-1f6feb?style=flat-square"></a>
  <a href="./README.md"><img alt="语言 简体中文" src="https://img.shields.io/badge/语言-简体中文-9aa0a6?style=flat-square"></a>
</p>

# nosal-murder-mystery-game (NoSQL Course Portfolio)

[VM-related parts used in the course are not included/shown in this repository.]

This repository aggregates my NoSQL course work: two small cases (Redis & MongoDB) and one final project (a MongoDB + Redis high-concurrency system).

中文版：`README.md`

## Repository Layout

- `redis-case/`: Redis case (voucher/ticket seckill under high concurrency)
- `mongodb-case/`: MongoDB case (basic CRUD & document operations)
- `scriptkill-store-nosql/`: Final project — Script-kill store management system (MongoDB + Redis)

## Projects

### 1) Redis Case (Voucher/Ticket Seckill)

Path: `redis-case/seckill/`

Key points:
- Redis cache and high-throughput access patterns
- Atomic stock deduction via Lua scripts (anti-oversell)
- Distributed lock / purchase limit (anti-duplicate)
- JMeter load test assets (`.jmx/.jtl`)

Note: the original lab environment involved VM/LAN setup (e.g., Redis on a VM). This repository keeps the code and documentation, but does not include VM setup details.

### 2) MongoDB Case (Basic Document Operations)

Path: `mongodb-case/`

Key points:
- PyMongo connection and CRUD
- Simple wrappers/utilities for document operations

### 3) Final Project: Script-kill Store Management System (NoSQL)

Path: `scriptkill-store-nosql/`

Key points:
- MongoDB stores durable business facts (users/scripts/schedules/orders/transactions/lock_records, etc.)
- Redis stores runtime state (inventory seats, lock keys, expiration queue locks:exp)
- Lua atomic operations + TTL + background cleanup for consistency convergence
- Vue3 frontend + Flask backend, multi-role domain separation (player/staff/boss)

Entry points:
- Deployment & run: `scriptkill-store-nosql/README.md`
- Tech docs: `scriptkill-store-nosql/docs/mongodb_tech_doc.md`, `scriptkill-store-nosql/docs/redis_tech_doc.md`
- Report materials: `scriptkill-store-nosql/docs/`
