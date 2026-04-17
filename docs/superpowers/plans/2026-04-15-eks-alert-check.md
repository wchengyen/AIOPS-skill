# EKS Alert Check Skill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create a new `eks-alert-check` skill that reports EC2-level health and CloudWatch metrics for EKS worker nodes in AWS China regions, with both node-level and cluster-level report modes.

**Architecture:** A single Python script `eks_alert_check.py` inside an `eks-alert-check/` directory, following the pattern of `ec2-cpu-monitor`. The script uses pure AWS APIs (EKS, Auto Scaling, EC2, CloudWatch) to map managed node groups to EC2 instances, fetch status checks and metrics, and generate PNG charts using matplotlib.

**Tech Stack:** Python 3, boto3, matplotlib

---

## File Structure

| File | Responsibility |
|------|----------------|
| `eks-alert-check/SKILL.md` | Skill metadata, triggers, usage instructions, and workflow documentation |
| `eks-alert-check/eks_alert_check.py` | Main implementation: AWS API calls, metric queries, chart generation, CLI/interactive flow |
| `README.md` | Add entry for the new skill alongside existing ones |

---

### Task 1: Create skill directory and SKILL.md

**Files:**
- Create: `eks-alert-check/SKILL.md`

- [ ] **Step 1: Write SKILL.md**

```markdown
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
```

- [ ] **Step 2: Commit**

```bash
git add eks-alert-check/SKILL.md
git commit -m "docs: add eks-alert-check SKILL.md"
```

---

### Task 2: Implement AWS API helpers

**Files:**
- Create: `eks-alert-check/eks_alert_check.py`

- [ ] **Step 1: Write the script skeleton and AWS helpers**

```python
#!/usr/bin/env python3
"""EKS Alert Diagnostic — Report EC2-level health and metrics for EKS worker nodes."""

import argparse
import os
import sys
from datetime import datetime, timedelta

import boto3
from botocore.exceptions import ClientError

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

REGIONS = ['cn-north-1', 'cn-northwest-1']


def _session(profile=None):
    return boto3.Session(profile_name=profile) if profile else boto3.Session()


def get_cluster_info(cluster_name, profile=None):
    """Validate cluster exists in either China region. Returns (region, cluster_info) or None."""
    session = _session(profile)
    for region in REGIONS:
        try:
            eks = session.client('eks', region_name=region)
            resp = eks.describe_cluster(name=cluster_name)
            return region, resp['cluster']
        except ClientError as e:
            if 'ResourceNotFoundException' in str(e):
                continue
            raise
    return None, None


def get_nodegroup_instances(cluster_name, region, profile=None):
    """Map managed node groups to EC2 instances. Returns list of dicts."""
    session = _session(profile)
    eks = session.client('eks', region_name=region)
    autoscaling = session.client('autoscaling', region_name=region)
    ec2 = session.client('ec2', region_name=region)

    nodegroups_resp = eks.list_nodegroups(clusterName=cluster_name)
    nodegroups = nodegroups_resp.get('nodegroups', [])

    instances = []
    asg_instance_ids = []
    asg_to_nodegroup = {}

    for ng_name in nodegroups:
        ng = eks.describe_nodegroup(clusterName=cluster_name, nodegroupName=ng_name)['nodegroup']
        resources = ng.get('resources', {})
        asgs = resources.get('autoScalingGroups', [])
        for asg in asgs:
            asg_name = asg['name']
            asg_to_nodegroup[asg_name] = ng_name

    if not asg_to_nodegroup:
        return instances

    paginator = autoscaling.get_paginator('describe_auto_scaling_instances')
    for page in paginator.paginate():
        for inst in page['AutoScalingInstances']:
            if inst['AutoScalingGroupName'] in asg_to_nodegroup:
                asg_instance_ids.append({
                    'InstanceId': inst['InstanceId'],
                    'AutoScalingGroupName': inst['AutoScalingGroupName'],
                    'LifecycleState': inst['LifecycleState'],
                    'NodegroupName': asg_to_nodegroup[inst['AutoScalingGroupName']],
                })

    if not asg_instance_ids:
        return instances

    ids = [i['InstanceId'] for i in asg_instance_ids]
    resp = ec2.describe_instances(InstanceIds=ids)
    id_to_details = {}
    for reservation in resp['Reservations']:
        for inst in reservation['Instances']:
            name = ''
            for tag in inst.get('Tags', []):
                if tag['Key'] == 'Name':
                    name = tag['Value']
                    break
            id_to_details[inst['InstanceId']] = {
                'InstanceType': inst['InstanceType'],
                'AvailabilityZone': inst['Placement']['AvailabilityZone'],
                'Name': name,
            }

    for mapping in asg_instance_ids:
        iid = mapping['InstanceId']
        details = id_to_details.get(iid, {})
        instances.append({
            'InstanceId': iid,
            'NodegroupName': mapping['NodegroupName'],
            'AutoScalingGroupName': mapping['AutoScalingGroupName'],
            'LifecycleState': mapping['LifecycleState'],
            'InstanceType': details.get('InstanceType', 'unknown'),
            'AvailabilityZone': details.get('AvailabilityZone', 'unknown'),
            'Name': details.get('Name', ''),
        })

    return instances


def get_instance_health(instance_id, region, profile=None):
    """Fetch EC2 instance status checks. Returns dict."""
    session = _session(profile)
    ec2 = session.client('ec2', region_name=region)
    try:
        resp = ec2.describe_instance_status(InstanceIds=[instance_id], IncludeAllInstances=True)
        statuses = resp.get('InstanceStatuses', [])
        if not statuses:
            return {'SystemStatus': 'unknown', 'InstanceStatus': 'unknown'}
        s = statuses[0]
        return {
            'SystemStatus': s.get('SystemStatus', {}).get('Status', 'unknown'),
            'InstanceStatus': s.get('InstanceStatus', {}).get('Status', 'unknown'),
        }
    except ClientError as e:
        return {'SystemStatus': f'error: {e}', 'InstanceStatus': f'error: {e}'}


if __name__ == '__main__':
    pass
```

