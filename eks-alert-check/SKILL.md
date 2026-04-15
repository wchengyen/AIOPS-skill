---
name: eks-alert-check
description: Respond to EKS alerts by reporting EC2-level node health and CloudWatch metrics for underlying worker nodes in AWS China regions.
version: 1.0.0
author: AI Assistant
triggers:
  - eks alert
  - eks 告警
  - eks node status
  - eks cluster health
  - eks 诊断
---

# EKS Alert Diagnostic

Respond to EKS alerts by reporting EC2-level health and CloudWatch metrics for underlying worker nodes.

## When to Use

- An EKS node-level alert fired and you need to inspect the underlying EC2 instance health
- A cluster-level alert fired and you want a quick overview of all worker nodes
- You need EC2 CPU, network throughput, and status check data for EKS nodes

## Usage

```bash
# Run with default AWS profile
python3 eks_alert_check.py

# Use a specific AWS profile
python3 eks_alert_check.py --profile myprofile

# Output charts to a specific directory
python3 eks_alert_check.py --output /tmp/eks_reports
```

## Output

- Node-level or cluster-level text summary with EC2 status checks and metric stats
- Per-node health trend charts (PNG)
- Cluster-wide summary comparison chart (PNG) for cluster-level reports

## Prerequisites

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

## Workflow

1. Trigger the skill with a keyword.
2. Provide the EKS cluster name.
3. Choose report mode: `node` or `cluster`.
4. If node-level, provide the target EC2 instance ID.
5. The skill queries EKS node groups, EC2 instance status, and CloudWatch metrics.
6. Summary and charts are printed/saved.
