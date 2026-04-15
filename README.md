# AIOPS-skill

AIOps skills collection for automated AWS infrastructure diagnostics and monitoring.

## Skills

### RDS-event-check

Inspect RDS health metrics based on RDS event types. Detects event category, queries relevant CloudWatch metrics, and generates a diagnostic report.

**Triggers:** `RDS事件确认`, `rds event check`, `rds failover`, `rds recovery`, `rds maintenance`, `rds low storage`, `rds failure`, `rds availability`, `rds replica lag`

See [SKILL.md](SKILL.md) for full documentation.

### eks-alert-check

Respond to EKS alerts by reporting EC2-level node health and CloudWatch metrics for underlying worker nodes in AWS China regions.

**Triggers:** `eks alert`, `eks 告警`, `eks node status`, `eks cluster health`, `eks 诊断`

**Modes:**
- Node-level — deep-dive on a single EC2 instance backing an EKS node
- Cluster-level — overview of all managed node group instances with CPU and health summary

**Output:**
- Text summary with EC2 status checks and CPU/network metrics
- Per-node health trend charts (PNG)
- Cluster-wide summary chart (PNG) for cluster-level reports

**Prerequisites:** AWS CLI with EKS, EC2, Auto Scaling, and CloudWatch read access; Python 3 with boto3 and matplotlib

See [eks-alert-check/SKILL.md](eks-alert-check/SKILL.md) for full documentation.

### ec2-cpu-monitor

Scan all running EC2 instances across cn-north-1 and cn-northwest-1, collect past 7 days of CPUUtilization metrics (hourly), generate trend charts (PNG), and report max/min/avg statistics.

**Triggers:** `ec2 cpu utilization`, `ec2 cpu监控`, `ec2 cpu check`, `ec2 性能检查`, `ec2 cpu趋势`

**Output:**
- Per-instance CPU trend chart with min-max range shading
- Cross-instance comparison chart
- Summary table with CPU Max/Min/Avg
- Alerts: 🔴 Avg > 80%, 🟡 Avg < 5% (over-provisioned), 🟢 Normal

**Prerequisites:** AWS CLI with EC2 + CloudWatch read access, Python 3 with boto3 and matplotlib

See [ec2-cpu-monitor/SKILL.md](ec2-cpu-monitor/SKILL.md) for full documentation.