- [ ] **Step 2: Verify syntax**

Run:
```bash
python3 -m py_compile eks-alert-check/eks_alert_check.py
```
Expected: no output (success).

- [ ] **Step 3: Commit**

```bash
git add eks-alert-check/eks_alert_check.py
git commit -m "feat: add EKS alert check AWS API helpers"
```

---

### Task 3: Implement CloudWatch metrics helper

**Files:**
- Modify: `eks-alert-check/eks_alert_check.py`

- [ ] **Step 1: Append metrics helper to the script**

Add the following before `if __name__ == '__main__':`:

```python

def get_ec2_metrics(instance_id, region, profile=None):
    """Query CloudWatch EC2 metrics for past 1h (5min) and 24h (1h). Returns dict."""
    session = _session(profile)
    cw = session.client('cloudwatch', region_name=region)
    end = datetime.utcnow()

    def _fetch(metric_name, start, period):
        resp = cw.get_metric_statistics(
            Namespace='AWS/EC2',
            MetricName=metric_name,
            Dimensions=[{'Name': 'InstanceId', 'Value': instance_id}],
            StartTime=start,
            EndTime=end,
            Period=period,
            Statistics=['Average', 'Maximum', 'Minimum'],
        )
        return sorted(resp.get('Datapoints', []), key=lambda x: x['Timestamp'])

    metrics = {}
    for name in ['CPUUtilization', 'NetworkIn', 'NetworkOut', 'StatusCheckFailed']:
        metrics[f'{name}_1h'] = _fetch(name, end - timedelta(hours=1), 300)
        metrics[f'{name}_24h'] = _fetch(name, end - timedelta(hours=24), 3600)
    return metrics
```

- [ ] **Step 2: Verify syntax**

Run:
```bash
python3 -m py_compile eks-alert-check/eks_alert_check.py
```
Expected: no output.

- [ ] **Step 3: Commit**

```bash
git add eks-alert-check/eks_alert_check.py
git commit -m "feat: add CloudWatch metrics helper for EKS alert check"
```

---

### Task 4: Implement chart plotting helpers

**Files:**
- Modify: `eks-alert-check/eks_alert_check.py`

- [ ] **Step 1: Append plotting helpers**

Add before `if __name__ == '__main__':`:

