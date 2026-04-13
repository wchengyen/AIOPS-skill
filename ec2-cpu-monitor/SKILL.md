---
name: ec2-cpu-monitor
description: Scan EC2 instances in cn-north-1 and cn-northwest-1, collect past week CPUUtilization metrics (hourly), plot trend charts, and report max/min/avg values.
version: 1.0.0
author: AI Assistant
triggers:
  - ec2 cpu utilization
  - ec2 cpu监控
  - ec2 cpu check
  - ec2 性能检查
  - ec2 cpu趋势
---

# EC2 CPU Utilization Monitor

Scan all running EC2 instances across cn-north-1 and cn-northwest-1, query the past 7 days of CloudWatch CPUUtilization (hourly granularity), generate trend charts, and provide max/min/avg statistics.

## When to Use

- Weekly review of EC2 CPU usage across China regions
- Identify over-utilized or under-utilized instances
- Generate CPU trend reports for capacity planning

## Usage

```bash
# Scan both regions with default profile
python3 ec2_cpu_monitor.py

# Use a specific AWS profile
python3 ec2_cpu_monitor.py --profile myprofile

# Output charts to a specific directory
python3 ec2_cpu_monitor.py --output /tmp/cpu_reports
```

## Output

- Per-instance CPU trend chart (PNG) with hourly data points over 7 days
- Summary table with Instance ID, Type, Region, CPU Max/Min/Avg
- Alerts for instances with Avg CPU > 80% or < 5%

## Prerequisites

- AWS CLI configured with credentials that have EC2 and CloudWatch read access
- Python 3 with boto3 and matplotlib
