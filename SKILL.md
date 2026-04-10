---
name: RDS-event-check
description: Inspect RDS health metrics based on RDS event types. Detects event category, queries relevant CloudWatch metrics, and generates a diagnostic report.
version: 1.3.0
author: AI Assistant
triggers:
  - RDS事件确认
  - rds event check
  - rds failover
  - rds recovery
  - rds maintenance
  - rds low storage
  - rds failure
  - rds availability
  - rds replica lag
  - rds 故障转移
  - rds 健康检查
---

# AWS RDS Event Health Inspector

Detect important RDS events and automatically query the most relevant CloudWatch metrics to diagnose the current health of the RDS instance.

## When to Use

- An RDS failover event occurred and you need to verify post-failover health
- RDS maintenance completed and you want to confirm the instance is healthy
- A low storage or failure event was triggered
- Recovery events appeared and you need to validate the instance is back to normal
- Replication issues detected on read replicas
- Any RDS event that requires a health check of the affected instance

## AWS China Region (aws-cn)

This skill targets the AWS China partition (`aws-cn`). Key differences:
- **Regions**: `cn-north-1` (Beijing), `cn-northwest-1` (Ningxia)
- **Default region**: `cn-northwest-1` (if user does not specify)
- **ARN partition**: `arn:aws-cn:rds:<region>:<account>:db:<instance>`
- **Endpoint format**: `<instance>.<id>.rds.<region>.amazonaws.com.cn`
- **Aurora 可用性**: Aurora MySQL 和 Aurora PostgreSQL 均在中国区可用
- **Aurora 中国区差异** (参考 https://docs.amazonaws.cn/en_us/aws/latest/userguide/aurora.html):
  - Backtracking 不可用
  - Aurora Serverless v1 在北京区不可用
  - 宁夏区使用独立 CA，证书标识为 `rds-ca-rsa2048-g1`
  - Zero-ETL integration with SageMaker Lakehouse 不可用
- **Note**: 跨区域只读副本等部分全球功能可能受限

When executing AWS CLI commands, always use `--region cn-northwest-1` or `--region cn-north-1`.

## Prerequisites

- AWS CLI configured with valid credentials for AWS China account
- Permissions: `rds:DescribeEvents`, `rds:DescribeDBInstances`, `cloudwatch:GetMetricStatistics`

## Workflow

1. **Identify the event** — User provides an RDS event ID, event category, or describes the situation
2. **Classify the scenario** — Map to one of the defined event scenarios below
3. **Detect region** — Check `aws sts get-caller-identity` to confirm aws-cn partition; default to `cn-northwest-1`
4. **Describe the instance** — Call `rds describe-db-instances` to get instance metadata (engine, class, Multi-AZ, storage, status)
5. **Fetch recent events** — Call `rds describe-events` for the instance (last 24h) to confirm the event
6. **Query CloudWatch metrics** — For each metric, query TWO time windows relative to the reference time point (user-specified or current time):
   - **Past 1 hour** (T-60min → T): period=300s (5-min), provides trend context
   - **Past 10 minutes** (T-10min → T): period=60s (1-min), provides real-time snapshot
   - If user provides a specific time point (e.g., failover timestamp), use that as T; otherwise T = now
7. **Generate report** — Summarize findings with thresholds and actionable recommendations

## Event Scenarios and Metric Mapping

### 1. Failover (Category: `failover`)

Key Events:
- `RDS-EVENT-0013` — Multi-AZ instance failover started
- `RDS-EVENT-0015` — Multi-AZ failover to standby complete
- `RDS-EVENT-0049` — Multi-AZ instance failover completed
- `RDS-EVENT-0050` — Multi-AZ instance activation started
- `RDS-EVENT-0051` — Multi-AZ instance activation completed
- `RDS-EVENT-0065` — Recovered from partial failover
- `RDS-EVENT-0034` — Abandoning failover (recently occurred)
- `RDS-EVENT-0069` — Cluster failover failed (Aurora/Multi-AZ DB cluster)
- `RDS-EVENT-0071` — Completed failover to DB instance (Aurora/Multi-AZ DB cluster)
- `RDS-EVENT-0072` — Started same AZ failover (Aurora/Multi-AZ DB cluster)
- `RDS-EVENT-0073` — Started cross AZ failover (Aurora/Multi-AZ DB cluster)

Metrics to Query:
| Metric | Why | Threshold |
|--------|-----|-----------|
| `CPUUtilization` | High CPU may have triggered failover | Avg >80% warning, >90% critical |
| `DatabaseConnections` | Verify connections re-established post-failover | Sudden drop to 0 then recovery |
| `FreeableMemory` | Memory pressure can cause failover | <256MB critical |
| `ReadLatency` | I/O issues can trigger failover | >20ms warning |
| `WriteLatency` | I/O issues can trigger failover | >20ms warning |
| `DiskQueueDepth` | Disk saturation indicator | >10 warning |
| `FreeStorageSpace` | Storage exhaustion can cause failover | <10% critical |
| `NetworkReceiveThroughput` | Network health post-failover | Sudden drop indicates issue |
| `NetworkTransmitThroughput` | Network health post-failover | Sudden drop indicates issue |
| `ReadIOPS` | I/O pattern change after failover | Compare pre/post |
| `WriteIOPS` | I/O pattern change after failover | Compare pre/post |

Analysis Focus:
- Compare metrics before and after the failover timestamp
- Check if connections recovered to pre-failover levels
- Verify latency returned to normal
- Confirm the new primary is handling load properly

### 2. Recovery (Category: `recovery`)

Key Events:
- `RDS-EVENT-0020` — Recovery of DB instance started
- `RDS-EVENT-0021` — Recovery of DB instance complete
- `RDS-EVENT-0052` — Multi-AZ instance recovery started
- `RDS-EVENT-0053` — Multi-AZ instance recovery completed
- `RDS-EVENT-0066` — Degraded while mirroring reestablished
- `RDS-EVENT-0361` — Recovery of standby started
- `RDS-EVENT-0362` — Recovery of standby completed

Metrics to Query:
| Metric | Why | Threshold |
|--------|-----|-----------|
| `CPUUtilization` | Recovery is CPU-intensive | >90% during recovery is expected |
| `FreeableMemory` | Memory consumption during recovery | <128MB critical |
| `ReadIOPS` | Recovery reads heavily from disk | Spike expected |
| `WriteIOPS` | Recovery writes heavily | Spike expected |
| `ReadLatency` | I/O performance during recovery | >50ms warning |
| `WriteLatency` | I/O performance during recovery | >50ms warning |
| `DiskQueueDepth` | Disk pressure during recovery | >20 expected during recovery |
| `DatabaseConnections` | Availability during recovery | 0 = still recovering |
| `FreeStorageSpace` | Ensure storage is sufficient for recovery | <5% critical |
| `ReplicaLag` | If read replicas exist, check lag | >60s warning |

Analysis Focus:
- Track recovery duration (time between start and complete events)
- Monitor if metrics return to baseline after recovery completes
- Check if standby rebuild is impacting primary performance

### 3. Low Storage (Category: `low storage`)

Key Events:
- `RDS-EVENT-0007` — Allocated storage exhausted
- `RDS-EVENT-0089` — Free storage capacity low (>90% used)
- `RDS-EVENT-0227` — Aurora storage dangerously low (Aurora only)

Metrics to Query:
| Metric | Why | Threshold |
|--------|-----|-----------|
| `FreeStorageSpace` | Primary indicator | <10% warning, <5% critical |
| `BinLogDiskUsage` | Binary logs consuming space (MySQL/MariaDB) | Growing rapidly = issue |
| `WriteIOPS` | Write activity driving storage consumption | Correlate with storage decline |
| `WriteThroughput` | Data write rate | High = rapid storage consumption |
| `DiskQueueDepth` | I/O queuing due to storage pressure | >10 warning |
| `ReadLatency` | Performance degradation from storage issues | >20ms warning |
| `WriteLatency` | Performance degradation from storage issues | >20ms warning |
| `DatabaseConnections` | Instance may shut down if storage full | Drop to 0 = shutdown |
| `ReplicationSlotDiskUsage` | Replication slots consuming space (PostgreSQL) | Growing = issue |
| `SwapUsage` | Memory pressure from storage issues | >256MB warning |

Analysis Focus:
- Calculate storage consumption rate (GB/hour)
- Estimate time until storage exhaustion
- Check if storage autoscaling is enabled and working
- Identify the source of storage growth (binlogs, data, temp files)

### 4. Maintenance (Category: `maintenance`)

Key Events:
- `RDS-EVENT-0026` — Applying offline patches (unavailable)
- `RDS-EVENT-0027` — Finished applying offline patches
- `RDS-EVENT-0047` — Database instance patched
- `RDS-EVENT-0155` — Minor version upgrade available
- `RDS-EVENT-0178` — Database instance upgrade in progress
- `RDS-EVENT-0267` — Engine version upgrade started
- `RDS-EVENT-0268` — Engine version upgrade finished
- `RDS-EVENT-0270` — Engine version upgrade failed, rollback succeeded
- `RDS-EVENT-0422` — Host replacement due to pending maintenance

Metrics to Query:
| Metric | Why | Threshold |
|--------|-----|-----------|
| `CPUUtilization` | Post-maintenance performance baseline | >80% warning |
| `DatabaseConnections` | Verify connections restored | Compare to pre-maintenance |
| `FreeableMemory` | Memory after engine upgrade | <256MB warning |
| `ReadLatency` | I/O performance post-maintenance | >20ms warning |
| `WriteLatency` | I/O performance post-maintenance | >20ms warning |
| `ReadIOPS` | I/O pattern post-maintenance | Compare to baseline |
| `WriteIOPS` | I/O pattern post-maintenance | Compare to baseline |
| `FreeStorageSpace` | Storage after upgrade | <10% warning |
| `BurstBalance` | gp2 burst credits after maintenance I/O | <20% warning |

Analysis Focus:
- Confirm instance is back online and accepting connections
- Compare post-maintenance performance to pre-maintenance baseline
- Check if engine version upgrade introduced performance regression
- Verify no pending maintenance actions remain

### 5. Failure (Category: `failure`)

Key Events:
- `RDS-EVENT-0031` — DB instance put into incompatible state (PITR recommended)
- `RDS-EVENT-0035` — Invalid parameters, instance in error state
- `RDS-EVENT-0036` — Incompatible network
- `RDS-EVENT-0219` — DB instance in invalid state
- `RDS-EVENT-0278` — DB instance creation failed
- `RDS-EVENT-0306` — Storage configuration upgrade failed
- `RDS-EVENT-0418` — Unable to access KMS encryption key
- `RDS-EVENT-0419` — KMS key inaccessible, instance will be inaccessible

Metrics to Query:
| Metric | Why | Threshold |
|--------|-----|-----------|
| `CPUUtilization` | Check if instance is responsive | 0% = instance down |
| `DatabaseConnections` | Check if instance accepts connections | 0 = down |
| `FreeableMemory` | Memory state before failure | <64MB = OOM possible cause |
| `FreeStorageSpace` | Storage state before failure | 0 = storage exhaustion cause |
| `ReadLatency` | I/O issues before failure | >100ms = severe I/O issue |
| `WriteLatency` | I/O issues before failure | >100ms = severe I/O issue |
| `DiskQueueDepth` | Disk saturation before failure | >50 = severe |
| `SwapUsage` | Memory pressure before failure | >1GB = severe |
| `BurstBalance` | I/O credit exhaustion | 0% = throttled |
| `EBSIOBalance%` | EBS I/O credit exhaustion | <10% = throttled |

Analysis Focus:
- Determine root cause from metrics leading up to the failure
- Check instance status (describe-db-instances → DBInstanceStatus)
- Identify if failure is infrastructure (storage, network) or configuration
- Recommend recovery action (PITR, modify, reboot)

### 6. Availability (Category: `availability`)

Key Events:
- `RDS-EVENT-0004` — DB instance shutdown
- `RDS-EVENT-0006` — DB instance restarted
- `RDS-EVENT-0221` — Storage-full threshold reached, DB shut down
- `RDS-EVENT-0222` — Free storage capacity low, shutdown imminent

Metrics to Query:
| Metric | Why | Threshold |
|--------|-----|-----------|
| `CPUUtilization` | Instance responsiveness | 0% = down |
| `DatabaseConnections` | Connection availability | 0 = unavailable |
| `FreeStorageSpace` | Storage-triggered shutdown | 0 bytes = cause |
| `FreeableMemory` | Memory-triggered issues | <64MB critical |
| `ReadLatency` | Performance before shutdown | Spike before shutdown |
| `WriteLatency` | Performance before shutdown | Spike before shutdown |
| `NetworkReceiveThroughput` | Network availability | 0 = network issue |
| `NetworkTransmitThroughput` | Network availability | 0 = network issue |

Analysis Focus:
- Determine if shutdown was planned (user-initiated) or unplanned
- Check if storage exhaustion caused the shutdown
- Verify instance has restarted and is accepting connections
- Monitor for recurring availability events

### 7. Read Replica Issues (Category: `read replica`)

Key Events:
- `RDS-EVENT-0045` — Replication stopped
- `RDS-EVENT-0046` — Replication resumed
- `RDS-EVENT-0057` — Replication streaming terminated
- `RDS-EVENT-0062` — Replication manually stopped
- `RDS-EVENT-0202` — Read replica creation failed

Metrics to Query:
| Metric | Why | Threshold |
|--------|-----|-----------|
| `ReplicaLag` | Primary replication health indicator | >60s warning, >300s critical |
| `CPUUtilization` | Replica under load | >80% = replica can't keep up |
| `DatabaseConnections` | Replica serving reads | Compare to expected |
| `ReadIOPS` | Replica read activity | Compare to source |
| `WriteIOPS` | Replica write activity (replay) | Compare to source |
| `FreeStorageSpace` | Replica storage | <10% warning |
| `FreeableMemory` | Replica memory | <256MB warning |
| `DiskQueueDepth` | Replica I/O pressure | >10 warning |
| `BinLogDiskUsage` | Binary log accumulation (MySQL) | Growing = replication behind |
| `OldestReplicationSlotLag` | Replication slot lag (PostgreSQL) | Growing = issue |

Analysis Focus:
- Identify cause of replication break (network, storage, load)
- Check if source instance has high write load overwhelming replica
- Verify replica has sufficient resources (CPU, memory, storage, IOPS)
- Check ReplicaLag trend (increasing = not catching up)

### 8. Notification / Configuration Change

Key Events:
- `RDS-EVENT-0189` — gp2 burst balance credits low
- `RDS-EVENT-0225` — Approaching maximum storage threshold
- `RDS-EVENT-0403` — Critically low memory, innodb_buffer_pool_size auto-adjusted
- `RDS-EVENT-0404` — Critically low memory, shared_buffers auto-adjusted

Metrics to Query:
| Metric | Why | Threshold |
|--------|-----|-----------|
| `BurstBalance` | gp2 burst credit status | <20% warning, <5% critical |
| `EBSIOBalance%` | EBS I/O credit status | <20% warning |
| `EBSByteBalance%` | EBS throughput credit status | <20% warning |
| `FreeableMemory` | Memory pressure | <256MB warning |
| `SwapUsage` | Swap indicates memory pressure | >0 = memory pressure |
| `CPUUtilization` | Overall load | >80% warning |
| `ReadIOPS` | I/O driving burst consumption | Correlate with burst decline |
| `WriteIOPS` | I/O driving burst consumption | Correlate with burst decline |
| `FreeStorageSpace` | Approaching storage limit | <10% warning |

Analysis Focus:
- For burst balance: calculate I/O rate vs baseline IOPS to predict credit recovery
- For memory: check if workload changed or instance needs upsizing
- For storage: check autoscaling config and consumption rate

## AWS CLI Commands Reference

All commands below use China region. Replace `<region>` with `cn-northwest-1` or `cn-north-1`.

### Detect Partition and Region
```bash
aws sts get-caller-identity
# Confirm ARN contains "aws-cn" partition
```

### Describe Instance
```bash
aws rds describe-db-instances --db-instance-identifier <instance-id> --region <region>
```

### Fetch Recent Events
```bash
aws rds describe-events \
  --source-identifier <instance-id> \
  --source-type db-instance \
  --duration 1440 \
  --region <region>
```

### Fetch CloudWatch Metric — Dual Time Window

Reference time point `T` = user-specified timestamp or current time (`date -u +%Y-%m-%dT%H:%M:%S`).

**Past 1 hour (trend context, 5-min period):**
```bash
aws cloudwatch get-metric-statistics \
  --namespace AWS/RDS \
  --metric-name <MetricName> \
  --dimensions Name=DBInstanceIdentifier,Value=<instance-id> \
  --start-time <T-60min> \
  --end-time <T> \
  --period 300 \
  --statistics Average Maximum Minimum \
  --region <region>
```

**Past 10 minutes (real-time snapshot, 1-min period):**
```bash
aws cloudwatch get-metric-statistics \
  --namespace AWS/RDS \
  --metric-name <MetricName> \
  --dimensions Name=DBInstanceIdentifier,Value=<instance-id> \
  --start-time <T-10min> \
  --end-time <T> \
  --period 60 \
  --statistics Average Maximum Minimum \
  --region <region>
```

## Report Format

```
═══════════════════════════════════════════════
  RDS Event Health Report
═══════════════════════════════════════════════

Instance:     <instance-id>
Engine:       <engine> <version>
Class:        <instance-class>
Multi-AZ:     <yes/no>
Status:       <status>
AZ:           <availability-zone>

─── Detected Event ───────────────────────────
Event:        <RDS-EVENT-XXXX>
Category:     <category>
Time:         <timestamp>
Message:      <message>

─── Health Metrics ────────────────────────────
Reference Time (T): <timestamp or "now">

  CPU Utilization:
    Past 1h:   Avg: XX.X%    Max: XX.X%
    Past 10m:  Avg: XX.X%    Max: XX.X%    Status: ✓/⚠/🔴

  Freeable Memory:
    Past 1h:   Avg: XXX MB   Min: XXX MB
    Past 10m:  Avg: XXX MB   Min: XXX MB   Status: ✓/⚠/🔴

  Database Connections:
    Past 1h:   Avg: XXX      Max: XXX
    Past 10m:  Avg: XXX      Max: XXX      Status: ✓/⚠/🔴

  Read Latency:
    Past 1h:   Avg: X.Xms    Max: X.Xms
    Past 10m:  Avg: X.Xms    Max: X.Xms    Status: ✓/⚠/🔴

  Write Latency:
    Past 1h:   Avg: X.Xms    Max: X.Xms
    Past 10m:  Avg: X.Xms    Max: X.Xms    Status: ✓/⚠/🔴

  Free Storage Space:
    Past 1h:   Avg: XX.X GB  Min: XX.X GB
    Past 10m:  Avg: XX.X GB  Min: XX.X GB  Status: ✓/⚠/🔴

  Disk Queue Depth:
    Past 1h:   Avg: X.X      Max: XX
    Past 10m:  Avg: X.X      Max: XX       Status: ✓/⚠/🔴

  [Additional scenario-specific metrics...]

  Note: Status is based on Past 10m values.
        Compare Past 1h vs Past 10m to identify trends (improving ↑ / degrading ↓ / stable →).

─── Diagnosis ────────────────────────────────
<Summary of findings based on event + metrics>

─── Recommendations ──────────────────────────
1. <Actionable recommendation>
2. <Actionable recommendation>
...

─── Alert Levels ─────────────────────────────
🟢 Normal    ⚠️ Warning    🔴 Critical
═══════════════════════════════════════════════
```

## Example Usage

```
"Check RDS health after failover event on my-db-instance in cn-northwest-1"
"RDS instance my-prod-db triggered RDS-EVENT-0007, diagnose the issue"
"My RDS instance just completed maintenance, verify it's healthy"
"Check replica lag issues on my-read-replica in cn-north-1"
"RDS-EVENT-0013 occurred on my-database, what happened and is it OK now?"
"我的 RDS 实例发生了故障转移，帮我检查一下运行状况"
"检查 RDS 实例 my-db 的低存储事件"
"宁夏区域的 RDS 实例 prod-db 刚做完维护，确认健康状态"
```