```python

def _safe_avg(points, key='Average'):
    if not points:
        return None
    return sum(p[key] for p in points) / len(points)


def _safe_max(points, key='Maximum'):
    if not points:
        return None
    return max(p[key] for p in points)


def _safe_min(points, key='Minimum'):
    if not points:
        return None
    return min(p[key] for p in points)


def plot_node_metrics(instance, metrics, output_dir):
    """Generate per-node CPU + network trend chart. Returns output path or None."""
    cpu_points = metrics.get('CPUUtilization_24h', [])
    if not cpu_points:
        return None

    timestamps = [p['Timestamp'] for p in cpu_points]
    cpu_avgs = [p['Average'] for p in cpu_points]

    net_in = metrics.get('NetworkIn_24h', [])
    net_out = metrics.get('NetworkOut_24h', [])
    net_in_avgs = [p['Average'] / 1024 / 1024 for p in net_in] if net_in else []
    net_out_avgs = [p['Average'] / 1024 / 1024 for p in net_out] if net_out else []

    label = instance['Name'] or instance['InstanceId']
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 8), sharex=True)

    ax1.plot(timestamps, cpu_avgs, linewidth=1.5, color='#2196F3')
    ax1.set_ylabel('CPU %')
    ax1.set_ylim(0, max(105, max(cpu_avgs) + 5))
    ax1.axhline(y=80, color='red', linestyle=':', linewidth=0.8, alpha=0.5)
    ax1.set_title(f'Node Health — {label} ({instance["InstanceId"]})\n'
                  f'Type: {instance["InstanceType"]}  AZ: {instance["AvailabilityZone"]}', fontsize=11)
    ax1.grid(True, alpha=0.3)

    if net_in_avgs and net_out_avgs:
        ax2.plot(timestamps, net_in_avgs, linewidth=1.5, label='NetworkIn', color='#4CAF50')
        ax2.plot(timestamps, net_out_avgs, linewidth=1.5, label='NetworkOut', color='#FF9800')
        ax2.legend(loc='upper right', fontsize=8)
    ax2.set_ylabel('Network (MiB/s)')
    ax2.grid(True, alpha=0.3)

    plt.xticks(rotation=30, fontsize=8)
    plt.tight_layout()
    fname = os.path.join(output_dir, f'{instance["InstanceId"]}_health.png')
    plt.savefig(fname, dpi=150)
    plt.close()
    return fname


def plot_cluster_summary(nodes_data, cluster_name, output_dir):
    """Generate cluster-wide summary chart. Returns output path or None."""
    if not nodes_data:
        return None

    labels = [n['Name'] or n['InstanceId'] for n in nodes_data]
    cpu_avgs = [n.get('CPUAvg_1h', 0) or 0 for n in nodes_data]
    health_ok = [1 if n.get('SystemStatus') == 'ok' and n.get('InstanceStatus') == 'ok' else 0 for n in nodes_data]

    x = range(len(labels))
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 8))

    colors = ['#4CAF50' if v < 50 else '#FFC107' if v < 80 else '#F44336' for v in cpu_avgs]
    ax1.bar(x, cpu_avgs, color=colors)
    ax1.set_xticks(x)
    ax1.set_xticklabels(labels, rotation=45, ha='right', fontsize=8)
    ax1.set_ylabel('CPU Avg % (1h)')
    ax1.axhline(y=80, color='red', linestyle=':', linewidth=0.8, alpha=0.5)
    ax1.set_title(f'Cluster Summary — {cluster_name}', fontsize=11)
    ax1.grid(True, alpha=0.3, axis='y')

    health_colors = ['#4CAF50' if v == 1 else '#F44336' for v in health_ok]
    ax2.bar(x, health_ok, color=health_colors)
    ax2.set_xticks(x)
    ax2.set_xticklabels(labels, rotation=45, ha='right', fontsize=8)
    ax2.set_ylabel('Health OK')
    ax2.set_ylim(0, 1.2)
    ax2.set_title('EC2 Status Checks (System + Instance)', fontsize=11)

    plt.tight_layout()
    fname = os.path.join(output_dir, f'{cluster_name}_summary.png')
    plt.savefig(fname, dpi=150)
    plt.close()
    return fname
```

- [ ] **Step 2: Verify syntax**

Run:
```bash
python3 -m py_compile eks-alert-check/eks_alert_check.py
```
Expected: no output.

- [ ] **Step 3: Commit**

```bash
git add eks-alert-check/eks_alert_check.py
git commit -m "feat: add chart plotting for EKS alert check"
```

---

### Task 5: Implement main interactive flow

**Files:**
- Modify: `eks-alert-check/eks_alert_check.py`

- [ ] **Step 1: Replace `if __name__ == '__main__': pass` with full main()**

