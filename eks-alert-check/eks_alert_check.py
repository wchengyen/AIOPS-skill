#!/usr/bin/env python3
"""EKS Alert Diagnostic — Report EC2-level health and metrics for EKS worker nodes."""

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone

import boto3
from botocore.exceptions import ClientError

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

REGIONS = ['cn-north-1', 'cn-northwest-1']

PERIOD_5MIN = 300
PERIOD_1HOUR = 3600


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
            if e.response['Error']['Code'] == 'ResourceNotFoundException':
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

    asg_resp = autoscaling.describe_auto_scaling_groups(AutoScalingGroupNames=list(asg_to_nodegroup.keys()))
    for asg in asg_resp.get('AutoScalingGroups', []):
        asg_name = asg['AutoScalingGroupName']
        for inst in asg.get('Instances', []):
            asg_instance_ids.append({
                'InstanceId': inst['InstanceId'],
                'AutoScalingGroupName': asg_name,
                'LifecycleState': inst['LifecycleState'],
                'NodegroupName': asg_to_nodegroup[asg_name],
            })

    if not asg_instance_ids:
        return instances

    ids = [i['InstanceId'] for i in asg_instance_ids]
    id_to_details = {}
    for chunk in [ids[i:i + 200] for i in range(0, len(ids), 200)]:
        resp = ec2.describe_instances(InstanceIds=chunk)
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
        status = statuses[0]
        return {
            'SystemStatus': status.get('SystemStatus', {}).get('Status', 'unknown'),
            'InstanceStatus': status.get('InstanceStatus', {}).get('Status', 'unknown'),
        }
    except ClientError as e:
        print(f'Warning: instance health error: {e}')
        return {'SystemStatus': f'error: {e}', 'InstanceStatus': f'error: {e}'}


def get_ec2_metrics(instance_id, region, profile=None):
    """Query CloudWatch EC2 metrics for past 1h (5min) and 24h (1h). Returns dict."""
    session = _session(profile)
    cw = session.client('cloudwatch', region_name=region)
    end = datetime.now(timezone.utc)

    def _fetch(metric_name, start, period):
        try:
            resp = cw.get_metric_statistics(
                Namespace='AWS/EC2',
                MetricName=metric_name,
                Dimensions=[{'Name': 'InstanceId', 'Value': instance_id}],
                StartTime=start,
                EndTime=end,
                Period=period,
                Statistics=['Average', 'Maximum', 'Minimum'],
            )
        except ClientError as e:
            print(f'Warning: CloudWatch metric error for {metric_name}: {e}')
            return []
        return sorted(resp.get('Datapoints', []), key=lambda x: x['Timestamp'])

    metrics = {}
    for name in ['CPUUtilization', 'NetworkIn', 'NetworkOut', 'StatusCheckFailed']:
        metrics[f'{name}_1h'] = _fetch(name, end - timedelta(hours=1), PERIOD_5MIN)
        metrics[f'{name}_24h'] = _fetch(name, end - timedelta(hours=24), PERIOD_1HOUR)
    return metrics


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


if __name__ == '__main__':
    pass
