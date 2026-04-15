# EKS Alert Diagnostic Skill — Design Document

**Date:** 2026-04-15  
**Skill Name:** `eks-alert-check`  
**Target Repository:** AIOPS-skill

---

## 1. Overview

A new AWS EKS diagnostic skill that responds to EKS alert triggers by reporting:
- EC2-level health of underlying EKS worker nodes
- Key EC2 CloudWatch metrics for the affected node(s)
- A cluster-wide summary when a cluster-level alert is received

The skill targets the AWS China partition (`aws-cn`) and follows the same structure as the existing `ec2-cpu-monitor` and `RDS-event-check` skills.

---

## 2. Scope

### In Scope
- Node-level alert diagnostics (single node deep-dive)
- Cluster-level alert diagnostics (all managed node group instances overview)
- EC2 CloudWatch metrics: `CPUUtilization`, `NetworkIn`, `NetworkOut`, `StatusCheckFailed`
- EC2 status checks (SystemStatusCheck + InstanceStatusCheck)
- Auto Scaling Group lifecycle state mapping via EKS managed node groups
- Text summary + generated charts saved to disk
- AWS China regions: `cn-north-1`, `cn-northwest-1`

### Out of Scope
- Pod-level diagnostics (e.g., CrashLoopBackOff, OOMKilled)
- Kubernetes API access via `kubectl` (pure AWS APIs only)
- Control plane metrics (e.g., API server latency)
- Fargate profiles (managed node groups only)
- Automated remediation actions

---

## 3. User Flow

1. User triggers the skill with a keyword (e.g., `eks alert`).
2. The skill asks: **"Which EKS cluster?"**
3. The skill asks: **"Node-level or cluster-level report?"**
4. If **node-level**: asks for the target EC2 instance ID, then queries EC2 health and metrics for that instance.
5. If **cluster-level**: discovers all managed node groups and their EC2 instances, then queries EC2 health and metrics for every instance.
6. The skill prints a summary table and saves charts to the output directory.

---

## 4. Architecture

A single Python script `eks-alert-check/eks_alert_check.py` with the following components:

| Function | Purpose |
|----------|---------|
| `get_cluster_info(cluster_name, region, profile)` | Validates the cluster via `eks describe-cluster` and returns cluster metadata. |
| `get_nodegroup_instances(cluster_name, region, profile)` | Maps managed node groups to EC2 instances by calling `eks list-nodegroups`, `eks describe-nodegroup`, `autoscaling describe-auto-scaling-instances`, and `ec2 describe-instances`. |
| `get_instance_health(instance_id, region, profile)` | Fetches EC2 `describe-instance-status` for system and instance status checks. |
| `get_ec2_metrics(instance_id, region, profile)` | Queries CloudWatch `AWS/EC2` metrics with two time windows: past 1 hour (5-min period) and past 24 hours (1-hour period). |
| `plot_node_metrics(instance, datapoints, output_dir)` | Generates a per-node PNG chart showing CPU utilization and network throughput trends. |
| `plot_cluster_summary(nodes_data, output_dir)` | Generates a cluster-wide summary PNG chart comparing CPU and health status across nodes. |
| `main()` | Parses arguments, drives the interactive prompt flow, orchestrates API calls, and prints the final report. |

---

## 5. Data Flow

```
User keyword trigger
    ↓
Ask cluster name + report mode
    ↓
EKS API (describe-cluster) → validate cluster exists
    ↓
IF node-level:
    EC2 API (describe-instance-status, describe-instances)
    CloudWatch (CPU, NetworkIn, NetworkOut, StatusCheckFailed)
    ↓
    Plot single-node chart
    Print node summary
IF cluster-level:
    EKS API (list-nodegroups, describe-nodegroup)
    ASG API (describe-auto-scaling-instances)
    EC2 API (describe-instances, describe-instance-status)
    CloudWatch (metrics per instance)
    ↓
    Plot per-node charts + cluster summary chart
    Print cluster summary table
```

---

## 6. Error Handling

| Scenario | Behavior |
|----------|----------|
| Cluster not found | Print a graceful message and suggest checking the other China region. |
| No managed node groups | Report that no managed node groups were found and suggest checking unmanaged nodes or Fargate. |
| No CloudWatch data | Skip the chart for that node and print "no metric data" in the summary table. |
| AWS API error (`ClientError`) | Catch and print a readable error message, then suggest checking AWS profile and permissions. |
| `matplotlib` not installed | Print a text-only fallback summary without charts. |

---

## 7. Output Artifacts

### Console Output
A summary table containing:
- Instance ID
- Node group name
- Instance type
- AZ
- EC2 status checks (System / Instance)
- ASG lifecycle state
- CPU avg (past 1h)
- Network throughput (past 1h)
- Alert flags: 🟢 Normal, 🟡 Warning, 🔴 Critical

### Saved Charts
- `eks_reports/<instance_id>_health.png` — per-node CPU + network trend chart
- `eks_reports/<cluster_name>_summary.png` — cluster-wide comparison chart (CPU bars + health indicators)

---

## 8. Testing Strategy

| Test | Method |
|------|--------|
| Cluster not found | Dry-run with a non-existent cluster name to verify error message. |
| Empty node groups | Validate that the script handles clusters with no managed node groups gracefully. |
| Chart generation | Run the script and verify PNG files are created in the output directory. |
| Region probing | Ensure both `cn-north-1` and `cn-northwest-1` are checked when the user does not specify a region. |
| Text fallback | Temporarily disable `matplotlib` import to verify text-only output still works. |

---

## 9. Prerequisites

- AWS CLI configured with credentials for an AWS China account
- IAM permissions:
  - `eks:DescribeCluster`
  - `eks:ListNodegroups`
  - `eks:DescribeNodegroup`
  - `autoscaling:DescribeAutoScalingInstances`
  - `ec2:DescribeInstances`
  - `ec2:DescribeInstanceStatus`
  - `cloudwatch:GetMetricStatistics`
- Python 3 with `boto3` and `matplotlib`

---

## 10. Triggers

```yaml
triggers:
  - eks alert
  - eks 告警
  - eks node status
  - eks cluster health
  - eks 诊断
```