```python

def _prompt(question):
    return input(question).strip()


def main():
    parser = argparse.ArgumentParser(description='EKS Alert Diagnostic')
    parser.add_argument('--profile', help='AWS profile name')
    parser.add_argument('--output', default='eks_reports', help='Output directory (default: eks_reports)')
    args = parser.parse_args()

    os.makedirs(args.output, exist_ok=True)

    cluster_name = _prompt('EKS cluster name: ')
    if not cluster_name:
        print('Cluster name is required.')
        sys.exit(1)

    print(f'Looking up cluster "{cluster_name}" in {", ".join(REGIONS)} ...')
    region, cluster_info = get_cluster_info(cluster_name, args.profile)
    if not cluster_info:
        print(f'Cluster "{cluster_name}" not found in any region.')
        sys.exit(1)

    print(f'Found cluster in {region}. Status: {cluster_info.get("status", "unknown")}')

    mode = _prompt('Report mode (node/cluster): ').lower()
    while mode not in ('node', 'cluster'):
        mode = _prompt('Please enter "node" or "cluster": ').lower()

    if mode == 'node':
        instance_id = _prompt('EC2 Instance ID (e.g., i-xxxxxxxxxxxxxxxxx): ').strip()
        if not instance_id:
            print('Instance ID is required for node-level report.')
            sys.exit(1)

        print(f'Fetching health and metrics for {instance_id} ...')
        health = get_instance_health(instance_id, region, args.profile)
        metrics = get_ec2_metrics(instance_id, region, args.profile)

        ec2 = _session(args.profile).client('ec2', region_name=region)
        try:
            inst_resp = ec2.describe_instances(InstanceIds=[instance_id])
            inst = inst_resp['Reservations'][0]['Instances'][0]
            name = ''
            for tag in inst.get('Tags', []):
                if tag['Key'] == 'Name':
                    name = tag['Value']
                    break
            instance_info = {
                'InstanceId': instance_id,
                'Name': name,
                'InstanceType': inst['InstanceType'],
                'AvailabilityZone': inst['Placement']['AvailabilityZone'],
            }
        except ClientError as e:
            print(f'Warning: could not describe instance: {e}')
            instance_info = {
                'InstanceId': instance_id,
                'Name': '',
                'InstanceType': 'unknown',
                'AvailabilityZone': 'unknown',
            }

        chart = plot_node_metrics(instance_info, metrics, args.output)

        cpu_avg = _safe_avg(metrics.get('CPUUtilization_1h', []))
        status_flag = '🟢'
        if cpu_avg is not None:
            if cpu_avg > 80:
                status_flag = '🔴'
            elif cpu_avg > 50:
                status_flag = '🟡'
        if health.get('SystemStatus') != 'ok' or health.get('InstanceStatus') != 'ok':
            status_flag = '🔴'

        print(f'\n{"="*80}')
        print(f'  EKS Node Report')
        print(f'{"="*80}')
        print(f'  Instance:      {instance_id}')
        print(f'  Name:          {instance_info["Name"] or "-"}')
        print(f'  Type:          {instance_info["InstanceType"]}')
        print(f'  AZ:            {instance_info["AvailabilityZone"]}')
        print(f'  System Status: {health["SystemStatus"]}')
        print(f'  Instance Status: {health["InstanceStatus"]}')
        print(f'  CPU Avg (1h):  {cpu_avg:.1f}%  {status_flag}' if cpu_avg is not None else '  CPU Avg (1h):  no data')
        if chart:
            print(f'  Chart:         {chart}')
        print(f'{"="*80}')
        return

    # cluster mode
    print('Discovering managed node groups ...')
    instances = get_nodegroup_instances(cluster_name, region, args.profile)
    if not instances:
        print('No managed node group instances found.')
        sys.exit(0)

    print(f'Found {len(instances)} instance(s)\n')
    results = []
    for inst in instances:
        iid = inst['InstanceId']
        label = inst['Name'] or iid
        print(f'  [{inst["NodegroupName"]}] {label} ({inst["InstanceType"]}) ... ', end='', flush=True)
        health = get_instance_health(iid, region, args.profile)
        metrics = get_ec2_metrics(iid, region, args.profile)
        chart = plot_node_metrics(inst, metrics, args.output)

        cpu_avg = _safe_avg(metrics.get('CPUUtilization_1h', []))
        net_in_avg = _safe_avg(metrics.get('NetworkIn_1h', []))
        status_flag = '🟢'
        if cpu_avg is not None:
            if cpu_avg > 80:
                status_flag = '🔴'
            elif cpu_avg > 50:
                status_flag = '🟡'
        if health.get('SystemStatus') != 'ok' or health.get('InstanceStatus') != 'ok':
            status_flag = '🔴'

        inst['SystemStatus'] = health['SystemStatus']
        inst['InstanceStatus'] = health['InstanceStatus']
        inst['CPUAvg_1h'] = cpu_avg
        inst['NetworkInAvg_1h'] = net_in_avg
        inst['Chart'] = chart
        inst['Flag'] = status_flag
        results.append(inst)
        print(f'CPU={cpu_avg:.1f}%  Health={health["SystemStatus"]}/{health["InstanceStatus"]}  {status_flag}' if cpu_avg is not None else 'no data')

    summary_chart = plot_cluster_summary(results, cluster_name, args.output)

    print(f'\n{"="*110}')
    print(f'  EKS Cluster Report — {cluster_name}  ({region})')
    print(f'{"="*110}')
    print(f'  {"Instance ID":<22} {"Nodegroup":<18} {"Type":<12} {"AZ":<14} {"Sys":<8} {"Inst":<8} {"CPU%":>8} {"Flag":>6}')
    print(f'  {"-"*110}')
    for r in results:
        name = (r['Name'] or '-')[:16]
        cpu_str = f'{r["CPUAvg_1h"]:.1f}' if r.get('CPUAvg_1h') is not None else '-'
        print(f'  {r["InstanceId"]:<22} {r["NodegroupName"]:<18} {r["InstanceType"]:<12} {r["AvailabilityZone"]:<14} '
              f'{r["SystemStatus"]:<8} {r["InstanceStatus"]:<8} {cpu_str:>8} {r["Flag"]:>6}')
    print(f'{"="*110}')
    if summary_chart:
        print(f'\nSummary chart: {summary_chart}')
    print(f'Charts saved to: {os.path.abspath(args.output)}/')


if __name__ == '__main__':
    main()
```

- [ ] **Step 2: Make script executable**

Run:
```bash
chmod +x eks-alert-check/eks_alert_check.py
```

- [ ] **Step 3: Verify syntax**

Run:
```bash
python3 -m py_compile eks-alert-check/eks_alert_check.py
```
Expected: no output.

- [ ] **Step 4: Commit**

```bash
git add eks-alert-check/eks_alert_check.py
git commit -m "feat: add EKS alert check main interactive flow"
```

---

### Task 6: Update README.md

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add EKS alert check entry between RDS-event-check and ec2-cpu-monitor**

Insert after the RDS-event-check section and before the ec2-cpu-monitor section:

```markdown
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

```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: add eks-alert-check to README"
```

---

### Task 7: Final verification

**Files:**
- None (read-only verification)

- [ ] **Step 1: Run syntax check on the complete script**

```bash
python3 -m py_compile eks-alert-check/eks_alert_check.py
```
Expected: no output.

- [ ] **Step 2: Verify the directory structure matches existing skills**

```bash
ls -la eks-alert-check/
```
Expected:
```
SKILL.md
eks_alert_check.py
```

- [ ] **Step 3: Review git diff to ensure no unintended changes**

```bash
git diff --stat
```
Expected changes only in:
- `eks-alert-check/SKILL.md`
- `eks-alert-check/eks_alert_check.py`
- `README.md`

- [ ] **Step 4: Commit any remaining changes (if any)**

If there are uncommitted changes, commit them with an appropriate message.

---

## Self-Review Checklist

| Spec Section | Implemented By |
|--------------|----------------|
| Node-level diagnostics | Task 5 (node mode in `main()`) |
| Cluster-level diagnostics | Task 5 (cluster mode in `main()`) |
| EC2 CloudWatch metrics (CPU, NetworkIn, NetworkOut, StatusCheckFailed) | Task 3 (`get_ec2_metrics`) |
| EC2 status checks | Task 2 (`get_instance_health`) |
| ASG lifecycle mapping | Task 2 (`get_nodegroup_instances`) |
| Text summary + charts | Tasks 4 and 5 |
| AWS China regions | Task 2 (`REGIONS` constant) |
| No `kubectl` | Entire plan uses only boto3 AWS APIs |
| Error handling (cluster not found, no node groups, no CW data) | Task 5 (`main()`) |
| SKILL.md documentation | Task 1 |
| README update | Task 6 |

No placeholders detected. All functions, types, and file names are consistent.
